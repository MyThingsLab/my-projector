from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field

from mythings.engine import Engine, EngineRequest
from mythings.github import Runner, _gh
from mythings.isolation import in_github_actions
from mythings.ledger import Ledger
from mythings.policy import ALLOW, Action, Decision, Policy, PolicyResult
from mythings.projects import ProjectField, ProjectItem, Projects

from myprojector.board import checklist_updates, find_drift, normalize_ref, status_change
from myprojector.sources import RepoActivity, list_repos, repo_activity

STATUS_FIELD = "Fleet Status"
LAST_STEP_FIELD = "Last step"
NEXT_STEP_FIELD = "Next step"

_ENGINE_SYSTEM = (
    "Rewrite a project card's one-line last-step and next-step summary from the "
    "raw PR/issue activity for its repo. Reply with only a JSON object: "
    '{"last_step": "<one line>", "next_step": "<one line>"}, nothing else.'
)


class DefaultPolicy:
    # The two-tier default: editing a private board is trivially reversible and
    # allowed; editing/closing public content is ASK — never touched unprompted.
    def evaluate(self, action: Action) -> PolicyResult:
        if action.kind == "project-field-edit":
            return ALLOW
        if action.kind in ("tracking-issue-edit", "issue-close"):
            return PolicyResult(Decision.ASK, reason="edits public content", rule="public-content")
        return PolicyResult(Decision.ASK, reason="unclassified action", rule="default-deny-ish")


@dataclass
class SyncResult:
    cards_updated: int = 0
    checklist_items_checked: int = 0
    drift: list[str] = field(default_factory=list)

    @property
    def outcome(self) -> str:
        return "drift_found" if self.drift else "success"


@dataclass(frozen=True)
class Tracking:
    repo: str  # "owner/repo"
    issue: int


