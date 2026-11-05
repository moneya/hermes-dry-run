# dry-run

See what an agent would do, without letting it do it.

A `tool_execution` middleware for [Hermes Agent](https://github.com/NousResearch/hermes-agent).
In dry-run mode it **does not call the tool at all** — it returns a report describing what
the call would have done and how much damage it could cause.

```
DRY RUN — NOT EXECUTED
==========================================================================
tool      : terminal
risk      : IRREVERSIBLE  (cannot be undone)
why       : recursive force delete; force-push rewrites remote history
command   : rm -rf build/legacy && git push origin main --force
--------------------------------------------------------------------------
Nothing was executed. No file was written, no command ran, nothing was sent.
```

## Why

Approvals catch one call at a time. There is nothing that shows you a whole session before
it happens — so the first time you learn an agent's plan is when it is already carrying it
out.

Dry run inverts that. The agent does its work, produces its plan, and nothing leaves a mark.
You read the report, decide, then run it for real.

## Install

```bash
git clone https://github.com/moneya/hermes-dry-run.git ~/.hermes/plugins/dry-run
hermes plugins enable dry-run
```

No dependencies. No network. Pure Python standard library.

## Modes

Set in `dry_run.json` (created on first use), by `/dryrun`, or per-run with `DRY_RUN_MODE`.

| Mode | Behaviour |
|---|---|
| `off` | Everything executes normally. |
| `guard` | Default. Anything that changes state is simulated; reads pass through. |
| `all` | Everything is simulated, including reads. |

Reads always pass in `guard` mode — a dry run that cannot read files or search the web
cannot produce a plan worth reviewing.

## Risk tiers

Not a binary block. The report says what the call would actually do:

| Tier | Meaning | Examples |
|---|---|---|
| `SAFE` | Read-only. | `read_file`, `search_files`, `web_search` |
| `WRITE` | Recoverable if you have the previous state. | `git commit`, migrations, `chmod`, file writes |
| `DESTRUCTIVE` | May be recoverable, may not. | `sudo`, `systemctl stop`, `curl -X DELETE` |
| `IRREVERSIBLE` | Cannot be undone. | `rm -rf`, `git push --force`, `DROP TABLE`, `npm publish`, `terraform apply` |

The tier comes from the command itself, so `rm -rf` and `git push --force` are flagged even
when they arrive inside a longer shell line.

## Turning it off

Two routes, and they differ on purpose.

**The human** sends `/dryrun off`. Immediate, no questions.

**The agent** calls `dryrun_control` with `action="off"` and `approved_by` quoting what the
human actually said:

```json
{"action": "off", "approved_by": "user: 'turn it off now'"}
```

Without that field the call is **refused**. Every change is announced in the transcript and
written to the audit log with a timestamp, the previous mode, and the stated approval:

```
DRY-RUN IS NOW OFF — tools execute for real from the next call.
  changed : guard -> off
  approved: user: 'turn it off now'
  at      : 2026-11-05T06:41:02+00:00
This is recorded in the audit log. Send /dryrun on to restore protection.
```

Turning dry-run **on** never needs approval — it is the safe direction.

## What this does not do

Be clear-eyed about the boundary.

- **`approved_by` is evidence, not proof.** An agent could claim approval no human gave.
  What the plugin guarantees is that every disable is visible in the transcript and durable
  in the log, so a human can spot it and reverse it with `/dryrun on`. It is an evidentiary
  control, not a security boundary. If you need a hard boundary, use `/dryrun off` and keep
  the agent out of `dryrun_control` by removing it from `exempt_tools`.
- **It cannot promise a real run matches the plan.** The simulation reports intent, not
  outcome. A command that behaves differently once executed will do so.
- **It does not make an agent trustworthy.** It makes one *reviewable*. Those are different
  things, and the second is what you can actually have today.
- **Classification is pattern-based.** A destructive command phrased in a way no rule
  anticipates lands in `WRITE`, not `IRREVERSIBLE`. The tiers are a reading aid, not a
  guarantee.

## Files

```
dry-run/
  plugin.yaml          manifest
  __init__.py          middleware, dryrun_control, dry_run_report, /dryrun
  dryrun_simulate.py   risk classification and report rendering
  dryrun_settings.py   config, audit log, approval gate
  tests/
    test_dryrun.py           22 unit tests
    test_host_integration.py through Hermes' real middleware dispatch
```

Sibling modules are loaded by explicit file path under a unique prefix. Importing them by
bare name (`import settings`) is a trap: another plugin with a `settings.py` shares a single
entry in `sys.modules`, and whichever loads first silently wins for the whole process.

## Tests

```bash
python3 tests/test_dryrun.py           # 22 passed
python3 tests/test_host_integration.py # 8 checks, VERDICT: PASS
hermes plugins validate .              # security scan — safe
```

The load-bearing test is `test_simulated_tool_never_executes`: the downstream tool must not
be called. Everything else is wording.

## Licence

MIT
