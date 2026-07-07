from __future__ import annotations

import argparse
import json
from pathlib import Path

from mythings.engine import ClaudeCLIEngine, Engine
from mythings.ledger import Ledger

from myprojector.projector import Projector, SyncResult, Tracking

_ENGINE_NAMES = ("noop", "claude-cli")


def build_engine(name: str, *, model: str | None = None) -> Engine | None:
    # noop -> None so summaries fall back to the deterministic template with no
    # model call at all, rather than a no-op reply.
    if name == "claude-cli":
        return ClaudeCLIEngine(model=model)
    return None


def _render(result: SyncResult, *, as_json: bool) -> str:
    if as_json:
        return json.dumps(
            {
                "cards_updated": result.cards_updated,
                "checklist_items_checked": result.checklist_items_checked,
                "drift": result.drift,
                "outcome": result.outcome,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    lines = [
        f"synced {result.cards_updated} cards, "
        f"checked {result.checklist_items_checked} checklist items"
    ]
    if result.drift:
        lines.append(f"drift flagged (not touched): {', '.join(result.drift)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="myprojector",
        description="Sync the fleet's GitHub Project board to live repo state.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sync = sub.add_parser("sync", help="reconcile the board with live repo state")
    sync.add_argument("--org", default="MyThingsLab")
    sync.add_argument("--project-number", type=int, required=True, help="the org ProjectV2 number")
    sync.add_argument("--repos", help="comma-separated repo short names (default: all org repos)")
    sync.add_argument("--dry-run", action="store_true", help="report changes, make no edits")
    sync.add_argument(
        "--force", action="store_true", help="override a human-set Blocked/Design Only status"
    )
    sync.add_argument("--json", action="store_true", help="machine-readable output")
    sync.add_argument("--ledger", type=Path, default=Path(".mythings/ledger.jsonl"))
    sync.add_argument(
        "--apply-checklist",
        action="store_true",
        help="also check off a tracking issue's checklist (the ASK-tier public edit)",
    )
    sync.add_argument("--tracking-repo", help='tracking issue repo, e.g. "MyThingsLab/core"')
    sync.add_argument("--tracking-issue", type=int, help="tracking issue number")
    sync.add_argument("--engine", choices=sorted(_ENGINE_NAMES), default="noop")
    sync.add_argument("--engine-model", help="model for --engine claude-cli")

    args = parser.parse_args(argv)
    if args.apply_checklist and not (args.tracking_repo and args.tracking_issue):
        parser.error("--apply-checklist needs --tracking-repo and --tracking-issue")

    tracking = (
        Tracking(repo=args.tracking_repo, issue=args.tracking_issue)
        if args.tracking_repo and args.tracking_issue
        else None
    )
    projector = Projector(
        org=args.org,
        project_number=args.project_number,
        ledger=Ledger(args.ledger),
        engine=build_engine(args.engine, model=args.engine_model),
    )
    result = projector.sync(
        repos=args.repos.split(",") if args.repos else None,
        dry_run=args.dry_run,
        force=args.force,
        apply_checklist=args.apply_checklist,
        tracking=tracking,
    )
    print(_render(result, as_json=args.json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