class Projector:
    def __init__(
        self,
        *,
        org: str,
        project_number: int,
        ledger: Ledger,
        projects: Projects | None = None,
        runner: Runner = _gh,
        engine: Engine | None = None,
        policy: Policy | None = None,
    ) -> None:
        self.org = org
        self.project_number = project_number
        self.ledger = ledger
        self.projects = projects or Projects(runner=runner)
        self.runner = runner
        self.engine = engine
        self.policy: Policy = policy or DefaultPolicy()

    def sync(
        self,
        *,
        repos: list[str] | None = None,
        dry_run: bool = False,
        force: bool = False,
        apply_checklist: bool = False,
        tracking: Tracking | None = None,
    ) -> SyncResult:
        since, prior_types = self._bookmark()
        repo_names = repos if repos is not None else list_repos(self.runner, self.org)
        activity = {r: repo_activity(self.runner, self.org, r, since=since) for r in repo_names}

        project_id = self.projects.project_id(self.org, self.project_number)
        items = self.projects.items(project_id)
        fields = {f.name: f for f in self.projects.fields(project_id)}
        drift = find_drift(items, prior_types)

        result = SyncResult(drift=drift)
        for item in items:
            if item.id in drift:
                continue  # flag, never mutate a drifted card
            repo = self._match_repo(item, repo_names)
            act = activity.get(repo) if repo else None
            if act is None:
                continue
            if self._sync_status(project_id, item, act, fields, force=force, dry_run=dry_run):
                result.cards_updated += 1
            if act.has_events:
                self._sync_summary(project_id, item, act, fields, dry_run=dry_run)

        if apply_checklist and tracking is not None:
            result.checklist_items_checked = self._sync_checklist(
                tracking, activity, dry_run=dry_run
            )

        self._record(result, items)
        return result

    def _bookmark(self) -> tuple[str, dict[str, str]]:
        entries = self.ledger.read(tool="myprojector", kind="project-sync")
        if not entries:
            return "", {}
        last = entries[-1]
        return last.ts, dict(last.data.get("item_types", {}))

    def _match_repo(self, item: ProjectItem, repos: list[str]) -> str | None:
        if item.repo and item.repo in repos:
            return item.repo
        low = item.title.lower()
        for repo in repos:
            if repo.lower() in low:
                return repo
        return None

    def _sync_status(
        self,
        project_id: str,
        item: ProjectItem,
        act: RepoActivity,
        fields: dict[str, ProjectField],
        *,
        force: bool,
        dry_run: bool,
    ) -> bool:
        target = status_change(item.fields.get(STATUS_FIELD, ""), act.open_count, force=force)
        fld = fields.get(STATUS_FIELD)
        if target is None or fld is None:
            return False
        option_id = fld.option_id(target)
        if option_id is None:
            return False
        return self._guarded(
            "project-field-edit",
            {"item": item.id, "field": STATUS_FIELD, "value": target},
            lambda: self.projects.set_single_select(project_id, item.id, fld.id, option_id),
            dry_run=dry_run,
        )

    def _sync_summary(
        self,
        project_id: str,
        item: ProjectItem,
        act: RepoActivity,
        fields: dict[str, ProjectField],
        *,
        dry_run: bool,
    ) -> None:
        last_step, next_step = self._summarize(item, act)
        for name, text in ((LAST_STEP_FIELD, last_step), (NEXT_STEP_FIELD, next_step)):
            fld = fields.get(name)
            if fld is None or item.fields.get(name, "") == text:
                continue
            self._guarded(
                "project-field-edit",
                {"item": item.id, "field": name, "value": text},
                lambda f=fld, t=text: self.projects.set_text_field(project_id, item.id, f.id, t),
                dry_run=dry_run,
            )

    def _summarize(self, item: ProjectItem, act: RepoActivity) -> tuple[str, str]:
        last_step, next_step = _template_summary(act)
        if self.engine is None:
            return last_step, next_step
        result = self.engine.run(
            EngineRequest(
                prompt=json.dumps(
                    {
                        "repo": act.repo,
                        "prior_last_step": item.fields.get(LAST_STEP_FIELD, ""),
                        "prior_next_step": item.fields.get(NEXT_STEP_FIELD, ""),
                        "events": [e.__dict__ for e in act.events],
                    }
                ),
                system=_ENGINE_SYSTEM,
                context={"repo": act.repo},
            )
        )
        try:
            obj = json.loads(result.text) if result.text else {}
        except json.JSONDecodeError:
            obj = {}
        ls, ns = obj.get("last_step"), obj.get("next_step")
        if isinstance(ls, str) and isinstance(ns, str):
            return ls, ns
        return last_step, next_step  # unusable reply => honest deterministic template

    def _sync_checklist(
        self, tracking: Tracking, activity: dict[str, RepoActivity], *, dry_run: bool
    ) -> int:
        closed_refs: set[str] = set()
        for act in activity.values():
            for event in act.events:
                closed_refs |= normalize_ref(f"{self.org}/{act.repo}", event.number)
        body = self.runner(["issue", "view", str(tracking.issue), "--repo", tracking.repo,
                            "--json", "body", "-q", ".body"])
        new_body, checked = checklist_updates(body, closed_refs)
        if checked == 0:
            return 0
        argv = ["issue", "edit", str(tracking.issue), "--repo", tracking.repo, "--body", new_body]
        # --apply-checklist is the human's explicit opt-in, which is what satisfies
        # this action's ASK tier — but only when attended: under(unattended) still
        # collapses ASK to DENY in CI, so public content is never edited headless.
        applied = self._guarded(
            "tracking-issue-edit",
            {"repo": tracking.repo, "issue": tracking.issue, "command": "gh " + shlex.join(argv)},
            lambda: self.runner(argv),
            dry_run=dry_run,
            confirmed=True,
        )
        return checked if applied else 0

    def _guarded(
        self, kind: str, payload: dict, thunk, *, dry_run: bool, confirmed: bool = False
    ) -> bool:
        if dry_run:
            return True  # reports the intended change; makes no call
        decision = self.policy.evaluate(Action(kind=kind, payload=payload)).under(
            unattended=in_github_actions()
        )
        if decision is Decision.ALLOW or (decision is Decision.ASK and confirmed):
            thunk()
            return True
        return False

    def _record(self, result: SyncResult, items: list[ProjectItem]) -> None:
        self.ledger.record(
            tool="myprojector",
            kind="project-sync",
            outcome=result.outcome,
            detail=(
                f"synced {result.cards_updated} cards, "
                f"checked {result.checklist_items_checked} checklist items"
            ),
            cards_updated=result.cards_updated,
            checklist_items_checked=result.checklist_items_checked,
            drift=result.drift,
            item_types={item.id: item.content_type for item in items},
        )


def _template_summary(act: RepoActivity) -> tuple[str, str]:
    merged = [f"#{e.number}" for e in act.events if e.action == "merged"]
    closed = [f"#{e.number}" for e in act.events if e.action == "closed"]
    parts = []
    if merged:
        parts.append(f"{len(merged)} PRs merged: {', '.join(merged)}")
    if closed:
        parts.append(f"{len(closed)} issues closed: {', '.join(closed)}")
    last_step = "; ".join(parts) or "no new activity"
    next_step = (
        "no open PRs/issues remain"
        if act.open_count == 0
        else f"{act.open_count} open PRs/issues remain"
    )
    return last_step, next_step
