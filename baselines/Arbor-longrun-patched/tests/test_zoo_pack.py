"""Tests for zoo pack discovery (``arbor.zoo.pack``)."""

from __future__ import annotations

from pathlib import Path

from arbor.zoo import discover_packs, find_eval_entrypoint, is_pack_dir


def _make_pack(zoo: Path, name: str, *, readme: str = "# title\n\nA demo benchmark.\n",
               entry: str = "eval.sh") -> Path:
    pack = zoo / name
    pack.mkdir(parents=True, exist_ok=True)
    (pack / "README.md").write_text(readme, encoding="utf-8")
    (pack / entry).write_text("echo 'score: 1.0'\n", encoding="utf-8")
    return pack


def test_is_pack_dir(tmp_path: Path) -> None:
    assert is_pack_dir(_make_pack(tmp_path, "demo"))


def test_is_pack_dir_requires_readme_and_eval(tmp_path: Path) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    assert not is_pack_dir(bare)
    (bare / "README.md").write_text("# x")
    assert not is_pack_dir(bare)  # README but no eval entrypoint


def test_find_eval_entrypoint_prefers_sh(tmp_path: Path) -> None:
    pack = tmp_path / "p"
    pack.mkdir()
    (pack / "eval.py").write_text("x")
    assert find_eval_entrypoint(pack) == "eval.py"
    (pack / "eval.sh").write_text("x")
    assert find_eval_entrypoint(pack) == "eval.sh"


def test_discover_skips_underscore_and_non_packs(tmp_path: Path) -> None:
    _make_pack(tmp_path, "real")
    _make_pack(tmp_path, "_template")           # scaffold — skipped
    (tmp_path / "not_a_pack").mkdir()           # no README/eval — skipped
    (tmp_path / "README.md").write_text("index", encoding="utf-8")
    assert [p.name for p in discover_packs(tmp_path)] == ["real"]


def test_discover_description_from_readme(tmp_path: Path) -> None:
    _make_pack(tmp_path, "demo", readme="# Heading\n\nThe one-line summary.\n")
    assert discover_packs(tmp_path)[0].description == "The one-line summary."


def test_discover_description_tolerates_legacy_front_matter(tmp_path: Path) -> None:
    readme = "---\nname: demo\n---\n\n# Heading\n\nReal summary line.\n"
    _make_pack(tmp_path, "demo", readme=readme)
    assert discover_packs(tmp_path)[0].description == "Real summary line."


def test_discover_empty_dir(tmp_path: Path) -> None:
    assert discover_packs(tmp_path / "missing") == []
