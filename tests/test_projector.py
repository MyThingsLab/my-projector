from __future__ import annotations

from pathlib import Path

import pytest
from mythings.engine import EngineResult
from mythings.ledger import Ledger, LedgerEntry

from conftest import (
    FakeGh,
    FakeProjects,
    SpyEngine,
    card,
    status_field,
    text_fields,
)
from myprojector.projector import Projector, Tracking


def test_happy_path_moves_to_shipped_and_updates_summary(tmp_path: Path) -> None:
    # my-guard's only open PR just merged: 0 open now => Shipped, and the two
    # summary text fields get rewritten from the merge event.
    gh = FakeGh(
        repos=["my-guard"],
        open_prs={"my-guard": []},
        open_issues={"my-guard": []},
        merged={"my-guard": [{"number": 5, "title": "ship", "mergedAt": "2026-07-07T10:00:00Z"}]},
    )
    projects = FakeProjects(
        items=[card("ITEM_1", "my-guard", status="In Progress")],
        fields=[status_field(), *text_fields()],
    )
    ledger = Ledger(tmp_path / "ledger.jsonl")
    engine = SpyEngine(EngineResult(text='{"last_step": "merged #5", "next_step": "all shipped"}'))

    result = Projector(
        org="MyThingsLab",
        project_number=1,
        ledger=ledger,
        projects=projects,
        runner=gh,
        engine=engine,
    ).sync()

    assert result.cards_updated == 1
    assert result.outcome == "success"
    assert projects.single_select == [("ITEM_1", "F_STATUS", "opt_ship")]
    assert len(engine.calls) == 1  # one summarize call, because there was an event
    assert ("ITEM_1", "F_LAST", "merged #5") in projects.text
    assert ("ITEM_1", "F_NEXT", "all shipped") in projects.text

    entry = ledger.read(kind="project-sync")[-1]
    assert entry.outcome == "success"
    assert entry.data["cards_updated"] == 1
    assert entry.data["item_types"] == {"ITEM_1": "Issue"}


def test_zero_events_skips_engine_and_summary(tmp_path: Path) -> None:
    # Still In Progress (an open PR remains), no merge/close events => no Engine
    # call at all, and no status change.
    gh = FakeGh(
        repos=["my-guard"],
        open_prs={"my-guard": [{"number": 9}]},
        open_issues={"my-guard": []},
    )
    projects = FakeProjects(
        items=[card("ITEM_1", "my-guard", status="In Progress")],
        fields=[status_field(), *text_fields()],
    )
    ledger = Ledger(tmp_path / "ledger.jsonl")
    engine = SpyEngine()

    result = Projector(
        org="MyThingsLab",
        project_number=1,
        ledger=ledger,
        projects=projects,
        runner=gh,
        engine=engine,
    ).sync()

    assert engine.calls == []
    assert result.cards_updated == 0
    assert projects.single_select == []
    assert projects.text == []


def test_drift_is_flagged_and_card_not_mutated(tmp_path: Path) -> None:
    # A prior sync recorded ITEM_1 as a DraftIssue; it's now a real Issue.
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        LedgerEntry(
            tool="myprojector",
            kind="project-sync",
            outcome="success",
            data={"item_types": {"ITEM_1": "DraftIssue"}},
        )
    )
    gh = FakeGh(repos=["my-guard"], open_prs={"my-guard": []}, open_issues={"my-guard": []})
    projects = FakeProjects(
        items=[card("ITEM_1", "my-guard", status="In Progress", content_type="Issue")],
        fields=[status_field(), *text_fields()],
    )

    result = Projector(
        org="MyThingsLab", project_number=1, ledger=ledger, projects=projects, runner=gh
    ).sync()

    assert result.drift == ["ITEM_1"]
    assert result.outcome == "drift_found"
    assert projects.single_select == []  # drifted card is never mutated
    assert ledger.read(kind="project-sync")[-1].outcome == "drift_found"


def test_human_set_blocked_not_overridden_without_force(tmp_path: Path) -> None:
    gh = FakeGh(repos=["my-guard"], open_prs={"my-guard": []}, open_issues={"my-guard": []})
    projects = FakeProjects(
        items=[card("ITEM_1", "my-guard", status="Blocked")],
        fields=[status_field(), *text_fields()],
    )
    ledger = Ledger(tmp_path / "ledger.jsonl")

    result = Projector(
        org="MyThingsLab", project_number=1, ledger=ledger, projects=projects, runner=gh
    ).sync()

    assert result.cards_updated == 0
    assert projects.single_select == []


