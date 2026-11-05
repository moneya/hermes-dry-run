"""Classification and rendering for the dry-run plugin.

Two jobs:
  1. Decide whether a tool call would change something, and how badly.
  2. Render an honest report of what *would* have happened, so a run can be reviewed
     before it is trusted.

Risk is not binary. A read is free, a file write is recoverable, a `git push --force` or a
`DROP TABLE` is not. The tier decides the wording and the colour of the warning, and it is
what makes the report useful rather than a blanket refusal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Tools that only read. Everything not listed here and not obviously read-only is treated
# as mutating when the plugin is in "guard" mode — safer to over-report than to let a write
# through because a new tool name was not on a list.
READ_ONLY_TOOLS = {
    "read_file", "search_files", "web_search", "web_extract", "session_search",
    "skill_view", "skills_list", "todo_list", "tool_search", "tool_describe",
    "process_manage", "browser_vault", "list_files", "get_weather",
}

RISK_SAFE = "SAFE"
RISK_WRITE = "WRITE"
RISK_DESTRUCTIVE = "DESTRUCTIVE"
RISK_IRREVERSIBLE = "IRREVERSIBLE"

# Order matters: first match wins, most severe first.
COMMAND_RULES: list[tuple[str, str, str]] = [
    (r"\brm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)\b", RISK_IRREVERSIBLE, "recursive force delete"),
    (r"\bmkfs\b", RISK_IRREVERSIBLE, "formats a filesystem"),
    (r"\bdd\b[^.\n]{0,12}?\bif[=\s]", RISK_IRREVERSIBLE, "raw disk write"),
    (r":\(\)\s*\{", RISK_IRREVERSIBLE, "fork bomb"),
    (r"\b(drop|truncate)\s+(table|database|schema)\b(?!\s+if\s+exists)", RISK_IRREVERSIBLE, "destroys database objects"),
    (r"\bgit\s+push\b[^.\n]{0,30}(-f\b|--force(?![\w-]))", RISK_IRREVERSIBLE, "force-push rewrites remote history"),
    (r"\b(npm|yarn|pnpm)\s+publish\b|\bcargo\s+publish\b|\btwine\s+upload\b", RISK_IRREVERSIBLE, "publishes to a public registry"),
    (r"\b(kubectl\s+(apply|delete)|terraform\s+apply|helm\s+(install|upgrade))\b", RISK_IRREVERSIBLE, "changes live infrastructure"),
    (r"\b(sudo|su\s+-)\b", RISK_DESTRUCTIVE, "runs with elevated privileges"),
    (r"\bsystemctl\s+(stop|disable|restart)\b", RISK_DESTRUCTIVE, "changes a running service"),
    (r"\bcurl\b[^.\n]{0,80}(-X\s*(POST|PUT|PATCH|DELETE)|--data)", RISK_DESTRUCTIVE, "sends a state-changing HTTP request"),
    (r"\bgit\s+(commit|merge|rebase|reset|checkout\s+-f)\b", RISK_WRITE, "changes repository state"),
    (r"\bmigrate\b|\bmigrations?\b", RISK_WRITE, "runs database migrations"),
    (r"\b(chmod|chown)\b", RISK_WRITE, "changes file permissions"),
    (r">\s*[^\s&|]+", RISK_WRITE, "redirects output into a file"),
]

_WRITE_TOOLS = {
    "write_file", "patch", "delete_file", "create_file", "move_file", "copy_file",
    "skill_manage", "memory", "cronjob_manage", "kanban_create", "kanban_complete",
    "kanban_block", "kanban_request_review", "kanban_attach", "delegate_task",
}

_READONLY_PREFIXES = ("read_", "get_", "list_", "show_", "search_", "view_", "describe_")


@dataclass
class Plan:
    tool: str
    args: dict
    risk: str
    reasons: list[str] = field(default_factory=list)
    detail: list[str] = field(default_factory=list)
    block: bool = True


def _text(args: dict) -> str:
    """Flatten an args dict to searchable text, bounded so a huge payload stays cheap."""
    parts = []
    for key in ("command", "cmd", "script", "content", "new_string", "old_string",
                "path", "file_path", "query", "url", "description"):
        v = args.get(key)
        if isinstance(v, str):
            parts.append(v)
    if not parts:
        parts = [str(v) for v in args.values() if isinstance(v, str)]
    return "\n".join(parts)[:200_000]


def _command_risk(text: str) -> tuple[str, list[str], list[str]]:
    risk = ""
    reasons, labels = [], []
    order = {RISK_SAFE: 0, RISK_WRITE: 1, RISK_DESTRUCTIVE: 2, RISK_IRREVERSIBLE: 3}
    for pattern, tier, label in COMMAND_RULES:
        if re.search(pattern, text, re.I):
            labels.append(label)
            if order[tier] > order.get(risk, -1):
                risk = tier
            if tier == RISK_IRREVERSIBLE:
                reasons.append(label)
    return risk, reasons, labels


def classify(tool_name: str, args: dict) -> Plan:
    name = (tool_name or "").strip()
    args = args or {}
    plan = Plan(tool=name, args=args, risk=RISK_WRITE)

    if name in READ_ONLY_TOOLS or name.startswith(_READONLY_PREFIXES):
        plan.risk, plan.block = RISK_SAFE, False
        return plan

    text = _text(args)
    risk, reasons, labels = _command_risk(text)

    if name == "terminal":
        plan.risk = risk or RISK_WRITE
        plan.reasons = reasons or (labels or ["runs a shell command"])
        cmd = str(args.get("command") or args.get("cmd") or "")
        plan.detail.append(("command", cmd.splitlines()[0][:180] if cmd else "(none)"))
    elif name == "execute_code":
        plan.risk = risk or RISK_WRITE
        plan.reasons = reasons or (labels or ["executes code with full user permissions"])
        code = str(args.get("code") or "")
        plan.detail.append(("code", f"{len(code)} chars, {code.count(chr(10)) + 1} lines"))
    elif name == "write_file":
        content = str(args.get("content") or "")
        plan.risk = RISK_WRITE
        plan.reasons = ["creates or overwrites a file"]
        plan.detail.append(("path", str(args.get("path") or "(none)")))
        plan.detail.append(("content", f"{len(content):,} chars, {content.count(chr(10)) + 1} lines"))
        if content:
            preview = content.strip().splitlines()[:3]
            plan.detail.append(("preview", " / ".join(p.strip()[:70] for p in preview)[:180]))
    elif name == "patch":
        plan.risk = RISK_WRITE
        plan.reasons = ["edits an existing file"]
        plan.detail.append(("path", str(args.get("path") or "(none)")))
        old = str(args.get("old_string") or "")
        new = str(args.get("new_string") or "")
        plan.detail.append(("replace", f"{len(old)} chars -> {len(new)} chars"))
    elif name == "browser_exec":
        plan.risk = risk or RISK_WRITE
        plan.reasons = reasons or (labels or ["drives a live browser session"])
        plan.detail.append(("action", str(args.get("code") or "")[:120] or "(none)"))
    elif name in _WRITE_TOOLS:
        plan.risk = risk or RISK_WRITE
        plan.reasons = reasons or (labels or [f"calls {name}, which changes state"])
        for k in ("path", "name", "action", "job_id", "task_id"):
            if args.get(k):
                plan.detail.append((k, str(args[k])[:120]))
    else:
        # Unknown tool: report it rather than assume it is harmless.
        plan.risk = risk or RISK_WRITE
        plan.reasons = reasons or (labels or [f"{name or 'unknown tool'} is not on the read-only list"])
        for k, v in list(args.items())[:4]:
            if isinstance(v, (str, int, float)):
                plan.detail.append((k, str(v)[:120]))

    plan.block = True
    return plan


_TIER_NOTE = {
    RISK_WRITE: "recoverable if you have the previous state",
    RISK_DESTRUCTIVE: "may be recoverable, may not — inspect before running for real",
    RISK_IRREVERSIBLE: "cannot be undone",
}


def render(plan: Plan, product: str = "Hermes") -> str:
    w = 74
    lines = [
        "DRY RUN — NOT EXECUTED",
        "=" * w,
        f"tool      : {plan.tool}",
        f"risk      : {plan.risk}  ({_TIER_NOTE.get(plan.risk, 'changes state')})",
        f"why       : {'; '.join(plan.reasons)[:150]}",
    ]
    for k, v in plan.detail:
        lines.append(f"{k:<10}: {v}")
    lines += [
        "-" * w,
        "Nothing was executed. No file was written, no command ran, nothing was sent.",
        f"This is the plan a real run would carry out. To execute for real, turn dry-run off:",
        "   /dryrun off        (this session)",
        "   DRY_RUN_MODE=off   (one run)",
        f"or add '{plan.tool}' to allow_tools in dry_run.json",
        "=" * w,
    ]
    return "\n".join(lines)
