"""End-to-end: the shipped ``arbor-zoo`` packs pass the structural verify lint.

``verify`` does not run the eval, so this needs no numpy and is not timing-sensitive.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from arbor.cli.app import app
from arbor.zoo import discover_packs, verify_pack

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ZOO = _REPO_ROOT / "arbor-zoo"

requires_zoo = pytest.mark.skipif(not _ZOO.is_dir(), reason="arbor-zoo not present")


@requires_zoo
def test_algotune_knn_verifies_clean() -> None:
    fails = [(r.name, r.message) for r in verify_pack(_ZOO / "algotune_knn") if r.status == "fail"]
    assert fails == [], f"algotune_knn should verify clean, got: {fails}"


@requires_zoo
def test_every_shipped_pack_verifies() -> None:
    packs = discover_packs(_ZOO)
    assert packs, "expected at least one pack in arbor-zoo"
    for summary in packs:
        fails = [(r.name, r.message) for r in verify_pack(Path(summary.path)) if r.status == "fail"]
        assert fails == [], f"pack {summary.name} has failures: {fails}"


@requires_zoo
def test_cli_verify_exit_code_zero() -> None:
    result = CliRunner().invoke(app, ["benchmark", "verify", str(_ZOO / "algotune_knn")])
    assert result.exit_code == 0, result.output


def test_cli_verify_incomplete_pack_fails(tmp_path: Path) -> None:
    # an empty dir: verify runs (no eval) and reports the missing pieces → exit 1
    result = CliRunner().invoke(app, ["benchmark", "verify", str(tmp_path)])
    assert result.exit_code == 1
