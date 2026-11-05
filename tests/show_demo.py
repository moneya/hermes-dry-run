"""Show what dry-run reports for a handful of real calls. No config, no network.

    python3 tests/show_demo.py
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import dryrun_simulate as sim  # noqa: E402

CASES = [
    ("terminal", {"command": "rm -rf ~/projects/legacy && git push origin main --force"}),
    ("terminal", {"command": "php artisan migrate --force"}),
    ("terminal", {"command": "ls -la src/"}),
    ("write_file", {"path": "app/Services/Billing.php",
                    "content": "<?php\n\nnamespace App\\Services;\n\nclass Billing {}\n"}),
    ("patch", {"path": "config/app.php", "old_string": "'debug' => true",
               "new_string": "'debug' => false"}),
    ("read_file", {"path": "README.md"}),
]


def main() -> int:
    line = "─" * 78
    print(line)
    print("dry-run — what would happen, without letting it happen")
    print(line)

    for tool, args in CASES:
        plan = sim.classify(tool, args)
        if plan.risk == sim.RISK_SAFE:
            print(f"\n{tool}  →  SAFE — executes normally, nothing to report")
            continue
        print()
        print(sim.render(plan))

    print(line)
    print("Reads pass through. Anything that changes state is reported, tiered, and skipped.")
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
