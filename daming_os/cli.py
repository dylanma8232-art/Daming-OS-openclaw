"""Command-line support for a host-neutral Daming OS plugin."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union

from .memory.migration import MemoryMigrator


CONFIG_NAME = "daming-os.json"
INTEGRATIONS = ("generic", "langgraph", "openclaw", "codex")
BOOTSTRAP_NAME = "daming_bootstrap.py"

BOOTSTRAP_TEMPLATE = '''"""Generated Daming OS bridge. Safe to import from any Python agent host."""
import atexit
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from daming_os import DamingRuntime


_HOST_ROOT = Path(__file__).resolve().parent
_STATE_DIR = _HOST_ROOT / __STATE_DIR__
_AGENT_ID = __AGENT_ID__


class DamingPlugin:
    """Lazy, fail-open facade around Daming OS lifecycle hooks."""

    def __init__(self, state_dir: Optional[Path] = None, *, strict: bool = False,
                 agent_id: Optional[str] = None) -> None:
        self.state_dir = Path(state_dir or _STATE_DIR).resolve()
        self.strict = strict
        self.default_agent_id = agent_id or _AGENT_ID
        self.default_session_id = self.new_session_id()
        self._runtime: Optional[DamingRuntime] = None
        self._init_error: Optional[str] = None
        self._lock = threading.RLock()

    @staticmethod
    def new_session_id() -> str:
        return "session-" + uuid.uuid4().hex

    def _get_runtime(self) -> DamingRuntime:
        with self._lock:
            if self._runtime is not None:
                return self._runtime
            if self._init_error is not None:
                raise RuntimeError(self._init_error)
            try:
                self._runtime = DamingRuntime(str(self.state_dir))
                return self._runtime
            except Exception as exc:
                self._init_error = f"{type(exc).__name__}: {exc}"
                raise

    @property
    def runtime(self) -> DamingRuntime:
        """Compatibility access for hosts that need the underlying runtime."""
        return self._get_runtime()

    def retry_initialization(self) -> Dict[str, Any]:
        with self._lock:
            self._init_error = None
        try:
            self._get_runtime()
            return {"ok": True, "state": "ready"}
        except Exception as exc:
            return {"ok": False, "state": "degraded", "error": str(exc)}

    def status(self) -> Dict[str, Any]:
        return {
            "state": "ready" if self._runtime is not None else ("degraded" if self._init_error else "lazy"),
            "workspace": str(self.state_dir),
            "agent_id": self.default_agent_id,
            "default_session_id": self.default_session_id,
            "error": self._init_error,
        }

    def _dispatch(self, hook: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload.setdefault("agent_id", self.default_agent_id)
        payload.setdefault("session_id", self.default_session_id)
        try:
            callback = getattr(self._get_runtime().hooks, hook)
            return callback(payload)
        except Exception as exc:
            if self.strict:
                raise
            self._init_error = self._init_error or f"{type(exc).__name__}: {exc}"
            payload["daming_degraded"] = {"hook": hook, "error": str(exc)}
            if hook == "before_turn":
                payload.setdefault("daming_memories", [])
            return payload

    def before_turn(self, user_input: str, *, agent_id: Optional[str] = None,
                    session_id: Optional[str] = None, tenant_id: Optional[str] = None,
                    messages: Optional[Iterable[Dict[str, Any]]] = None,
                    metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        values = dict(metadata or {})
        if messages is not None:
            values["messages"] = list(messages)
        return self._dispatch("before_turn", {
            "input": user_input, "agent_id": agent_id or self.default_agent_id,
            "session_id": session_id or self.default_session_id,
            "tenant_id": tenant_id, "metadata": values,
        })

    def after_turn(self, user_input: str, output: Any, *, agent_id: Optional[str] = None,
                   session_id: Optional[str] = None, tenant_id: Optional[str] = None,
                   metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._dispatch("after_turn", {
            "input": user_input, "output": output,
            "agent_id": agent_id or self.default_agent_id,
            "session_id": session_id or self.default_session_id, "tenant_id": tenant_id,
            "metadata": dict(metadata or {}),
        })

    def on_error(self, error: Exception, *, agent_id: Optional[str] = None,
                 session_id: Optional[str] = None, tenant_id: Optional[str] = None,
                 metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._dispatch("on_error", {
            "error": error, "agent_id": agent_id or self.default_agent_id,
            "session_id": session_id or self.default_session_id,
            "tenant_id": tenant_id, "metadata": dict(metadata or {}),
        })

    def install_hooks(self, register: Any) -> None:
        register("before_turn", lambda payload: self._dispatch("before_turn", payload))
        register("after_turn", lambda payload: self._dispatch("after_turn", payload))
        register("error", lambda payload: self._dispatch("on_error", payload))

    def start_scheduler(self, poll_seconds: float = 30) -> None:
        self._get_runtime().start_scheduler(poll_seconds)

    def close(self) -> None:
        with self._lock:
            runtime = self._runtime
            self._runtime = None
        if runtime is not None:
            runtime.close()


daming = DamingPlugin()
atexit.register(daming.close)
'''


def _config_path(workspace: Union[str, Path]) -> Path:
    return Path(workspace) / CONFIG_NAME


def _stable_agent_id(workspace: Path) -> str:
    host = workspace.resolve().parent
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", host.name).strip("-").lower() or "agent"
    digest = hashlib.sha256(str(host).encode("utf-8")).hexdigest()[:10]
    return f"{slug}-{digest}"


def _default_config(workspace: Path, integration: str) -> Dict[str, Any]:
    return {
        "version": 1,
        "integration": integration,
        "runtime": {
            "workspace": str(workspace.resolve()),
            "agent_id": _stable_agent_id(workspace),
            "watchdog_enabled": False,
            "daily_digest_enabled": False,
            "timezone": "local",
        },
    }


def init_workspace(target_dir: str, integration: str = "generic") -> Dict[str, Any]:
    """Create only Daming OS-owned state and configuration.

    A host agent owns its own AGENTS.md, USER.md, and secret-management files.
    Known integrations are recorded as configuration, never injected into the
    host project without an explicit adapter installation step.
    """
    root = Path(target_dir).resolve()
    if integration not in INTEGRATIONS:
        raise ValueError(f"unsupported integration: {integration}")
    layout = MemoryMigrator(str(root)).initialize()
    (root / ".daming-os").mkdir(parents=True, exist_ok=True)
    config_path = _config_path(root)
    created = not config_path.exists()
    updated = False
    if created:
        value = _default_config(root, integration)
    else:
        value = _load_config(root)
        if "_error" in value:
            raise ValueError(f"invalid existing configuration: {value['_error']}")
        defaults = _default_config(root, integration)
        value.setdefault("version", defaults["version"])
        value["integration"] = integration
        runtime = value.setdefault("runtime", {})
        if not isinstance(runtime, dict):
            raise ValueError("runtime configuration must be a JSON object")
        if "reviews_enabled" in runtime:
            runtime.setdefault("daily_digest_enabled", bool(runtime["reviews_enabled"]))
            runtime.pop("reviews_enabled")
        for key, default in defaults["runtime"].items():
            runtime.setdefault(key, default)
        runtime["workspace"] = str(root)
    serialized = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    updated = not created and config_path.read_text(encoding="utf-8") != serialized
    if created or updated:
        config_path.write_text(serialized, encoding="utf-8")
    return {"workspace": str(root), "layout": layout, "config": str(config_path), "created": created,
            "updated": updated, "integration": integration}


def install_host(host_dir: str, state_dir: str = ".daming", integration: str = "generic",
                 force: bool = False) -> Dict[str, Any]:
    """Install a single generic bridge into a Python agent project."""
    host = Path(host_dir).resolve()
    host.mkdir(parents=True, exist_ok=True)
    state_value = Path(state_dir)
    if state_value.is_absolute() or ".." in state_value.parts:
        raise ValueError("--state-dir must be a safe path inside the host project")
    state = (host / state_value).resolve()
    initialized = init_workspace(str(state), integration)
    runtime_config = _load_config(state).get("runtime", {})
    agent_id = str(runtime_config.get("agent_id", _stable_agent_id(state)))
    bootstrap = host / BOOTSTRAP_NAME
    bootstrap_existed = bootstrap.exists()
    source = (BOOTSTRAP_TEMPLATE
              .replace("__STATE_DIR__", json.dumps(state_value.as_posix()))
              .replace("__AGENT_ID__", json.dumps(agent_id)))
    if bootstrap.exists() and not force:
        existing = bootstrap.read_text(encoding="utf-8")
        if not existing.startswith('"""Generated Daming OS bridge.'):
            raise FileExistsError(f"refusing to overwrite existing {bootstrap}; use --force to replace it")
        if existing == source:
            bootstrap_action = "unchanged"
        else:
            bootstrap.write_text(source, encoding="utf-8")
            bootstrap_action = "updated"
    else:
        bootstrap.write_text(source, encoding="utf-8")
        bootstrap_action = "replaced" if bootstrap_existed else "created"
    state_ignore = state / ".gitignore"
    state_ignore.write_text("*\n!.gitignore\n", encoding="utf-8")
    manifest = state / ".daming-os" / "install.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "host": str(host),
        "workspace": str(state),
        "bootstrap": str(bootstrap),
        "integration": integration,
        "agent_id": agent_id,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = doctor(str(state), host_dir=str(host))
    return {
        "ok": report["ok"],
        "host": str(host),
        "workspace": str(state),
        "bootstrap": str(bootstrap),
        "bootstrap_action": bootstrap_action,
        "initialized": initialized,
        "doctor": report,
        "usage": "from daming_bootstrap import daming",
    }


