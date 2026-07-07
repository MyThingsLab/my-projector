from __future__ import annotations

import json
from dataclasses import dataclass, field

from mythings.github import Runner

# Fleet reads go through the same `gh` process boundary the core `github` contract
# uses (an injected Runner), so tests mock only that. Core `github.GitHub` still
# has no repo-listing or PR-event exposure, so we query the runner directly here
# in the same thin-wrapper style as MyOrchestrator's sources.


@dataclass(frozen=True)
class Event:
    kind: str  # "pr" | "issue"
    number: int
    title: str
    action: str  # "merged" | "closed"


@dataclass(frozen=True)
class RepoActivity:
    repo: str  # short name
    open_count: int
    events: list[Event] = field(default_factory=list)

    @property
    def has_events(self) -> bool:
        return bool(self.events)


def list_repos(runner: Runner, org: str, *, limit: int = 1000) -> list[str]:
    raw = json.loads(runner(["repo", "list", org, "--json", "name", "--limit", str(limit)]))
    return [obj["name"] for obj in raw]


def _open_count(runner: Runner, slug: str) -> int:
    total = 0
    for kind in ("pr", "issue"):
        argv = [kind, "list", "--repo", slug, "--state", "open",
                "--limit", "100", "--json", "number"]
        total += len(json.loads(runner(argv)))
    return total


def repo_activity(runner: Runner, org: str, repo: str, *, since: str) -> RepoActivity:
    slug = f"{org}/{repo}"
    events: list[Event] = []
    for pr in json.loads(
        runner(["pr", "list", "--repo", slug, "--state", "merged", "--limit", "50",
                "--json", "number,title,mergedAt"])
    ):
        if _after(pr.get("mergedAt"), since):
            events.append(Event("pr", pr["number"], pr.get("title", ""), "merged"))
    for issue in json.loads(
        runner(["issue", "list", "--repo", slug, "--state", "closed", "--limit", "50",
                "--json", "number,title,closedAt"])
    ):
        if _after(issue.get("closedAt"), since):
            events.append(Event("issue", issue["number"], issue.get("title", ""), "closed"))
    return RepoActivity(repo=repo, open_count=_open_count(runner, slug), events=events)


def _after(ts: str | None, since: str) -> bool:
    # `since` empty (first-ever run) => everything counts. Otherwise ISO-8601
    # strings compare lexicographically, so no datetime parsing is needed.
    if not ts:
        return False
    return not since or ts > since
