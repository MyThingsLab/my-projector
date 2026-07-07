# my-projector

[![CI](https://github.com/MyThingsLab/my-projector/actions/workflows/ci.yml/badge.svg)](https://github.com/MyThingsLab/my-projector/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/MyThingsLab/my-projector/branch/main/graph/badge.svg)](https://codecov.io/gh/MyThingsLab/my-projector) ![Python](https://img.shields.io/badge/python-3.11%2B-blue) [![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A [MyThingsLab](../mythings-core) `My[X]` tool. It keeps the fleet's GitHub
Project board — and any linked org-wide tracking issue — synced to the **live**
state of every repo, so the dashboard never silently drifts from reality.

Pure bookkeeping: MyProjector makes no priority judgments (that's
MyOrchestrator's and MyPlanner's job). It only reconciles what *already
happened* — merged/closed PRs and issues — into the board's `Fleet Status`
field and a tracking issue's checklist.

## What it does each run

1. Lists every repo under the `MyThingsLab` org.
2. For each repo, counts open PRs/issues and collects merge/close events since
   the last `kind=project-sync` ledger entry (the bookmark window).
3. Reads the Project board's items and field values (via the new
   `mythings.projects` GraphQL contract).
4. Computes a mechanical `Fleet Status` transition — `0 open → Shipped`,
   `≥1 open → In Progress` — **never** overriding a human-set
   `Blocked`/`Design Only` without `--force`.
5. Flags drift (a card whose linked content type changed unexpectedly) instead
   of silently fixing it.
6. Optionally rewrites each changed card's `Last step`/`Next step` prose (the
   single Engine call; deterministic template under `NoopEngine`).
7. With `--apply-checklist`, checks off a linked tracking issue's checklist
   lines that name a now-merged/closed `repo#number`.

## Risk tiers

| Side effect | `Action.kind` | Default |
|---|---|---|
| Board field value edit (private board) | `project-field-edit` | `ALLOW` |
| Tracking-issue checklist edit (public) | `tracking-issue-edit` | `ASK` |
| Closing a public issue | `issue-close` | `ASK` |

## CLI

```bash
myprojector sync --project-number 1 [--repos owner/a,owner/b] [--dry-run] [--json]
myprojector sync --project-number 1 --apply-checklist \
  --tracking-repo MyThingsLab/mythings-core --tracking-issue 1
```

## Install (development)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ../mythings-core -e ".[dev]"
pytest
```

## License

MIT — see [`LICENSE`](LICENSE).
