"""Plugin system — lightweight domain adapters via YAML-driven prompt injection.

A Plugin is a YAML file that injects domain-specific guidance into agent
prompts at predefined injection points, plus declares evaluation contracts,
merge guards, config overrides, and lifecycle hooks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_PLUGINS_DIR = Path(__file__).parent

_KNOWN_PLUGIN_KEYS = frozenset({
    "schema_version", "name", "description",
    "meta_init_inject", "meta_ideate_inject", "meta_decide_inject",
    "meta_preamble_inject", "sub_workflow_inject", "sub_preamble_inject",
    "eval_contract", "protected_paths", "required_outputs",
    "config_overrides", "profiles", "lifecycle_hooks", "convergence",
})


class PluginNotFoundError(ValueError):
    """Raised when a requested plugin name is not visible in configured search dirs."""


@dataclass(frozen=True)
class PluginSummary:
    name: str
    description: str
    source: str
    profiles: tuple[str, ...] = ()


@dataclass
class Plugin:
    schema_version: int = 1
    name: str = ""
    description: str = ""

    # Prompt injections (6 injection points)
    meta_init_inject: str = ""
    meta_ideate_inject: str = ""
    meta_decide_inject: str = ""
    meta_preamble_inject: str = ""
    sub_workflow_inject: str = ""
    sub_preamble_inject: str = ""

    # Eval contract — static eval protocol, prefilled into tree.meta
    eval_contract: dict[str, Any] = field(default_factory=dict)

    # Protected paths & required outputs — merge guard
    protected_paths: list[str] = field(default_factory=list)
    required_outputs: list[str] = field(default_factory=list)

    # Runtime config overrides
    # Priority: defaults < plugin.config_overrides < profiles[active] < YAML config < CLI args
    config_overrides: dict[str, Any] = field(default_factory=dict)
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Lifecycle hooks
    lifecycle_hooks: dict[str, Any] = field(default_factory=dict)

    # Convergence detection config (passed to ConvergenceDetector)
    convergence: dict[str, Any] = field(default_factory=dict)


def load_plugin(name: str, search_dirs: list[Path] | None = None, *, strict: bool = False) -> Plugin:
    """Load a plugin by name from the plugins/ directory.

    Looks for ``<name>.yaml`` (or ``.yml``) in *search_dirs* (checked
    first, in order) then in the built-in ``plugins/`` package directory.
    Returns an empty Plugin if not found anywhere unless ``strict`` is true.
    """
    dirs = list(search_dirs or []) + [_PLUGINS_DIR]
    for d in dirs:
        for ext in (".yaml", ".yml"):
            path = d / f"{name}{ext}"
            if path.exists():
                return _load_plugin_from_path(path)

    if strict:
        searched = ", ".join(str(d) for d in dirs)
        raise PluginNotFoundError(f"Plugin {name!r} was not found in: {searched}")
    log.warning("Plugin %r not found in %s, using empty defaults", name, dirs)
    return Plugin(name=name)


def discover_plugins(search_dirs: list[Path] | None = None) -> list[PluginSummary]:
    """Return available plugins, with earlier search dirs overriding built-ins."""
    dirs = [(d, "project") for d in list(search_dirs or [])] + [(_PLUGINS_DIR, "built-in")]
    seen: set[str] = set()
    out: list[PluginSummary] = []
    for directory, source in dirs:
        if not directory.exists() or not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.y*ml")):
            try:
                plugin = _load_plugin_from_path(path)
            except Exception as exc:  # noqa: BLE001
                log.warning("Plugin %s could not be discovered: %s", path, exc)
                continue
            name = plugin.name or path.stem
            if name in seen:
                continue
            seen.add(name)
            out.append(PluginSummary(
                name=name,
                description=plugin.description or "(no description)",
                source=source,
                profiles=tuple(sorted(plugin.profiles)),
            ))
    return sorted(out, key=lambda p: p.name)


def _load_plugin_from_path(path: Path) -> Plugin:
    """Parse a plugin YAML file into a Plugin dataclass."""
    text = path.read_text(encoding="utf-8")

    try:
        import yaml
        data = yaml.safe_load(text)
    except ImportError:
        raise ImportError(
            "PyYAML is required for plugin loading. Install it: pip install pyyaml"
        )

    if not isinstance(data, dict):
        raise ValueError(f"Plugin file must be a YAML mapping, got {type(data).__name__}")

    unknown_keys = set(data.keys()) - _KNOWN_PLUGIN_KEYS
    if unknown_keys:
        log.warning(
            "Plugin %s has unknown keys (possible typos): %s",
            path.name, ", ".join(sorted(unknown_keys)),
        )

    return Plugin(
        schema_version=data.get("schema_version", 1),
        name=data.get("name", path.stem),
        description=data.get("description", ""),
        meta_init_inject=data.get("meta_init_inject", ""),
        meta_ideate_inject=data.get("meta_ideate_inject", ""),
        meta_decide_inject=data.get("meta_decide_inject", ""),
        meta_preamble_inject=data.get("meta_preamble_inject", ""),
        sub_workflow_inject=data.get("sub_workflow_inject", ""),
        sub_preamble_inject=data.get("sub_preamble_inject", ""),
        eval_contract=data.get("eval_contract", {}),
        protected_paths=data.get("protected_paths", []),
        required_outputs=data.get("required_outputs", []),
        config_overrides=data.get("config_overrides", {}),
        profiles=data.get("profiles", {}),
        lifecycle_hooks=data.get("lifecycle_hooks", {}),
        convergence=data.get("convergence", {}),
    )
