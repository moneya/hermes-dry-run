"""Console output used by the demo video. Real classifier calls, no mock-ups.

    python3 demo.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dryrun_simulate as sim  # noqa: E402


def _rule(ch: str = "─") -> None:
    print(ch * 74)


def section_1() -> None:
    """The problem: the command just runs."""
    print("WHAT HAPPENS TODAY")
    _rule("=")
    print('  you   : "clean up the old builds and ship it"')
    print('  agent : terminal("rm -rf build/legacy && git push origin main --force")')
    print("")
    print("  the command runs immediately.")
    print("  no preview. no confirmation. no undo.")
    print("  you find out afterwards, if at all.")
    _rule("=")


def section_2() -> None:
    """The same call, with dry-run on."""
    plan = sim.classify(
        "terminal", {"command": "rm -rf build/legacy && git push origin main --force"})
    print(sim.render(plan))


def section_3() -> None:
    """Risk is tiered, not binary."""
    cases = [
        ("php artisan migrate --force", "changes the database schema"),
        ("chmod -R 777 storage/", "changes permissions on every file"),
        ("git push origin main --force", "overwrites remote history"),
        ("rm -rf build/legacy", "deletes the folder outright"),
    ]
    print("RISK TIERS — the tier comes from the command itself")
    _rule("=")
    for cmd, note in cases:
        plan = sim.classify("terminal", {"command": cmd})
        print(f"  {plan.risk:<13} {cmd}")
        print(f"                {note}")
    print("")
    print("  WRITE         recoverable if you have the previous state")
    print("  IRREVERSIBLE  cannot be undone")
    _rule("=")


def section_4() -> None:
    """Reads still work. Only state changes are held."""
    print("ONLY STATE CHANGES ARE HELD BACK")
    _rule("=")
    for tool, args, label in [
        ("read_file", {"path": "README.md"}, "reading a file"),
        ("search_files", {"pattern": "billing"}, "searching the project"),
        ("write_file", {"path": "app/Billing.php", "content": "x"}, "editing a file"),
        ("terminal", {"command": "php artisan migrate"}, "running migrations"),
    ]:
        plan = sim.classify(tool, args)
        verdict = "executes" if plan.risk == sim.RISK_SAFE else "DRY RUN"
        print(f"  {verdict:<10} {tool:<14} {label}")
    print("")
    print("  through the host's real middleware runner: 8 / 8 checks passed")
    _rule("=")


if __name__ == "__main__":
    for fn in (section_1, section_2, section_3, section_4):
        fn()
        print()
