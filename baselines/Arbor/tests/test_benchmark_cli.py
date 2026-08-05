"""Tests for the `arbor benchmark scaffold` CLI subcommand."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from arbor.cli.commands.benchmark_cmd import benchmark_app

runner = CliRunner()


def test_scaffold_light_creates_files(tmp_path: Path) -> None:
    dest = tmp_path / "demo"
    result = runner.invoke(benchmark_app, ["scaffold", str(dest), "--name", "demo"])
    assert result.exit_code == 0, result.output
    assert (dest / "eval.py").exists()
    assert (dest / "solution.py").exists()
    assert "created" in result.output.lower()


def test_scaffold_zoo_renders_verify_rows(tmp_path: Path) -> None:
    dest = tmp_path / "demo"
    result = runner.invoke(
        benchmark_app,
        ["scaffold", str(dest), "--name", "demo", "--style", "zoo"],
    )
    assert result.exit_code == 0, result.output
    assert (dest / "README.md").exists()
    assert (dest / "PROVENANCE.md").exists()
    assert "provenance" in result.output  # a verify row name was rendered


def test_scaffold_rejects_invalid_split_kind(tmp_path: Path) -> None:
    dest = tmp_path / "demo"
    result = runner.invoke(
        benchmark_app,
        ["scaffold", str(dest), "--name", "demo", "--split-kind", "paht"],
    )
    assert result.exit_code == 2
    assert not dest.exists()  # bailed before writing anything
