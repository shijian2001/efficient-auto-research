from __future__ import annotations

from arbor.coordinator.tools.protected import resolve_protected_paths


class _Plugin:
    def __init__(self, paths):
        self.protected_paths = paths


class _Cfg:
    def __init__(self, paths):
        self.protected_paths = paths


def test_union_dedup_order_stable():
    cfg = _Cfg(["a/**", "b/**"])
    plugin = _Plugin(["b/**", "c/**"])
    meta = {"protected_paths": ["c/**", "d/**"]}
    assert resolve_protected_paths(cfg, plugin, meta) == ["a/**", "b/**", "c/**", "d/**"]


def test_handles_none_plugin_and_missing_meta():
    cfg = _Cfg(["x/**"])
    assert resolve_protected_paths(cfg, None, {}) == ["x/**"]


def test_empty_everywhere():
    assert resolve_protected_paths(_Cfg([]), None, {}) == []