def _load_config(root: Path) -> Dict[str, Any]:
    path = _config_path(root)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"_error": str(exc)}
    return value if isinstance(value, dict) else {"_error": "configuration must be a JSON object"}


def _functional_smoke_test(bootstrap: Optional[Path] = None) -> Dict[str, Any]:
    """Exercise a real capture → consolidation → recall loop in isolation."""
    plugin = None
    runtime = None
    try:
        with tempfile.TemporaryDirectory(prefix="daming-os-doctor-") as temporary:
            workspace = Path(temporary) / "state"
            session_id = "doctor-session"
            if bootstrap is not None:
                module_name = "daming_doctor_" + hashlib.sha256(
                    str(bootstrap).encode("utf-8")
                ).hexdigest()[:12]
                spec = importlib.util.spec_from_file_location(module_name, bootstrap)
                if spec is None or spec.loader is None:
                    raise RuntimeError("unable to load generated bootstrap")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                plugin = module.DamingPlugin(
                    state_dir=workspace, strict=True, agent_id="doctor-agent"
                )
                first = plugin.before_turn(
                    "doctor memory probe", session_id=session_id
                )
                plugin.after_turn(
                    "doctor memory probe", "doctor probe stored", session_id=session_id
                )
                plugin.after_turn(
                    "doctor second memory", "doctor second probe stored", session_id=session_id
                )
                runtime = plugin.runtime
                deep_sleep = runtime.deep_sleep.run()
                if deep_sleep.get("promoted"):
                    runtime.adapter.memory.cache.clear()
                recalled = plugin.before_turn(
                    "doctor memory probe", session_id=session_id
                )
            else:
                from .runtime import DamingRuntime
                runtime = DamingRuntime(str(workspace))
                first = runtime.hooks.before_turn({
                    "input": "doctor memory probe", "agent_id": "doctor-agent",
                    "session_id": session_id, "metadata": {},
                })
                runtime.hooks.after_turn({
                    "input": "doctor memory probe", "output": "doctor probe stored",
                    "agent_id": "doctor-agent", "session_id": session_id,
                    "metadata": {},
                })
                runtime.hooks.after_turn({
                    "input": "doctor second memory", "output": "doctor second probe stored",
                    "agent_id": "doctor-agent", "session_id": session_id,
                    "metadata": {},
                })
                deep_sleep = runtime.deep_sleep.run()
                if deep_sleep.get("promoted"):
                    runtime.adapter.memory.cache.clear()
                recalled = runtime.hooks.before_turn({
                    "input": "doctor memory probe", "agent_id": "doctor-agent",
                    "session_id": session_id, "metadata": {},
                })
            event_log = workspace / "memory" / "event_logs.jsonl"
            memories = recalled.get("daming_memories", [])
            ok = (
                "daming_degraded" not in first
                and event_log.is_file()
                and int(deep_sleep.get("promoted", 0)) >= 2
                and bool(memories)
            )
            return {
                "ok": ok,
                "capture": event_log.is_file(),
                "consolidated": int(deep_sleep.get("promoted", 0)),
                "recalled": len(memories),
            }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if plugin is not None:
            plugin.close()
        elif runtime is not None:
            runtime.close()


