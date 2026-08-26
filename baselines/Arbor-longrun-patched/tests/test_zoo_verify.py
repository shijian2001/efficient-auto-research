"""Tests for the zoo verifier (``arbor.zoo.verify``) — a light structural lint."""

from __future__ import annotations

from pathlib import Path

from arbor.zoo import verify_pack

_PROVENANCE = (
    "# Provenance\n\n## Source\nx\n## Setup & environment\nx\n## Baseline\nx\n"
    "## Contamination assessment\nx\n## Caveats\nx\n"
)
_README = "# demo\n\nA demo benchmark.\n\n## The task\nx\n## Metric\nx\n"


def _build(tmp_path: Path, *, readme: str | None = _README,
           provenance: str | None = _PROVENANCE, eval_entry: str | None = "eval.sh") -> Path:
    pack = tmp_path / "demo"
    pack.mkdir(parents=True, exist_ok=True)
    if readme is not None:
        (pack / "README.md").write_text(readme)
    if provenance is not None:
        (pack / "PROVENANCE.md").write_text(provenance)
    if eval_entry is not None:
        (pack / eval_entry).write_text("echo 'score: 1.0'\n")
    return pack


def _by_name(results) -> dict:
    return {r.name: r for r in results}


def test_valid_pack_passes(tmp_path: Path) -> None:
    results = verify_pack(_build(tmp_path))
    assert [r for r in results if r.status == "fail"] == []


def test_missing_eval_entrypoint_fails(tmp_path: Path) -> None:
    r = _by_name(verify_pack(_build(tmp_path, eval_entry=None)))["files-present"]
    assert r.status == "fail" and "eval" in r.message


def test_missing_provenance_fails(tmp_path: Path) -> None:
    r = _by_name(verify_pack(_build(tmp_path, provenance=None)))
    assert r["files-present"].status == "fail"


def test_missing_provenance_section_fails(tmp_path: Path) -> None:
    prov = _PROVENANCE.replace("## Contamination assessment\nx\n", "")
    r = _by_name(verify_pack(_build(tmp_path, provenance=prov)))["provenance"]
    assert r.status == "fail" and "Contamination" in r.message


def test_empty_readme_fails(tmp_path: Path) -> None:
    r = _by_name(verify_pack(_build(tmp_path, readme="# x\n")))["readme"]
    assert r.status == "fail"


def test_verify_does_not_run_eval(tmp_path: Path) -> None:
    # An eval that would exit 1 if run must not affect verify (it's never executed).
    pack = _build(tmp_path, eval_entry=None)
    (pack / "eval.sh").write_text("#!/usr/bin/env bash\nexit 1\n")
    assert [r for r in verify_pack(pack) if r.status == "fail"] == []
