"""Tests for dry-run.

The load-bearing assertion in this file is `test_simulated_tool_never_executes`: the whole
value of the plugin is that the downstream tool is not called. Everything else is wording.

Run: python3 tests/test_dryrun.py
"""

from __future__ import annotations

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
sys.path.insert(0, PLUGIN)

import dryrun_simulate as simulate  # noqa: E402


def _plugin():
    spec = importlib.util.spec_from_file_location(
        "dr_init", os.path.join(PLUGIN, "__init__.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(tool, args=None, result="ok", mode="guard"):
    """Dispatch through the plugin middleware with a fake downstream that records calls."""
    os.environ["DRY_RUN_MODE"] = mode
    calls = []

    def next_call():
        calls.append(1)
        return result

    out = _plugin().make_middleware(None)(
        tool_name=tool, args=args or {}, next_call=next_call)
    return out, calls


# ------------------------------------------------------------------ risk classification

def test_revursive_delete_is_irreversible():
    p = simulate.classify("terminal", {"command": "rm -rf /var/www"})
    assert p.risk == simulate.RISK_IRREVERSIBLE
    assert "recursive force delete" in " ".join(p.reasons)


def test_force_push_is_irreversible():
    p = simulate.classify("terminal", {"command": "git push origin main --force"})
    assert p.risk == simulate.RISK_IRREVERSIBLE


def test_migration_is_write_not_irreversible():
    p = simulate.classify("terminal", {"command": "php artisan migrate --force"})
    assert p.risk == simulate.RISK_WRITE


def test_harmless_command_is_still_simulated_as_write():
    p = simulate.classify("terminal", {"command": "ls -la"})
    assert p.risk == simulate.RISK_WRITE


def test_read_tool_is_safe():
    for name in ("read_file", "search_files", "web_search", "skill_view"):
        assert simulate.classify(name, {"path": "x"}).risk == simulate.RISK_SAFE


def test_unknown_tool_is_not_treated_as_safe():
    p = simulate.classify("some_new_tool_2027", {"x": 1})
    assert p.risk != simulate.RISK_SAFE
    assert p.block


def test_write_file_reports_size_and_path():
    p = simulate.classify("write_file", {"path": "src/a.ts", "content": "x\n" * 40})
    detail = dict(p.detail)
    assert detail["path"] == "src/a.ts"
    assert "lines" in detail["content"]


# ------------------------------------------------------------------ the safety property

def test_simulated_tool_never_executes():
    """The tool must not run when the call is simulated."""
    out, calls = _run("terminal", {"command": "rm -rf ~/project"})
    assert calls == [], "downstream tool ran during a dry run"
    assert "DRY RUN" in out
    assert "NOT EXECUTED" in out


def test_write_is_simulated_too():
    out, calls = _run("write_file", {"path": "a.txt", "content": "hello"})
    assert calls == []
    assert "NOT EXECUTED" in out


def test_read_passes_through_and_executes():
    out, calls = _run("read_file", {"path": "a.txt"}, result="file body")
    assert out == "file body"
    assert len(calls) == 1


def test_off_mode_executes_everything():
    out, calls = _run("terminal", {"command": "rm -rf x"}, result="ran", mode="off")
    assert out == "ran"
    assert len(calls) == 1


def test_allowed_tool_runs_even_in_all_mode():
    out, calls = _run("read_file", {"path": "a.txt"}, result="body", mode="all")
    assert out == "body" and len(calls) == 1


def test_all_mode_simulates_unknown_reads():
    out, calls = _run("mystery_tool", {}, result="ran", mode="all")
    assert calls == []
    assert "NOT EXECUTED" in out


def test_next_call_called_exactly_once_when_allowed():
    out, calls = _run("read_file", {"path": "a"})
    assert len(calls) == 1


# ------------------------------------------------------------------ robustness

def test_never_raises_on_odd_args():
    for args in (None, {}, {"command": 123}, {"content": None}, {"weird": object()}):
        out, _ = _run("terminal", args if isinstance(args, dict) else {})
        assert isinstance(out, str)


def test_report_says_what_would_happen():
    out, _ = _run("terminal", {"command": "git push --force"})
    low = out.lower()
    assert "cannot be undone" in low or "irreversible" in low
    assert "nothing was executed" in low


def test_register_wires_everything():
    class Ctx:
        def __init__(self):
            self.middleware, self.tools, self.commands = {}, {}, {}

        def register_middleware(self, kind, cb):
            self.middleware[kind] = cb

        def register_tool(self, **kw):
            self.tools[kw["name"]] = kw

        def register_command(self, name, handler, description="", **kw):
            self.commands[name] = handler

    ctx = Ctx()
    _plugin().register(ctx)
    assert "tool_execution" in ctx.middleware
    assert "dry_run_report" in ctx.tools
    assert "dryrun_control" in ctx.tools
    assert "dryrun" in ctx.commands


# ------------------------------------------------------------------ approval flow

def _settings_mod():
    return _plugin()._mod("dryrun_settings")


def _preserve_state():
    mod = _settings_mod()
    path = mod._CONFIG
    return path, (path.read_text() if path.exists() else None)


def _restore_state(path, original):
    if original is None:
        if path.exists():
            path.unlink()
    else:
        path.write_text(original)


def test_control_tool_is_exempt_from_simulation():
    """dryrun_control must stay callable while dry-run is on, or nothing can turn it off."""
    cfg = _settings_mod().DEFAULTS
    assert "dryrun_control" in cfg["exempt_tools"]


def test_disable_without_approval_is_refused():
    path, original = _preserve_state()
    try:
        res = _settings_mod().set_mode_with_approval("off", "")
        assert res["ok"] is False
        assert "approved_by" in res["error"]
    finally:
        _restore_state(path, original)


def test_disable_with_approval_is_allowed_and_logged():
    path, original = _preserve_state()
    try:
        res = _settings_mod().set_mode_with_approval("off", "user: 'turn it off now'")
        assert res["ok"] is True and res["to"] == "off"
        assert res["approved_by"] == "user: 'turn it off now'"
        log = _settings_mod()._LOG
        text = log.read_text() if log.exists() else ""
        assert "mode_change" in text and "turn it off now" in text
    finally:
        _restore_state(path, original)


def test_enable_never_requires_approval():
    path, original = _preserve_state()
    try:
        res = _settings_mod().set_mode_with_approval("guard", "")
        assert res["ok"] is True and res["to"] == "guard"
    finally:
        _restore_state(path, original)


def test_control_tool_refuses_and_reports():
    path, original = _preserve_state()
    try:
        plugin = _plugin()
        out = plugin._control_tool(action="off", approved_by="")
        assert out.startswith("REFUSED")
        out2 = plugin._control_tool(action="off", approved_by="user: yes do it")
        assert "DRY-RUN IS NOW OFF" in out2
    finally:
        _restore_state(path, original)


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                passed += 1
                print(f"  PASS  {name}")
            except AssertionError as exc:
                failed += 1
                print(f"  FAIL  {name}: {exc}")
            except Exception as exc:
                failed += 1
                print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
    os.environ.pop("DRY_RUN_MODE", None)
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