def test_force_overrides_blocked(tmp_path: Path) -> None:
    gh = FakeGh(repos=["my-guard"], open_prs={"my-guard": []}, open_issues={"my-guard": []})
    projects = FakeProjects(
        items=[card("ITEM_1", "my-guard", status="Blocked")],
        fields=[status_field(), *text_fields()],
    )
    ledger = Ledger(tmp_path / "ledger.jsonl")

    result = Projector(
        org="MyThingsLab", project_number=1, ledger=ledger, projects=projects, runner=gh
    ).sync(force=True)

    assert result.cards_updated == 1
    assert projects.single_select == [("ITEM_1", "F_STATUS", "opt_ship")]


def test_apply_checklist_checks_named_refs(tmp_path: Path) -> None:
    body = "## Checklist\n- [ ] ship my-guard#5\n- [ ] free text line\n- [x] my-guard#2 done\n"
    gh = FakeGh(
        repos=["my-guard"],
        open_prs={"my-guard": []},
        open_issues={"my-guard": []},
        merged={"my-guard": [{"number": 5, "title": "ship", "mergedAt": "2026-07-07T10:00:00Z"}]},
        issue_body=body,
    )
    projects = FakeProjects(
        items=[card("ITEM_1", "my-guard", status="In Progress")],
        fields=[status_field(), *text_fields()],
    )
    ledger = Ledger(tmp_path / "ledger.jsonl")

    result = Projector(
        org="MyThingsLab", project_number=1, ledger=ledger, projects=projects, runner=gh
    ).sync(apply_checklist=True, tracking=Tracking(repo="MyThingsLab/my-things-core", issue=1))

    assert result.checklist_items_checked == 1
    edit = [c for c in gh.calls if c[:2] == ["issue", "edit"]][0]
    new_body = edit[edit.index("--body") + 1]
    assert "- [x] ship my-guard#5" in new_body
    assert "- [ ] free text line" in new_body  # free text untouched


def test_unattended_ci_suppresses_public_checklist_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # In CI (unattended) the public tracking-issue edit fail-closes: the checklist
    # is not checked and no issue edit is made, while the private board sync still
    # runs (card field writes are not public-content-gated).
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    body = "## Checklist\n- [ ] ship my-guard#5\n"
    gh = FakeGh(
        repos=["my-guard"],
        open_prs={"my-guard": []},
        open_issues={"my-guard": []},
        merged={"my-guard": [{"number": 5, "title": "ship", "mergedAt": "2026-07-07T10:00:00Z"}]},
        issue_body=body,
    )
    projects = FakeProjects(
        items=[card("ITEM_1", "my-guard", status="In Progress")],
        fields=[status_field(), *text_fields()],
    )
    result = Projector(
        org="MyThingsLab",
        project_number=1,
        ledger=Ledger(tmp_path / "ledger.jsonl"),
        projects=projects,
        runner=gh,
    ).sync(apply_checklist=True, tracking=Tracking(repo="MyThingsLab/my-things-core", issue=1))

    assert result.checklist_items_checked == 0  # fail-closed on public content
    assert not any(c[:2] == ["issue", "edit"] for c in gh.calls)
    assert result.cards_updated == 1  # private board sync still happens


def test_dry_run_makes_no_edits(tmp_path: Path) -> None:
    gh = FakeGh(
        repos=["my-guard"],
        open_prs={"my-guard": []},
        open_issues={"my-guard": []},
        merged={"my-guard": [{"number": 5, "title": "ship", "mergedAt": "2026-07-07T10:00:00Z"}]},
    )
    projects = FakeProjects(
        items=[card("ITEM_1", "my-guard", status="In Progress")],
        fields=[status_field(), *text_fields()],
    )
    ledger = Ledger(tmp_path / "ledger.jsonl")

    result = Projector(
        org="MyThingsLab", project_number=1, ledger=ledger, projects=projects, runner=gh
    ).sync(dry_run=True)

    assert result.cards_updated == 1  # reported as intended...
    assert projects.single_select == []  # ...but nothing actually written
    assert projects.text == []


def test_default_policy_asks_before_public_edit(tmp_path: Path) -> None:
    # Without the --apply-checklist opt-in, the ASK tier means nothing is written
    # even though a ref matches.
    body = "- [ ] my-guard#5\n"
    gh = FakeGh(
        repos=["my-guard"],
        open_prs={"my-guard": []},
        open_issues={"my-guard": []},
        merged={"my-guard": [{"number": 5, "title": "s", "mergedAt": "2026-07-07T10:00:00Z"}]},
        issue_body=body,
    )
    projects = FakeProjects(items=[], fields=[status_field()])
    ledger = Ledger(tmp_path / "ledger.jsonl")

    result = Projector(
        org="MyThingsLab", project_number=1, ledger=ledger, projects=projects, runner=gh
    ).sync(tracking=Tracking(repo="MyThingsLab/my-things-core", issue=1))  # apply_checklist=False

    assert result.checklist_items_checked == 0
    assert not [c for c in gh.calls if c[:2] == ["issue", "edit"]]
