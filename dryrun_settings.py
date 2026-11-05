"""Settings and run log for the dry-run plugin.

Config lives in ``dry_run.json`` next to the plugin, created on first run. ``DRY_RUN_MODE``
overrides the mode for a single run, which is what you want in CI and in a demo.

The run log is the other half of the feature: a real session can be compared against what
the dry run predicted. It stores the plan — tool, risk, target — and never the payload.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_CONFIG = _HERE / "dry_run.json"
_LOG = _HERE / "dry_run_log.jsonl"

_LOCK = threading.Lock()
_RECENT: list[dict[str, Any]] = []
_MAX_RECENT = 200

# "guard" simulates anything that would change state; "all" simulates reads too, which is
# useful for showing a complete plan with no side effects at all.
MODES = ("off", "guard", "all")

DEFAULTS: dict[str, Any] = {
    "mode": "guard",
    # Tools that always execute even in "all" mode — reads you need in order to plan.
    "allow_tools": [
        "read_file", "search_files", "web_search", "web_extract", "session_search",
        "skill_view", "skills_list", "todo_list", "tool_search", "tool_describe",
    ],
    # Tools never simulated, whatever the mode. dryrun_control has to stay callable while
    # dry-run is on, otherwise there is no way to turn it off again from a conversation.
    "exempt_tools": ["dry_run_report", "dryrun_control"],
}


def _load() -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    try:
        if _CONFIG.exists():
            cfg.update(json.loads(_CONFIG.read_text(encoding="utf-8")))
    except Exception:
        pass
    env = os.environ.get("DRY_RUN_MODE", "").strip().lower()
    if env in MODES:
        cfg["mode"] = env
    return cfg


def settings() -> dict[str, Any]:
    return _load()


def set_mode(mode: str) -> bool:
    """Persist a mode change so /dryrun survives the session."""
    mode = (mode or "").strip().lower()
    if mode not in MODES:
        return False
    cfg = {}
    try:
        if _CONFIG.exists():
            cfg = json.loads(_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    cfg["mode"] = mode
    try:
        _CONFIG.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def set_mode_with_approval(mode: str, approved_by: str) -> dict[str, Any]:
    """Change mode from inside a conversation, recording who approved it.

    Turning dry-run ON never needs approval — it is the safe direction. Turning it OFF
    does, because that is the one action that lets tools execute for real.

    This is an evidentiary control, not a security boundary: an agent could claim approval
    that no human gave. What it guarantees is that every disable is announced in the
    transcript and written to the audit log with the stated reason, so a human can see it
    and reverse it with /dryrun on.
    """
    mode = (mode or "").strip().lower()
    approved_by = (approved_by or "").strip()
    if mode not in MODES:
        return {"ok": False, "error": f"mode must be one of {MODES}"}
    if mode == "off" and len(approved_by) < 3:
        return {
            "ok": False,
            "error": "turning dry-run OFF requires approved_by — quote the human's request",
        }
    previous = _load().get("mode")
    if not set_mode(mode):
        return {"ok": False, "error": "could not write dry_run.json"}

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": "mode_change",
        "from": previous,
        "to": mode,
        "approved_by": approved_by or "(not required)",
    }
    with _LOCK:
        try:
            with _LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass
    return {"ok": True, **entry}


def should_simulate(tool_name: str, cfg: dict[str, Any] | None = None) -> bool:
    cfg = cfg or _load()
    mode = cfg.get("mode")
    if mode == "off":
        return False
    name = (tool_name or "").strip()
    if name in (cfg.get("exempt_tools") or []):
        return False
    if name in (cfg.get("allow_tools") or []):
        return False
    return True  # "guard" blocks everything not allow-listed; classification sorts risk


def record(plan) -> dict[str, Any]:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool": plan.tool,
        "risk": plan.risk,
        "reasons": plan.reasons[:4],
        "target": dict(plan.detail[:2]),
    }
    with _LOCK:
        _RECENT.append(entry)
        del _RECENT[:-_MAX_RECENT]
        try:
            with _LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass
    return entry


def recent(limit: int = 50) -> list[dict[str, Any]]:
    with _LOCK:
        return list(_RECENT[-limit:])


def summary() -> dict[str, Any]:
    cfg = _load()
    items = recent(_MAX_RECENT)
    by_risk: dict[str, int] = {}
    by_tool: dict[str, int] = {}
    for it in items:
        by_risk[it["risk"]] = by_risk.get(it["risk"], 0) + 1
        by_tool[it["tool"]] = by_tool.get(it["tool"], 0) + 1
    return {
        "mode": cfg.get("mode"),
        "simulated_total": len(items),
        "by_risk": by_risk,
        "by_tool": by_tool,
        "recent": items[-8:],
        "log": str(_LOG),
    }
