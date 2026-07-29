"""Versioned workspace migrations for portable Daming OS installations."""
from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


class MemoryMigrator:
    """Apply ordered, backed-up workspace migrations.

    The migration ledger is deliberately separate from application databases so
    future releases can migrate memory and growth storage in one transactionally
    recorded sequence.
    """

    TARGET_SCHEMA_VERSION = 1

    def __init__(self, workspace: str):
        self.root = Path(workspace)
        self.state_dir = self.root / ".daming-os"
        self.ledger_path = self.state_dir / "schema.db"

    def verify(self) -> Dict[str, bool]:
        memory = self.root / "memory"
        required = (memory, memory / "hot", memory / "lancedb", self.root / "wiki" / "main")
        result = {str(item.relative_to(self.root)): item.exists() for item in required}
        result["schema"] = self.current_version() == self.TARGET_SCHEMA_VERSION
        return result

    def initialize(self) -> Dict[str, bool]:
        self._ensure_directories()
        self.migrate()
        return self.verify()

    def current_version(self) -> int:
        if not self.ledger_path.exists():
            return 0
        try:
            with closing(sqlite3.connect(self.ledger_path)) as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS schema_version "
                    "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                row = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
                return int(row[0] or 0)
        except sqlite3.DatabaseError:
            return 0

    def migrate(self) -> Dict[str, Any]:
        self._ensure_directories()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        current = self.current_version()
        if current > self.TARGET_SCHEMA_VERSION:
            raise ValueError(
                f"workspace schema {current} is newer than supported "
                f"{self.TARGET_SCHEMA_VERSION}"
            )
        backups: List[str] = []
        if current < self.TARGET_SCHEMA_VERSION:
            backups = self._backup_databases(current, self.TARGET_SCHEMA_VERSION)
        applied: List[int] = []
        with closing(sqlite3.connect(self.ledger_path)) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_version "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            for version in range(current + 1, self.TARGET_SCHEMA_VERSION + 1):
                migration = getattr(self, f"_migrate_v{version}", None)
                if migration is None:
                    raise RuntimeError(f"missing workspace migration v{version}")
                migration()
                connection.execute(
                    "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
                    (version, datetime.now(timezone.utc).isoformat()),
                )
                applied.append(version)
            connection.commit()
        return {
            "from_version": current,
            "to_version": self.TARGET_SCHEMA_VERSION,
            "applied": applied,
            "backups": backups,
        }

    def _ensure_directories(self) -> None:
        for item in (
            self.root / "memory" / "hot",
            self.root / "memory" / "lancedb",
            self.root / "wiki" / "main",
            self.root / "memory" / "archive",
            self.root / "memory" / "health-reports",
            self.root / "memory" / "maintenance",
            self.root / "memory" / "digests",
        ):
            item.mkdir(parents=True, exist_ok=True)

    def _backup_databases(self, source_version: int, target_version: int) -> List[str]:
        databases = sorted(path for path in (self.root / "memory").rglob("*.db") if path.is_file())
        if not databases:
            return []
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_root = self.state_dir / "backups" / (
            f"schema-v{source_version}-to-v{target_version}-{stamp}"
        )
        backed_up: List[str] = []
        for source in databases:
            relative = source.relative_to(self.root)
            target = backup_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with closing(sqlite3.connect(source)) as source_db, closing(sqlite3.connect(target)) as target_db:
                    source_db.backup(target_db)
            except sqlite3.DatabaseError:
                shutil.copy2(source, target)
            backed_up.append(str(target))
        return backed_up

    def _migrate_v1(self) -> None:
        """Establish the versioned workspace baseline used by Daming OS 1.5."""
        self._ensure_directories()