def manage_approval(workspace: str, action: str, proposal_id: str = "",
                    otp: str = "", reason: str = "") -> Dict[str, Any]:
    """Operate the local approval queue without requiring a chat integration."""
    root = Path(workspace).resolve()
    MemoryMigrator(str(root)).initialize()
    from .config import config
    from .growth.governance import GrowthLedger
    from .growth.proposals import ProposalStore

    config.WORKSPACE_ROOT = str(root)
    database = Path(config.GROWTH_DB_PATH)
    database = database if database.is_absolute() else root / database
    ledger = GrowthLedger(str(database))
    proposals = ProposalStore(str(database))

    def combined(record: Dict[str, Any]) -> Dict[str, Any]:
        try:
            proposal = proposals.get(record["proposal_id"])
        except KeyError:
            proposal = None
        return {**record, "proposal": proposal}

    if action == "list":
        return {"ok": True, "approvals": [combined(record) for record in ledger.records()]}
    if not proposal_id:
        raise ValueError("proposal id is required")
    record = next((item for item in ledger.records()
                   if item["proposal_id"] == proposal_id), None)
    if record is None:
        raise KeyError(proposal_id)
    if action == "show":
        return {"ok": True, "approval": combined(record)}
    if action == "issue":
        token = ledger.issue_otp(proposal_id)
        return {
            "ok": True,
            "proposal_id": proposal_id,
            "otp": token,
            "expires_in_minutes": 10,
            "warning": "This OTP is shown once and is not written to Daming logs.",
        }
    if action == "approve":
        if not otp:
            raise ValueError("OTP is required")
        from .runtime import DamingRuntime
        runtime = DamingRuntime(str(root))
        try:
            approved = runtime.growth_ledger.approve(proposal_id, otp)
            if not approved:
                return {"ok": False, "proposal_id": proposal_id,
                        "error": "invalid, expired, or locked OTP"}
            outcome = runtime._advance_growth().get(proposal_id, "approved")
            return {"ok": True, "proposal_id": proposal_id, "outcome": outcome}
        finally:
            runtime.close()
    if action == "reject":
        proposal = proposals.get(proposal_id)
        if proposal["state"] not in {"proposed", "validated", "approved"}:
            raise ValueError(f"proposal cannot be rejected from state {proposal['state']}")
        ledger.reject(proposal_id, reason or "rejected by user")
        proposals.transition(proposal_id, "rejected")
        return {"ok": True, "proposal_id": proposal_id, "outcome": "rejected"}
    raise ValueError(f"unknown approval action: {action}")


