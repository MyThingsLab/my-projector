from __future__ import annotations

import json

import pytest
from mythings.engine import EngineRequest, EngineResult
from mythings.projects import ProjectField, ProjectItem


@pytest.fixture(autouse=True)
def _attended_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Default the suite to the attended path. CI sets GITHUB_ACTIONS=true, which
    # collapses the public tracking-issue-edit ASK to DENY (fail-closed) and
    # suppresses the checklist edit — a real behavior tests must opt into, not
    # inherit from the runner's env. (Private board field writes are unaffected.)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


class FakeGh:
    # Mocks the `gh` process boundary for the REST-ish calls MyProjector makes:
    # repo list, open PR/issue counts, merged/closed history, tracking-issue
    # view/edit. argv is everything after `gh`.
    def __init__(
        self,
        *,
        repos: list[str],
        open_prs: dict[str, list] | None = None,
        open_issues: dict[str, list] | None = None,
        merged: dict[str, list] | None = None,
        closed: dict[str, list] | None = None,
        issue_body: str = "",
    ) -> None:
        self.repos = repos
        self.open_prs = open_prs or {}
        self.open_issues = open_issues or {}
        self.merged = merged or {}
        self.closed = closed or {}
        self.issue_body = issue_body
        self.calls: list[list[str]] = []

    def _repo(self, argv: list[str]) -> str:
        return argv[argv.index("--repo") + 1].split("/", 1)[1]

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        if argv[:2] == ["repo", "list"]:
            return json.dumps([{"name": r} for r in self.repos])
        state = argv[argv.index("--state") + 1] if "--state" in argv else ""
        repo = self._repo(argv)
        if argv[0] == "pr" and argv[1] == "list":
            hits = self.open_prs.get(repo, []) if state == "open" else self.merged.get(repo, [])
            return json.dumps(hits)
        if argv[0] == "issue" and argv[1] == "list":
            hits = self.open_issues.get(repo, []) if state == "open" else self.closed.get(repo, [])
            return json.dumps(hits)
        if argv[:2] == ["issue", "view"]:
            return self.issue_body
        if argv[:2] == ["issue", "edit"]:
            return ""
        raise AssertionError(f"unexpected gh call: {argv}")


class FakeProjects:
    def __init__(
        self, items: list[ProjectItem], fields: list[ProjectField], pid: str = "PVT_x"
    ) -> None:
        self._items = items
        self._fields = fields
        self._pid = pid
        self.single_select: list[tuple] = []
        self.text: list[tuple] = []

    def project_id(self, org: str, number: int) -> str:
        return self._pid

    def items(self, project_id: str) -> list[ProjectItem]:
        return self._items

    def fields(self, project_id: str) -> list[ProjectField]:
        return self._fields

    def set_single_select(self, project_id, item_id, field_id, option_id) -> None:
        self.single_select.append((item_id, field_id, option_id))

    def set_text_field(self, project_id, item_id, field_id, text) -> None:
        self.text.append((item_id, field_id, text))


class SpyEngine:
    def __init__(self, result: EngineResult | None = None) -> None:
        self.calls: list[EngineRequest] = []
        self.result = result or EngineResult(text="", data={})

    def run(self, request: EngineRequest) -> EngineResult:
        self.calls.append(request)
        return self.result


def status_field() -> ProjectField:
    return ProjectField(
        id="F_STATUS",
        name="Fleet Status",
        options={
            "Shipped": "opt_ship",
            "In Progress": "opt_prog",
            "Blocked": "opt_block",
            "Design Only": "opt_design",
        },
    )


def text_fields() -> list[ProjectField]:
    return [
        ProjectField(id="F_LAST", name="Last step"),
        ProjectField(id="F_NEXT", name="Next step"),
    ]


def card(
    item_id: str,
    title: str,
    *,
    status: str = "In Progress",
    content_type: str = "Issue",
    **fields,
) -> ProjectItem:
    values = {"Fleet Status": status, **fields}
    return ProjectItem(id=item_id, content_type=content_type, title=title, fields=values)
