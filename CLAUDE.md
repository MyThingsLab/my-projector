# my-projector — agent instructions

You are developing **my-projector**, a MyThingsLab My[X] tool.

**Inherited rules:** obey [`./HARNESS.md`](./HARNESS.md) in full — the vendored
MyThingsLab build-harness rules. Do not restate or override them. Anything not
covered here defers to `HARNESS.md`, then `my-things-core/docs/CONVENTIONS.md`.

## This tool

- **Purpose:** keep the fleet's GitHub Project board (and any linked org-wide
  tracking issue) synced to the *live* state of every repo — reconcile
  merged/closed PRs and issues into the board's `Fleet Status` field and a
  tracking issue's checklist, so the dashboard never drifts from reality. Pure
  bookkeeping: it makes no priority judgments (that's MyOrchestrator/MyPlanner).
- **The single Engine call:** optional — rewrite a card's `Last step`/`Next
  step` prose from the raw PR/issue activity since the last sync. Against
  `NoopEngine` it falls back to a deterministic templated string; a run with
  zero events skips the call entirely.
- **Invariants / rules:**
  - Never override a human-set `Blocked`/`Design Only` status without `--force`.
  - Detect drift (a card whose linked content type changed unexpectedly) and
    **flag** it — never silently convert an issue back to a draft.
  - Board-field edits are `Action(kind="project-field-edit")` → `ALLOW`
    (private board, reversible). Editing/closing public content is
    `Action(kind="tracking-issue-edit")` / `Action(kind="issue-close")` →
    **`ASK`** by default: never touch public content unprompted.
  - Checklist auto-check only when a line already names the exact `repo#number`;
    fuzzy title matching is out of scope for v0.
- **Backlog label:** `my-projector`.