def doctor(workspace: str, host_dir: str = "") -> Dict[str, Any]:
    """Report the prerequisites for safely attaching Daming OS to a host."""
    root = Path(workspace).resolve()
    config = _load_config(root)
    dependencies: Dict[str, bool] = {}
    optional = {name: importlib.util.find_spec(name) is not None
                for name in ("lancedb", "litellm", "requests", "tiktoken")}
    layout = MemoryMigrator(str(root)).verify()
    writable = root.exists() and root.is_dir() and os.access(str(root), os.R_OK | os.W_OK | os.X_OK)
    bootstrap = Path(host_dir).resolve() / BOOTSTRAP_NAME if host_dir else None
    if bootstrap is None:
        manifest_path = root / ".daming-os" / "install.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                candidate = manifest.get("bootstrap") if isinstance(manifest, dict) else None
                bootstrap = Path(candidate).resolve() if isinstance(candidate, str) and candidate else None
            except (OSError, ValueError, json.JSONDecodeError):
                bootstrap = None
    bootstrap_ready = bootstrap is None or bootstrap.is_file()
    bootstrap_valid = bootstrap_ready
    if bootstrap is not None and bootstrap_ready:
        try:
            compile(bootstrap.read_text(encoding="utf-8"), str(bootstrap), "exec")
        except (OSError, SyntaxError):
            bootstrap_valid = False
    sqlite_fts5 = False
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE VIRTUAL TABLE probe USING fts5(content)")
        sqlite_fts5 = True
    except sqlite3.DatabaseError:
        pass
    finally:
        connection.close()
    config_valid = ("_error" not in config and config.get("version") == 1
                    and config.get("integration") in INTEGRATIONS
                    and isinstance(config.get("runtime"), dict))
    python_supported = sys.version_info >= (3, 9)
    smoke_test = _functional_smoke_test(bootstrap if bootstrap_valid else None)
    return {
        "ok": writable and bootstrap_valid and sqlite_fts5 and python_supported
              and all(layout.values()) and config_valid and smoke_test["ok"],
        "workspace": str(root),
        "writable": writable,
        "layout": layout,
        "config": config,
        "dependencies": dependencies,
        "optional_dependencies": optional,
        "python": {"version": ".".join(str(part) for part in sys.version_info[:3]),
                   "supported": python_supported},
        "sqlite_fts5": sqlite_fts5,
        "config_valid": config_valid,
        "bootstrap": str(bootstrap) if bootstrap else None,
        "bootstrap_ready": bootstrap_ready,
        "bootstrap_valid": bootstrap_valid,
        "smoke_test": smoke_test,
        "next_step": "Import 'daming' from daming_bootstrap and call before_turn/after_turn/on_error.",
    }


