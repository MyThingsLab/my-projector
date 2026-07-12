from __future__ import annotations

from pathlib import Path

import pytest
from mythings.engine import ClaudeCLIEngine

from myprojector import cli
from myprojector.projector import SyncResult, Tracking


def test_build_engine_noop_returns_none() -> None:
    assert cli.build_engine("noop") is None


def test_build_engine_claude_cli_returns_engine_with_model() -> None:
    engine = cli.build_engine("claude-cli", model="opus")
    assert isinstance(engine, ClaudeCLIEngine)


def test_render_plain_without_drift() -> None:
    result = SyncResult(cards_updated=2, checklist_items_checked=1, drift=[])
    out = cli._render(result, as_json=False)
    assert out == "synced 2 cards, checked 1 checklist items"


def test_render_plain_with_drift() -> None:
    result = SyncResult(cards_updated=0, checklist_items_checked=0, drift=["ITEM_1", "ITEM_2"])
    out = cli._render(result, as_json=False)
    assert "drift flagged (not touched): ITEM_1, ITEM_2" in out


def test_render_json() -> None:
    result = SyncResult(cards_updated=1, checklist_items_checked=0, drift=[])
    out = cli._render(result, as_json=True)
    assert out == ('{"cards_updated":1,"checklist_items_checked":0,"drift":[],"outcome":"success"}')


class _RecordingProjector:
    # Stands in for myprojector.projector.Projector so main() never touches a
    # real `gh` subprocess; records the constructor/sync args it was called with.
    instances: list[_RecordingProjector] = []

    def __init__(self, *, org, project_number, ledger, engine=None) -> None:
        self.org = org
        self.project_number = project_number
        self.ledger = ledger
        self.engine = engine
        self.sync_kwargs: dict | None = None
        _RecordingProjector.instances.append(self)

    def sync(self, **kwargs) -> SyncResult:
        self.sync_kwargs = kwargs
        return SyncResult(cards_updated=1, checklist_items_checked=0, drift=[])


@pytest.fixture(autouse=True)
def _reset_recording_projector() -> None:
    _RecordingProjector.instances = []


def test_main_wires_args_into_projector_and_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(cli, "Projector", _RecordingProjector)
    ledger_path = tmp_path / "ledger.jsonl"

    rc = cli.main(
        [
            "sync",
            "--project-number",
            "3",
            "--repos",
            "my-guard,my-projector",
            "--force",
            "--ledger",
            str(ledger_path),
        ]
    )

    assert rc == 0
    (proj,) = _RecordingProjector.instances
    assert proj.org == "MyThingsLab"
    assert proj.project_number == 3
    assert proj.engine is None
    assert proj.sync_kwargs == {
        "repos": ["my-guard", "my-projector"],
        "dry_run": False,
        "force": True,
        "apply_checklist": False,
        "tracking": None,
    }
    out = capsys.readouterr().out
    assert "synced 1 cards" in out


def test_main_defaults_repos_to_none_when_not_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "Projector", _RecordingProjector)

    cli.main(["sync", "--project-number", "1"])

    (proj,) = _RecordingProjector.instances
    assert proj.sync_kwargs["repos"] is None


def test_main_json_output(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(cli, "Projector", _RecordingProjector)

    cli.main(["sync", "--project-number", "1", "--json"])

    out = capsys.readouterr().out
    assert out.strip() == (
        '{"cards_updated":1,"checklist_items_checked":0,"drift":[],"outcome":"success"}'
    )


def test_main_builds_tracking_when_both_repo_and_issue_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "Projector", _RecordingProjector)

    cli.main(
        [
            "sync",
            "--project-number",
            "1",
            "--apply-checklist",
            "--tracking-repo",
            "MyThingsLab/my-things-core",
            "--tracking-issue",
            "7",
        ]
    )

    (proj,) = _RecordingProjector.instances
    assert proj.sync_kwargs["apply_checklist"] is True
    assert proj.sync_kwargs["tracking"] == Tracking(repo="MyThingsLab/my-things-core", issue=7)


def test_main_wires_claude_cli_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "Projector", _RecordingProjector)

    cli.main(["sync", "--project-number", "1", "--engine", "claude-cli", "--engine-model", "opus"])

    (proj,) = _RecordingProjector.instances
    assert isinstance(proj.engine, ClaudeCLIEngine)


def test_apply_checklist_without_tracking_args_errors(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["sync", "--project-number", "1", "--apply-checklist"])

    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "--apply-checklist needs --tracking-repo and --tracking-issue" in err


def test_apply_checklist_with_only_tracking_repo_errors(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit):
        cli.main(
            [
                "sync",
                "--project-number",
                "1",
                "--apply-checklist",
                "--tracking-repo",
                "MyThingsLab/my-things-core",
            ]
        )

    assert "--apply-checklist needs" in capsys.readouterr().err


def test_missing_project_number_errors(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit):
        cli.main(["sync"])

    assert capsys.readouterr().err  # argparse's own required-arg error


def test_missing_subcommand_errors() -> None:
    with pytest.raises(SystemExit):
        cli.main([])
