from __future__ import annotations

from conftest import FakeGh
from myprojector.sources import repo_activity


def test_repo_activity_includes_closed_issue_events() -> None:
    gh = FakeGh(
        repos=["my-guard"],
        open_prs={"my-guard": []},
        open_issues={"my-guard": []},
        closed={"my-guard": [{"number": 7, "title": "stale", "closedAt": "2026-07-07T10:00:00Z"}]},
    )

    act = repo_activity(gh, "MyThingsLab", "my-guard", since="")

    assert act.events == [("issue", 7, "stale", "closed")] or (
        act.events[0].kind == "issue"
        and act.events[0].number == 7
        and act.events[0].action == "closed"
    )


def test_repo_activity_skips_pr_and_issue_missing_a_timestamp() -> None:
    # A merged/closed item with no mergedAt/closedAt (e.g. a GraphQL quirk or a
    # still-open item returned by mistake) must not be treated as "after since".
    gh = FakeGh(
        repos=["my-guard"],
        open_prs={"my-guard": []},
        open_issues={"my-guard": []},
        merged={"my-guard": [{"number": 5, "title": "ship"}]},  # no mergedAt
        closed={"my-guard": [{"number": 7, "title": "stale"}]},  # no closedAt
    )

    act = repo_activity(gh, "MyThingsLab", "my-guard", since="2026-01-01T00:00:00Z")

    assert act.events == []
