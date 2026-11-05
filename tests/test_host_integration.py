"""Integration test through the HOST's real middleware dispatch, not a simulation.

The claim this file exists to prove: when dry-run mode is on, a mutating tool call made
through Hermes' own middleware runner never reaches the tool, and the agent gets back a
report instead of a result.

Run: python3 tests/test_host_integration.py
"""

from __future__ import annotations

import os
import sys

HERMES = "/Users/theoneglobal/.hermes/hermes-agent"
sys.path.insert(0, HERMES)
os.environ["DRY_RUN_MODE"] = "guard"


def main() -> int:
    from hermes_cli.middleware import run_tool_execution_middleware
    from hermes_cli.plugins import get_plugin_manager

    mgr = get_plugin_manager()
    mgr.discover_and_load()
    registered = getattr(mgr, "_middleware", {})
    print("middleware on host:", {k: len(v) for k, v in registered.items()})
    if not registered.get("tool_execution"):
        print("FAIL: middleware did not register on the host")
        return 1

    ran: list[str] = []

    def real_tool(args):
        ran.append("RAN")
        return "REAL RESULT — the tool actually ran"

    # ---- 1. mutating call: must be simulated, never executed
    out1 = run_tool_execution_middleware(
        "terminal",
        {"command": "rm -rf ~/projects && git push origin main --force"},
        real_tool,
        original_args={"command": "rm -rf ~/projects && git push origin main --force"},
    )
    t1 = str(out1)
    step1_ran = bool(ran)
    print("\n--- 1. mutating call must NOT execute ---")
    print(f"  tool executed      : {step1_ran}   <- must be False")
    print(f"  report returned    : {'DRY RUN' in t1}")
    print(f"  risk detected      : {'IRREVERSIBLE' in t1}")
    print(f"  leaked real result : {'REAL RESULT' in t1}")
    print("\n  " + "\n  ".join(t1.splitlines()[:8]))

    # ---- 2. read-only call: must execute normally
    del ran[:]
    out2 = run_tool_execution_middleware(
        "read_file", {"path": "notes.md"}, real_tool, original_args={"path": "notes.md"})
    t2 = str(out2)
    step2_ran = bool(ran)
    print("\n--- 2. read-only call must still execute ---")
    print(f"  tool executed      : {step2_ran}   <- must be True")
    print(f"  real result passed : {'REAL RESULT' in t2}")

    # ---- 3. file write: simulated, target reported
    del ran[:]
    out3 = run_tool_execution_middleware(
        "write_file",
        {"path": "/tmp/scratch.txt", "content": "hello\n" * 10},
        real_tool,
        original_args={},
    )
    t3 = str(out3)
    step3_ran = bool(ran)
    print("\n--- 3. file write must be simulated ---")
    print(f"  tool executed      : {step3_ran}   <- must be False")
    print(f"  target reported    : {'/tmp/scratch.txt' in t3}")

    checks = [
        ("mutating call blocked", not step1_ran),
        ("report shows DRY RUN", "DRY RUN" in t1),
        ("irreversible classified", "IRREVERSIBLE" in t1),
        ("real result not leaked", "REAL RESULT" not in t1),
        ("read still executes", step2_ran),
        ("read result intact", "REAL RESULT" in t2),
        ("write blocked", not step3_ran),
        ("write target reported", "/tmp/scratch.txt" in t3),
    ]
    print("\n--- checks ---")
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    all_ok = all(ok for _, ok in checks)
    print("\n=== VERDICT:", "PASS" if all_ok else "FAIL", "===")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