def status(workspace: str) -> Dict[str, Any]:
    root = Path(workspace).resolve()
    state_path = root / ".daming-os" / "scheduler-state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    except (OSError, ValueError, json.JSONDecodeError):
        state = {"error": "scheduler state is unreadable"}
    manifest_path = root / ".daming-os" / "install.json"
    manifest = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            manifest = {"error": "install manifest is unreadable"}
    runs_path = root / "memory" / "maintenance" / "task-runs.jsonl"
    latest_run: Dict[str, Any] = {}
    latest_runs: Dict[str, Dict[str, Any]] = {}
    if runs_path.exists():
        try:
            lines = [line for line in runs_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            for line in lines:
                entry = json.loads(line)
                if isinstance(entry, dict) and isinstance(entry.get("pipeline"), str):
                    latest_runs[entry["pipeline"]] = entry
            latest_run = json.loads(lines[-1]) if lines else {}
        except (OSError, ValueError, json.JSONDecodeError):
            latest_run = {"error": "task history is unreadable"}
            latest_runs = {}
    config = _load_config(root)
    runtime = config.get("runtime", {}) if isinstance(config.get("runtime"), dict) else {}
    digest_enabled = bool(runtime.get("daily_digest_enabled", runtime.get("reviews_enabled", False)))
    scheduler_jobs = state.get("jobs", {}) if isinstance(state, dict) and isinstance(state.get("jobs"), dict) else {}

    def last_run(name: str) -> Optional[str]:
        value = latest_runs.get(name, {}).get("at")
        return str(value) if value else None

    def scheduler_value(name: str, field: str) -> Any:
        return scheduler_jobs.get(name, {}).get(field)

    def timestamp_value(name: str, field: str) -> Optional[str]:
        value = scheduler_value(name, field)
        try:
            return datetime.fromtimestamp(float(value)).astimezone().isoformat() if value is not None else None
        except (OSError, TypeError, ValueError):
            return None

    services = {
        "core_memory": {
            "enabled": True,
            "trigger": "before_turn recall + after_turn capture",
            "required_for_memory": True,
        },
        "daily_sleep_memory": {
            "enabled": True,
            "job": "daily-maintenance",
            "schedule": "daily@02:30",
            "last_run": last_run("daily-maintenance"),
            "next_run": timestamp_value("daily-maintenance", "next_due_at"),
            "failure_count": scheduler_value("daily-maintenance", "failure_count") or 0,
            "last_error": scheduler_value("daily-maintenance", "last_error"),
            "catch_up": "next after_turn when the host was offline",
            "required_for_memory": True,
        },
        "weekly_governance": {
            "enabled": True,
            "job": "weekly-governance",
            "schedule": "weekly@Sun@23:30",
            "last_run": last_run("weekly-governance"),
            "next_run": timestamp_value("weekly-governance", "next_due_at"),
            "failure_count": scheduler_value("weekly-governance", "failure_count") or 0,
            "required_for_memory": False,
        },
        "daily_digest": {
            "enabled": digest_enabled,
            "job": "daily-digest",
            "schedule": "daily@23:00" if digest_enabled else None,
            "last_run": last_run("daily-digest"),
            "next_run": timestamp_value("daily-digest", "next_due_at"),
            "required_for_memory": False,
            "note": "optional merged summary and diary",
        },
        "approval_reminders": {
            "enabled": True,
            "trigger": "overdue approval detected after a turn or during daily maintenance",
            "cooldown": "24h",
            "required_for_memory": False,
        },
        "agent_quality": {
            "enabled": True,
            "trigger": "daily-maintenance",
            "detail_policy": "persist detailed health report only on anomalies",
            "required_for_memory": False,
        },
        "watchdog": {
            "enabled": bool(runtime.get("watchdog_enabled", False)),
            "job": "watchdog",
            "schedule": "every:1800" if runtime.get("watchdog_enabled", False) else None,
            "last_run": last_run("watchdog"),
            "next_run": timestamp_value("watchdog", "next_due_at"),
            "required_for_memory": False,
            "note": "stale-session check only",
        },
    }
    return {"workspace": str(root), "agent_id": runtime.get("agent_id"),
            "layout": MemoryMigrator(str(root)).verify(),
            "config": config, "install": manifest, "services": services,
            "scheduler_state": state, "latest_task_run": latest_run}


def main() -> None:
    parser = argparse.ArgumentParser(description="Daming OS host-neutral plugin CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize Daming-owned plugin state")
    init_parser.add_argument("--dir", default=".", help="Daming OS workspace directory")
    init_parser.add_argument("--integration", choices=INTEGRATIONS, default="generic")
    install_parser = subparsers.add_parser("install", help="Install a generic bridge into an agent project")
    install_parser.add_argument("--host-dir", default=".", help="Agent project root")
    install_parser.add_argument("--state-dir", default=".daming", help="Daming state path inside the host")
    install_parser.add_argument("--integration", choices=INTEGRATIONS, default="generic")
    install_parser.add_argument("--force", action="store_true", help="Replace a previously generated bridge")
    doctor_parser = subparsers.add_parser("doctor", help="Check plugin workspace and dependencies")
    doctor_parser.add_argument("--dir", default=".")
    status_parser = subparsers.add_parser("status", help="Show workspace and scheduler state")
    status_parser.add_argument("--dir", default=".")
    migrate_parser = subparsers.add_parser("migrate", help="Apply backed-up workspace schema migrations")
    migrate_parser.add_argument("--dir", default=".")
    approvals_parser = subparsers.add_parser("approvals", help="Manage growth approval requests")
    approvals_parser.add_argument("--dir", default=".")
    approval_actions = approvals_parser.add_subparsers(dest="approval_action", required=True)
    approval_actions.add_parser("list", help="List approval records")
    approval_show = approval_actions.add_parser("show", help="Show an approval and proposal")
    approval_show.add_argument("proposal_id")
    approval_issue = approval_actions.add_parser("issue", help="Issue a one-time approval code")
    approval_issue.add_argument("proposal_id")
    approval_approve = approval_actions.add_parser("approve", help="Approve and safely advance a proposal")
    approval_approve.add_argument("proposal_id")
    approval_approve.add_argument("otp")
    approval_reject = approval_actions.add_parser("reject", help="Reject a proposal")
    approval_reject.add_argument("proposal_id")
    approval_reject.add_argument("--reason", default="")
    maintain_parser = subparsers.add_parser("maintain", help="Run portable memory maintenance")
    maintain_parser.add_argument("--dir", default=".", help="Workspace directory")
    maintain_parser.add_argument("--review-days", type=int, default=1)
    maintain_parser.add_argument("--consolidate", action="store_true")

    args = parser.parse_args()
    try:
        if args.command == "init":
            result = init_workspace(args.dir, args.integration)
        elif args.command == "install":
            result = install_host(args.host_dir, args.state_dir, args.integration, args.force)
        elif args.command == "doctor":
            result = doctor(args.dir)
        elif args.command == "status":
            result = status(args.dir)
        elif args.command == "migrate":
            result = MemoryMigrator(args.dir).migrate()
        elif args.command == "approvals":
            result = manage_approval(
                args.dir,
                args.approval_action,
                getattr(args, "proposal_id", ""),
                getattr(args, "otp", ""),
                getattr(args, "reason", ""),
            )
        else:
            from .memory.maintenance import MemoryMaintenance
            maintenance = MemoryMaintenance(args.dir)
            result = {"review": str(maintenance.review(args.review_days))}
            if args.consolidate:
                result["consolidated"] = maintenance.consolidate()
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(2)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if args.command in {"doctor", "install"} and not result.get("ok", False):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
