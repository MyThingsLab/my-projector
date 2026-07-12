from __future__ import annotations

from pathlib import Path

import pytest
from mythings.ledger import Ledger, LedgerEntry
from mythings.projects import ProjectField, ProjectItem

from conftest import (
    FakeGh,
    FakeProjects,
    ScriptedEngine,
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
    engine = ScriptedEngine('{"last_step": "merged #5", "next_step": "all shipped"}')

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
    engine = ScriptedEngine()

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


def test_unattended_ci_with_ask_channel_lets_a_human_approve_the_checklist_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The proof case for fleet-dispatch#40: with MYTHINGS_ASK_CMD wired to a human
    # who says yes (exit 0), the tracking-issue-edit ASK must resolve to ALLOW even
    # though the run is unattended — the ask channel, not just `.under()`, has to
    # decide. `true` stands in for `mytelegrambot ask`.
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("MYTHINGS_ASK_CMD", "true")
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

    assert result.checklist_items_checked == 1  # human said yes: checklist edit happened
    assert any(c[:2] == ["issue", "edit"] for c in gh.calls)


def test_card_matches_via_linked_repo_not_title(tmp_path: Path) -> None:
    # A real Issue/PR card has item.repo set directly (unlike a DraftIssue),
    # which short-circuits the title-substring fallback entirely.
    gh = FakeGh(repos=["my-guard"], open_prs={"my-guard": []}, open_issues={"my-guard": []})
    item = ProjectItem(
        id="ITEM_1",
        content_type="Issue",
        title="a title that says nothing about any repo",
        repo="my-guard",
        fields={"Fleet Status": "In Progress"},
    )
    projects = FakeProjects(
        items=[item],
        fields=[status_field(), *text_fields()],
    )
    ledger = Ledger(tmp_path / "ledger.jsonl")

    result = Projector(
        org="MyThingsLab", project_number=1, ledger=ledger, projects=projects, runner=gh
    ).sync()

    assert result.cards_updated == 1
    assert projects.single_select == [("ITEM_1", "F_STATUS", "opt_ship")]


def test_card_with_unmatched_repo_is_skipped(tmp_path: Path) -> None:
    # A card whose title/repo don't match any tracked repo has no activity to
    # sync against and must be left untouched, not crash.
    gh = FakeGh(repos=["my-guard"], open_prs={"my-guard": []}, open_issues={"my-guard": []})
    projects = FakeProjects(
        items=[card("ITEM_1", "totally unrelated title", status="In Progress")],
        fields=[status_field(), *text_fields()],
    )
    ledger = Ledger(tmp_path / "ledger.jsonl")

    result = Projector(
        org="MyThingsLab", project_number=1, ledger=ledger, projects=projects, runner=gh
    ).sync()

    assert result.cards_updated == 0
    assert projects.single_select == []


def test_status_change_with_no_matching_option_id_is_skipped(tmp_path: Path) -> None:
    # The board's Fleet Status field is missing the "Shipped" option entirely
    # (e.g. project misconfiguration) -> option_id() returns None, so the field
    # write is skipped rather than crashing.
    gh = FakeGh(repos=["my-guard"], open_prs={"my-guard": []}, open_issues={"my-guard": []})
    status_no_shipped = ProjectField(
        id="F_STATUS",
        name="Fleet Status",
        options={"In Progress": "opt_prog", "Blocked": "opt_block"},
    )
    projects = FakeProjects(
        items=[card("ITEM_1", "my-guard", status="In Progress")],
        fields=[status_no_shipped, *text_fields()],
    )
    ledger = Ledger(tmp_path / "ledger.jsonl")

    result = Projector(
        org="MyThingsLab", project_number=1, ledger=ledger, projects=projects, runner=gh
    ).sync()

    assert result.cards_updated == 0
    assert projects.single_select == []


def test_summary_skips_field_already_matching_and_missing_field(tmp_path: Path) -> None:
    # "Last step" is already the text the template would produce (skipped as a
    # no-op write), and "Next step" isn't a field on the board at all (skipped
    # because fields.get() is None) -- neither should be written.
    gh = FakeGh(
        repos=["my-guard"],
        open_prs={"my-guard": []},
        open_issues={"my-guard": []},
        merged={"my-guard": [{"number": 5, "title": "ship", "mergedAt": "2026-07-07T10:00:00Z"}]},
    )
    projects = FakeProjects(
        items=[
            card(
                "ITEM_1",
                "my-guard",
                status="In Progress",
                **{"Last step": "1 PRs merged: #5"},
            )
        ],
        fields=[status_field(), ProjectField(id="F_LAST", name="Last step")],
    )
    ledger = Ledger(tmp_path / "ledger.jsonl")

    result = Projector(
        org="MyThingsLab", project_number=1, ledger=ledger, projects=projects, runner=gh
    ).sync()

    assert result.cards_updated == 1  # status still moves to Shipped
    assert projects.text == []


def test_unusable_engine_reply_falls_back_to_template(tmp_path: Path) -> None:
    # A malformed (non-JSON) engine reply must not blow up the sync -- it falls
    # back to the deterministic template summary.
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
    engine = ScriptedEngine("not json at all")

    result = Projector(
        org="MyThingsLab",
        project_number=1,
        ledger=ledger,
        projects=projects,
        runner=gh,
        engine=engine,
    ).sync()

    assert result.cards_updated == 1
    assert ("ITEM_1", "F_LAST", "1 PRs merged: #5") in projects.text
    assert ("ITEM_1", "F_NEXT", "no open PRs/issues remain") in projects.text


def test_template_summary_reports_closed_issues(tmp_path: Path) -> None:
    # _template_summary's "issues closed" branch, exercised via a closed issue
    # (as opposed to a merged PR) event.
    gh = FakeGh(
        repos=["my-guard"],
        open_prs={"my-guard": []},
        open_issues={"my-guard": []},
        closed={"my-guard": [{"number": 7, "title": "stale", "closedAt": "2026-07-07T10:00:00Z"}]},
    )
    projects = FakeProjects(
        items=[card("ITEM_1", "my-guard", status="In Progress")],
        fields=[status_field(), *text_fields()],
    )
    ledger = Ledger(tmp_path / "ledger.jsonl")

    Projector(
        org="MyThingsLab", project_number=1, ledger=ledger, projects=projects, runner=gh
    ).sync()

    assert ("ITEM_1", "F_LAST", "1 issues closed: #7") in projects.text


def test_apply_checklist_with_no_matching_refs_checks_nothing(tmp_path: Path) -> None:
    # The tracking issue's checklist names a PR/issue that never closed --
    # checklist_updates() finds nothing to check, so no issue edit is issued.
    body = "## Checklist\n- [ ] ship my-guard#99\n"
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

    assert result.checklist_items_checked == 0
    assert not any(c[:2] == ["issue", "edit"] for c in gh.calls)


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


def test_match_repo_title_fallback_requires_word_boundary(tmp_path: Path) -> None:
    # Regression for #10: a DraftIssue card (no item.repo) whose title embeds a
    # repo name inside a longer hyphenated word must NOT match that repo — only
    # a standalone token match counts.
    gh = FakeGh(
        repos=["my-server", "my-serverless"],
        open_prs={"my-server": [], "my-serverless": [{"number": 1}]},
        open_issues={"my-server": [], "my-serverless": []},
    )
    projects = FakeProjects(
        items=[
            card(
                "ITEM_1",
                "Wire retries into my-serverless health checks",
                status="In Progress",
                content_type="DraftIssue",
            )
        ],
        fields=[status_field(), *text_fields()],
    )
    ledger = Ledger(tmp_path / "ledger.jsonl")

    result = Projector(
        org="MyThingsLab", project_number=1, ledger=ledger, projects=projects, runner=gh
    ).sync()

    # my-serverless still has an open PR => stays In Progress => no status write.
    # Before the fix this card matched "my-server" (0 open) and got moved to
    # Shipped, silently attributing the wrong repo's activity to the card.
    assert result.cards_updated == 0
    assert projects.single_select == []


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
