"""dry-run — see what an agent would do, without letting it do it.

Registers one ``tool_execution`` middleware. In dry-run mode it does not call the
downstream tool at all: it returns a report describing what the call would have done and
how much damage it could cause.

The contract that makes this safe: ``next_call`` is simply not invoked, so the real tool
never runs. There is no partial execution and no "attempted and rolled back" — nothing
happens.

Design rules:
1. Reads always pass. A dry run that cannot read files or search is useless for planning.
2. Unknown tools are simulated, not allowed. A new tool name must never be a way past the
   guard.
3. The report says what would happen and what it would cost — not merely "blocked".
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys

logger = logging.getLogger(__name__)

_TOOLSET = "safety"

# Sibling modules are loaded by explicit path under a unique prefix. Importing them by bare
# name (``import settings``) is a trap: another plugin with a ``settings.py`` gets the same
# entry in sys.modules, and whichever loads first silently wins for the whole process.
_PKG = "hermes_plugin_dry_run"


def _mod(name: str):
    full = f"{_PKG}_{name}"
    if full in sys.modules:
        return sys.modules[full]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"{name}.py")
    spec = importlib.util.spec_from_file_location(full, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


def make_middleware(ctx):
    def on_tool_execution(tool_name=None, args=None, original_args=None,
                          next_call=None, **_context):
        call = next_call if callable(next_call) else (lambda: {})

        try:
            cfg_mod = _mod("dryrun_settings")
            cfg = cfg_mod.settings()
            if not cfg_mod.should_simulate(tool_name, cfg):
                return call()

            simulate = _mod("dryrun_simulate")
            plan = simulate.classify(tool_name, args if isinstance(args, dict) else {})

            # In "guard" mode, a genuinely read-only tool still executes — the allow-list
            # check above covers the common names, this catches the classifier's view.
            if cfg.get("mode") == "guard" and plan.risk == simulate.RISK_SAFE:
                return call()

            cfg_mod.record(plan)
            logger.warning("dry-run: simulated %s (%s)", tool_name, plan.risk)
            return simulate.render(plan)

        except Exception as exc:
            # Fail CLOSED and loudly. The contract of this plugin is "nothing executes in
            # dry-run mode"; silently calling the tool because classification broke would
            # break that promise at exactly the moment something is wrong.
            logger.warning("dry-run could not classify %s (%s: %s) — blocking by default",
                           tool_name, type(exc).__name__, exc)
            return (
                "DRY RUN — NOT EXECUTED (classification failed)\n"
                + "=" * 74 + "\n"
                f"tool      : {tool_name}\n"
                f"problem   : dry-run could not inspect this call ({type(exc).__name__}).\n"
                "action    : blocked, because the plugin cannot confirm the call is safe.\n"
                "Nothing was executed.\n"
                + "=" * 74 + "\n"
                "Fix the plugin (or set DRY_RUN_MODE=off to disable dry-run deliberately)\n"
                "before relying on this session's results.\n"
            )

    return on_tool_execution


def _report_tool(**_kwargs):
    import json

    return json.dumps(_mod("dryrun_settings").summary(), indent=2)


def _dryrun_command(raw_args: str = "") -> str:
    cfg_mod = _mod("dryrun_settings")
    arg = (raw_args or "").strip().lower()

    if arg in ("off", "on", "guard", "all"):
        target = "off" if arg == "off" else arg
        cfg_mod.set_mode(target)
        note = {
            "off": "Dry run OFF — tools will execute for real from the next call.",
            "guard": "Dry run ON — anything that changes state is simulated.",
            "all": "Dry run ON — everything except read-only allow-listed tools is simulated.",
        }[target]
        return note

    info = cfg_mod.summary()
    lines = [
        f"dry-run — mode: {info['mode']}",
        f"simulated this session: {info['simulated_total']}",
    ]
    if info["by_risk"]:
        lines.append("by risk: " + ", ".join(f"{k}×{v}" for k, v in sorted(info["by_risk"].items())))
    if info["by_tool"]:
        lines.append("by tool: " + ", ".join(f"{k}×{v}" for k, v in sorted(info["by_tool"].items())))
    for it in info["recent"]:
        lines.append(f"  {it['ts']}  {it['tool']:<14} {it['risk']:<13} {'; '.join(it['reasons'])[:60]}")
    lines += [
        "",
        "  /dryrun off | on | all     change mode",
        f"  log: {info['log']}",
    ]
    return "\n".join(lines)


def _control_tool(action: str = "status", approved_by: str = "", **_kwargs):
    """Turn dry-run on or off from inside a conversation.

    OFF requires `approved_by` quoting the human's request. ON never does — it is the safe
    direction. Every change is announced in the transcript and written to the audit log.
    """
    cfg_mod = _mod("dryrun_settings")
    action = (action or "status").strip().lower()

    if action == "status":
        info = cfg_mod.summary()
        return (
            f"dry-run mode: {info['mode']}\n"
            f"simulated so far: {info['simulated_total']}\n"
            f"by risk: {info['by_risk'] or '{}'}\n"
            f"log: {info['log']}"
        )

    target = {"on": "guard"}.get(action, action)
    result = cfg_mod.set_mode_with_approval(target, approved_by)
    if not result.get("ok"):
        return f"REFUSED: {result.get('error')}"

    if target == "off":
        return (
            "DRY-RUN IS NOW OFF — tools execute for real from the next call.\n"
            f"  changed : {result['from']} -> {result['to']}\n"
            f"  approved: {result['approved_by']}\n"
            f"  at      : {result['ts']}\n"
            "This is recorded in the audit log. Send /dryrun on to restore protection."
        )
    note = {
        "guard": "anything that changes state is now simulated",
        "all": "everything except read-only allow-listed tools is now simulated",
    }.get(target, "")
    return (
        f"Dry-run is ON ({target}) — {note}.\n"
        f"  changed : {result['from']} -> {result['to']}\n"
        f"  at      : {result['ts']}"
    )


def register(ctx) -> None:
    ctx.register_middleware("tool_execution", make_middleware(ctx))

    ctx.register_tool(
        name="dryrun_control",
        toolset=_TOOLSET,
        schema={
            "name": "dryrun_control",
            "description": (
                "Turn dry-run mode on or off. Turning it OFF requires approved_by quoting "
                "the human's explicit request, and is recorded and announced. Turning it ON "
                "is always allowed. Use action='status' to inspect without changing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "on", "off", "all"],
                        "description": "status, on (=guard), off, or all",
                    },
                    "approved_by": {
                        "type": "string",
                        "description": (
                            "Required for action='off'. Quote the human's words that "
                            "authorise disabling dry-run, e.g. \"user: 'turn it off now'\"."
                        ),
                    },
                },
                "required": ["action"],
            },
        },
        handler=_control_tool,
        description="Turn dry-run on or off (off requires human approval)",
        emoji="🧪",
    )

    ctx.register_tool(
        name="dry_run_report",
        toolset=_TOOLSET,
        schema={
            "name": "dry_run_report",
            "description": (
                "Report what dry-run mode has simulated: current mode, counts by risk tier "
                "and by tool, and the recent plans. Read-only."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        handler=_report_tool,
        description="Show what the agent would have done, without doing it",
        emoji="🧪",
    )

    ctx.register_command(
        "dryrun",
        _dryrun_command,
        description="Show or change dry-run mode (off | on | all)",
        args_hint="[off|on|all]",
    )

    logger.debug("dry-run registered")
