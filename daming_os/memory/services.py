"""Portable maintenance services from the Memory System 3.0 blueprint."""
from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Protocol


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryReviewService:
    """Deterministic daily/weekly reviews that never require a chat platform."""
    def __init__(self, workspace: str):
        self.root = Path(workspace)

    def review(self, period: str, events: Iterable[Dict[str, Any]]) -> Path:
        if period not in {"daily", "weekly"}:
            raise ValueError("period must be daily or weekly")
        items = list(events)
        types: Dict[str, int] = {}
        for event in items:
            name = str(event.get("log_type", event.get("event_type", "unknown")))
            types[name] = types.get(name, 0) + 1
        output_dir = self.root / "memory" / ("diary" if period == "daily" else "reviews")
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = output_dir / f"{period}-{stamp}.json"
        path.write_text(json.dumps({"period": period, "generated_at": _utc_now(),
                                    "event_count": len(items), "by_type": types,
                                    "recent": items[-20:]}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return path


class GlacierStore:
    """Compressed, manifest-backed cold archive with explicit restore."""
    def __init__(self, workspace: str):
        self.root = Path(workspace)
        self.directory = self.root / "memory" / "glacier"

    def archive(self, sources: Iterable[str], label: str = "memory") -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        archive = self.directory / f"{label}-{stamp}.tar.gz"
        manifest: List[Dict[str, str]] = []
        with tarfile.open(archive, "w:gz") as bundle:
            for raw in sources:
                path = Path(raw)
                if not path.exists():
                    continue
                try:
                    arcname = str(path.resolve().relative_to(self.root.resolve()))
                except ValueError:
                    arcname = path.name
                bundle.add(path, arcname=arcname)
                manifest.append({"source": str(path), "archive_name": arcname})
        archive.with_suffix(".manifest.json").write_text(
            json.dumps({"created_at": _utc_now(), "files": manifest}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return archive

    def restore(self, archive: str, destination: str) -> List[Path]:
        target = Path(destination)
        target.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r:gz") as bundle:
            members = [member for member in bundle.getmembers()
                       if (target / member.name).resolve().is_relative_to(target.resolve())]
            # Paths were checked above, so extraction remains compatible with
            # Python 3.9-3.11 where tarfile's ``filter`` argument is absent.
            for member in members:
                bundle.extract(member, target)
        return [target / member.name for member in members]


class WikiSyncProvider(Protocol):
    def push(self, documents: Dict[str, str]) -> None: ...
    def pull(self) -> Dict[str, str]: ...


class FilesystemWikiProvider:
    """Default provider for local/other-agent Wiki synchronization."""
    def __init__(self, directory: str):
        self.directory = Path(directory)

    def push(self, documents: Dict[str, str]) -> None:
        for relative, content in documents.items():
            target = self.directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    def pull(self) -> Dict[str, str]:
        if not self.directory.exists():
            return {}
        return {str(path.relative_to(self.directory)): path.read_text(encoding="utf-8")
                for path in self.directory.rglob("*.md")}


class WikiSynchronizer:
    """Bidirectional local Wiki sync; external backends implement the Provider."""
    def __init__(self, local_directory: str, provider: WikiSyncProvider):
        self.local = Path(local_directory)
        self.provider = provider

    def sync(self) -> Dict[str, int]:
        self.local.mkdir(parents=True, exist_ok=True)
        local_docs = {str(path.relative_to(self.local)): path.read_text(encoding="utf-8")
                      for path in self.local.rglob("*.md")}
        self.provider.push(local_docs)
        pulled = self.provider.pull()
        changed = 0
        for relative, content in pulled.items():
            target = self.local / relative
            old = target.read_text(encoding="utf-8") if target.exists() else None
            if old != content:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                changed += 1
        return {"pushed": len(local_docs), "pulled": len(pulled), "changed": changed}


class BitableSyncProvider(Protocol):
    """Portable record provider; Feishu, Airtable or a local file may implement it."""
    def push_records(self, records: Dict[str, Dict[str, Any]]) -> None: ...
    def pull_records(self) -> Dict[str, Dict[str, Any]]: ...


class JsonBitableProvider:
    """Default local Bitable-compatible provider for agents without Feishu."""
    def __init__(self, path: str):
        self.path = Path(path)

    def push_records(self, records: Dict[str, Dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    def pull_records(self) -> Dict[str, Dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (OSError, ValueError, json.JSONDecodeError):
            return {}


class BitableSynchronizer:
    """Synchronize durable memory metadata with any tabular-record provider."""
    def __init__(self, metadata_path: str, provider: BitableSyncProvider, memory_db: Optional[str] = None):
        self.metadata_path = Path(metadata_path)
        self.provider = provider
        self.memory_db = Path(memory_db) if memory_db else None

    def _memory_records(self) -> Dict[str, Dict[str, Any]]:
        """Export warm-memory metadata instead of mirroring an empty file."""
        if self.memory_db is None or not self.memory_db.exists():
            return {}
        import sqlite3
        try:
            with sqlite3.connect(self.memory_db) as connection:
                exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='items'").fetchone()
                if not exists:
                    return {}
                rows = connection.execute("SELECT item_id,title,importance,category,created_at,metadata_json FROM items").fetchall()
            return {row[0]: {"title": row[1], "importance": row[2], "category": row[3],
                             "created_at": row[4], "metadata": json.loads(row[5] or "{}")}
                    for row in rows}
        except (OSError, ValueError, sqlite3.Error):
            return {}

    def sync(self) -> Dict[str, int]:
        local = JsonBitableProvider(str(self.metadata_path))
        remote = self.provider.pull_records()
        records = {**remote, **local.pull_records(), **self._memory_records()}
        self.provider.push_records(records)
        local.push_records(records)
        self._import_remote_items(remote)
        return {"pushed": len(records), "pulled": len(remote)}

    def _import_remote_items(self, records: Dict[str, Dict[str, Any]]) -> None:
        """Make pulled Bitable knowledge searchable, not merely mirrored."""
        if self.memory_db is None or not self.memory_db.exists():
            return
        import sqlite3
        with sqlite3.connect(self.memory_db) as connection:
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='items'").fetchone():
                return
            connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(item_id, content)")
            for identifier, record in records.items():
                content = str(record.get("content") or record.get("title") or "").strip()
                if not content:
                    continue
                exists = connection.execute("SELECT 1 FROM items WHERE item_id=?", (identifier,)).fetchone()
                if not exists:
                    connection.execute("INSERT INTO items(item_id,title,content,importance,category,created_at,metadata_json) VALUES(?,?,?,?,?,?,?)",
                                       (identifier, str(record.get("title", content[:80])), content,
                                        float(record.get("importance", .5)), str(record.get("category", "bitable")),
                                        str(record.get("created_at", _utc_now())),
                                        json.dumps(record.get("metadata", {}), ensure_ascii=False)))
                    connection.execute("INSERT INTO memory_fts(item_id,content) VALUES(?,?)", (identifier, content))


class SkillUsageLedger:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, skill: str, action: str, result: str = "success", **details: Any) -> Dict[str, Any]:
        entry = {"timestamp": _utc_now(), "skill": skill, "action": action,
                 "result": result, "details": details}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry
