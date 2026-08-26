"""Tests for the zoo scaffolder (``arbor.zoo.scaffold``)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from arbor.zoo import ScaffoldResult, scaffold_benchmark, verify_pack


def test_light_scaffold_creates_eval_split_and_solution(tmp_path: Path) -> None:
    res = scaffold_benchmark(tmp_path, name="demo", style="light")
    assert isinstance(res, ScaffoldResult)
    assert "eval.py" in res.created and "solution.py" in res.created
    assert (tmp_path / "eval.py").exists() and (tmp_path / "solution.py").exists()
    assert res.verify == []  # light style does not verify


def test_light_eval_template_prints_parseable_score(tmp_path: Path) -> None:
    scaffold_benchmark(tmp_path, name="demo", style="light")
    proc = subprocess.run(
        [sys.executable, str(tmp_path / "eval.py"), "--split", "dev"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0 and "score:" in proc.stdout


def test_zoo_readme_run_commands_match_entrypoint(tmp_path: Path) -> None:
    scaffold_benchmark(tmp_path / "sh", name="demo", style="zoo", eval_entrypoint="eval.sh")
    readme = (tmp_path / "sh" / "README.md").read_text()
    assert "bash eval.sh dev" in readme and "python eval.py" not in readme
    scaffold_benchmark(tmp_path / "py", name="demo", style="zoo", eval_entrypoint="eval.py")
    assert "python eval.py --split dev" in (tmp_path / "py" / "README.md").read_text()


def test_zoo_readme_is_natural_language_no_front_matter(tmp_path: Path) -> None:
    scaffold_benchmark(tmp_path, name="demo", metric_direction="minimize", style="zoo")
    readme = (tmp_path / "README.md").read_text()
    assert not readme.startswith("---")          # no YAML front-matter
    assert "lower is better" in readme            # direction rendered in prose


def test_path_split_creates_data_dirs(tmp_path: Path) -> None:
    res = scaffold_benchmark(tmp_path, name="demo", split_kind="path", style="light")
    assert "data/dev/example_001.txt" in res.created
    assert "data/test/example_001.txt" in res.created


def test_generated_eval_sh_uses_lf_line_endings(tmp_path: Path) -> None:
    # Regression: a CRLF shebang ("…bash\r") is a broken interpreter on Unix.
    scaffold_benchmark(tmp_path, name="demo", style="light", eval_entrypoint="eval.sh")
    raw = (tmp_path / "eval.sh").read_bytes()
    assert b"\r\n" not in raw
    assert raw.split(b"\n", 1)[0] == b"#!/usr/bin/env bash"


def test_zoo_scaffold_passes_structural_verify(tmp_path: Path) -> None:
    res = scaffold_benchmark(tmp_path, name="demo", style="zoo")
    assert "README.md" in res.created and "PROVENANCE.md" in res.created
    fails = [r for r in verify_pack(tmp_path) if r.status == "fail"]
    assert not fails, f"structural verify failed: {[(r.name, r.message) for r in fails]}"


def test_scaffold_is_idempotent(tmp_path: Path) -> None:
    scaffold_benchmark(tmp_path, name="demo", style="zoo")
    res2 = scaffold_benchmark(tmp_path, name="demo", style="zoo")
    assert res2.created == [] and "solution.py" in res2.skipped


def test_invalid_args_raise(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        scaffold_benchmark(tmp_path, name="x", style="bogus")
    with pytest.raises(ValueError):
        scaffold_benchmark(tmp_path, name="x", metric_direction="up")
    with pytest.raises(ValueError):
        scaffold_benchmark(tmp_path, name="x", split_kind="weird")
