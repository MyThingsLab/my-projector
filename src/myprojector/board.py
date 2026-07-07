from __future__ import annotations

import re

from mythings.projects import ProjectItem

# Human-set statuses MyProjector must never override without --force: they encode
# a decision a person made, not a mechanical fact about open PRs.
PROTECTED_STATUSES = ("Blocked", "Design Only")

SHIPPED = "Shipped"
IN_PROGRESS = "In Progress"


def target_status(open_count: int) -> str:
    # The fixed lattice from the design doc: nothing open means the work shipped.
    return SHIPPED if open_count == 0 else IN_PROGRESS


def status_change(current: str, open_count: int, *, force: bool) -> str | None:
    # Returns the option name to set, or None when nothing should change (already
    # correct, or a protected human decision we won't touch without --force).
    if current in PROTECTED_STATUSES and not force:
        return None
    target = target_status(open_count)
    return None if target == current else target


def find_drift(items: list[ProjectItem], prior_types: dict[str, str]) -> list[str]:
    # A card whose linked content type changed since the last recorded sync (e.g. a
    # DraftIssue that's now a real Issue). We flag these and skip mutating them —
    # converting a real issue back to a draft is delete-and-recreate, not safe.
    drifted = []
    for item in items:
        was = prior_types.get(item.id)
        if was is not None and was != item.content_type:
            drifted.append(item.id)
    return drifted


_CHECKLIST = re.compile(r"^(\s*[-*] \[)( )(\]\s.*)$")


def checklist_updates(body: str, closed_refs: set[str]) -> tuple[str, int]:
    # Checks off any unchecked line that names an exact repo#number now
    # merged/closed. Fuzzy title matching is deliberately out of scope: a line
    # only auto-checks if it already cites the PR/issue it tracks.
    checked = 0
    out_lines = []
    for line in body.splitlines():
        m = _CHECKLIST.match(line)
        if m and _line_refs(line) & closed_refs:
            out_lines.append(f"{m.group(1)}x{m.group(3)}")
            checked += 1
        else:
            out_lines.append(line)
    trailing = "\n" if body.endswith("\n") else ""
    return "\n".join(out_lines) + trailing, checked


# Requires a repo segment before the "#": "owner/repo#12" or "repo#12". A bare
# "#12" is deliberately NOT matched — it's ambiguous across repos, and the doc's
# rule is to auto-check only lines that name the exact PR/issue they track.
_REF = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+|[A-Za-z0-9_.-]+#\d+")


def _line_refs(line: str) -> set[str]:
    return {m.group(0) for m in _REF.finditer(line)}


def normalize_ref(repo: str, number: int) -> set[str]:
    # The forms a closed PR/issue can appear as in a checklist line: full
    # "owner/repo#12" and short "repo#12".
    return {f"{repo}#{number}", f"{repo.split('/')[-1]}#{number}"}
