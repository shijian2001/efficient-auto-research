"""Layered configuration resolution — the single, testable merge point (C2).

``resolve_config`` is a pure function implementing the precedence declared in
``plugins/base.py`` (and previously only enforced by hand-written merges
scattered across ``config_loader`` and the Typer CLI's ``eff`` dict)::

    pydantic defaults  <  plugin.config_overrides  <  profiles[active]
                       <  user YAML  <  CLI overrides

Every layer is a plain mapping; they are deep-merged and validated **once** at
the end by the typed config model, whose ``before`` validators fold flat keys
into subgroups and coerce structured blocks. Flat (legacy) and nested YAML keys
are both accepted — see :mod:`arbor.core.config_schema`.
"""

from __future__ import annotations

import logging
from difflib import get_close_matches
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: YAML top-level keys that are directives, not config fields.
_DIRECTIVE_KEYS = frozenset({"plugin", "plugin_profile", "coordinator", "executor"})
_LEGACY_ALIASES = frozenset({"max_depth", "branch_prefix"})


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` onto ``base`` (override wins per-key)."""
    out = dict(base)
    for key, value in override.items():
        if (
            key in out
            and isinstance(out[key], dict)
            and isinstance(value, dict)
        ):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Load a YAML config file as a mapping (PyYAML is a hard dependency)."""
    import yaml

    text = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(
            f"Config file must be a YAML mapping, got {type(data).__name__}"
        )
    return data


def _split_section(raw: dict[str, Any], role: str) -> dict[str, Any]:
    """Flatten a YAML doc into one layer: top-level shared keys, then the
    role section (``coordinator:`` / ``executor:``) overlaid on top.

    Directive keys (plugin/plugin_profile/section names) are dropped — they are
    handled by the caller.
    """
    shared = {k: v for k, v in raw.items() if k not in _DIRECTIVE_KEYS}
    section = raw.get(role)
    if isinstance(section, dict):
        return deep_merge(shared, section)
    return shared


def _model_keys(model_cls: Any) -> set[str]:
    return set(getattr(model_cls, "model_fields", {})) | set(getattr(model_cls, "PROXY", {}))


def _warn_unknown_key(*, location: str, key: str, candidates: set[str]) -> None:
    hint = get_close_matches(key, sorted(candidates), n=1)
    suffix = f"; did you mean {hint[0]!r}?" if hint else ""
    log.warning("Unknown config key %s.%s will be ignored%s", location, key, suffix)


def _warn_unknown_mapping_keys(
    mapping: dict[str, Any],
    *,
    location: str,
    allowed: set[str],
) -> None:
    for key in sorted(mapping):
        if key not in allowed:
            _warn_unknown_key(location=location, key=key, candidates=allowed)


def _warn_unknown_nested_blocks(mapping: dict[str, Any], *, location: str) -> None:
    from ..coordinator.config import BudgetPolicy, SearchConfig
    from ..coordinator.convergence import ConvergenceConfig
    from .config_schema import ContextConfig, LLMConfig, TimeoutConfig, UIConfig

    block_fields: dict[str, set[str]] = {
        "llm": set(LLMConfig.model_fields) | set(LLMConfig.FIELD_ALIASES),
        "timeout": set(TimeoutConfig.model_fields),
        "context": set(ContextConfig.model_fields),
        "ui": set(UIConfig.model_fields),
        "budget_policy": set(BudgetPolicy.model_fields) | {
            "total_time_budget", "time_budget", "run_training_default", "run_training_max",
        },
        "search": set(SearchConfig.model_fields),
        "convergence": set(ConvergenceConfig.model_fields),
    }
    for block, allowed in block_fields.items():
        value = mapping.get(block)
        if isinstance(value, dict):
            _warn_unknown_mapping_keys(value, location=f"{location}.{block}", allowed=allowed)


def _warn_unknown_yaml_keys(raw: dict[str, Any], yaml_path: Path | None) -> None:
    if not raw:
        return
    from ..coordinator.config import CoordinatorConfig
    from .config import AgentConfig

    source = str(yaml_path) if yaml_path is not None else "config"
    coordinator_keys = _model_keys(CoordinatorConfig) | _LEGACY_ALIASES
    executor_keys = _model_keys(AgentConfig)
    shared_top_keys = coordinator_keys | executor_keys | _DIRECTIVE_KEYS
    _warn_unknown_mapping_keys(raw, location=source, allowed=shared_top_keys)
    _warn_unknown_nested_blocks(raw, location=source)

    role_sections = {
        "coordinator": coordinator_keys,
        "executor": executor_keys,
    }
    for role, allowed in role_sections.items():
        section = raw.get(role)
        if isinstance(section, dict):
            _warn_unknown_mapping_keys(section, location=f"{source}.{role}", allowed=allowed)
            _warn_unknown_nested_blocks(section, location=f"{source}.{role}")


def _load_plugin(
    raw: dict[str, Any],
    yaml_path: Path | None,
    extra_search_dirs: list[Path] | None = None,
    *,
    strict: bool = True,
):
    """Load the plugin object referenced by ``plugin:`` (if any)."""
    name = raw.get("plugin")
    if not name or not isinstance(name, str):
        return None
    from ..plugins import load_plugin

    search_dirs = list(extra_search_dirs or [])
    if yaml_path is not None and (yaml_path.parent / "plugins").is_dir():
        search_dirs.append(yaml_path.parent / "plugins")
    plugin = load_plugin(name, search_dirs=search_dirs or None, strict=strict)
    log.info("Loaded plugin: %s", plugin.name)
    return plugin


def _plugin_profile(plugin: Any, profile_name: Any, *, strict: bool) -> dict[str, Any] | None:
    if not profile_name or not isinstance(profile_name, str):
        return None
    if profile_name in plugin.profiles:
        return plugin.profiles[profile_name]
    available = ", ".join(sorted(plugin.profiles)) or "none"
    message = f"Plugin {plugin.name!r} has no profile {profile_name!r}; available profiles: {available}"
    if strict:
        raise ValueError(message)
    log.warning(message)
    return None


def load_layered_defaults(
    yaml_path: str | Path | None,
    role: str = "coordinator",
    *,
    cli_directives: dict[str, Any] | None = None,
    extra_search_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    """Return plugin-overrides + profile + YAML merged into one flat-ish dict.

    This is the same precedence as :func:`resolve_config` minus CLI overrides
    and validation. It exists so the interactive Typer intake can compute its
    effective options with plugin/profile awareness (otherwise a plugin that
    sets ``provider``/``model`` would be invisible to the intake chat).
    """
    if yaml_path is None:
        return {}
    path = Path(yaml_path)
    raw = load_yaml(path)
    plugin_disabled = bool((cli_directives or {}).get("_plugin_disabled"))
    plugin_profile_cleared = bool((cli_directives or {}).get("_plugin_profile_cleared"))
    if plugin_disabled:
        directive_raw = dict(raw)
        directive_raw.pop("plugin", None)
        directive_raw.pop("plugin_profile", None)
    else:
        directives = {
            k: v for k, v in (cli_directives or {}).items()
            if k in _DIRECTIVE_KEYS and v is not None
        }
        directive_raw = deep_merge(raw, directives)
        if plugin_profile_cleared:
            directive_raw.pop("plugin_profile", None)
    plugin = _load_plugin(directive_raw, path, extra_search_dirs, strict=False)

    merged: dict[str, Any] = {}
    if plugin is not None:
        if plugin.config_overrides:
            merged = deep_merge(merged, plugin.config_overrides)
        profile = _plugin_profile(plugin, directive_raw.get("plugin_profile"), strict=False)
        if profile is not None:
            merged = deep_merge(merged, profile)
    return deep_merge(merged, _split_section(raw, role))


def resolve_config(
    *,
    yaml_path: str | Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
    role: str = "coordinator",
):
    """Resolve the full configuration for a run.

    Parameters
    ----------
    yaml_path:
        Optional path to a user YAML config.
    cli_overrides:
        Mapping of explicitly-provided CLI values (``None`` entries are dropped
        so they never shadow lower layers).
    role:
        ``"coordinator"`` or ``"executor"`` — selects the YAML section overlay
        and the target config model.

    Returns
    -------
    A validated ``CoordinatorConfig`` (or ``AgentConfig`` for ``executor``).
    """
    raw: dict[str, Any] = {}
    path = Path(yaml_path) if yaml_path else None
    if path is not None:
        raw = load_yaml(path)
        _warn_unknown_yaml_keys(raw, path)

    plugin_disabled = bool((cli_overrides or {}).get("_plugin_disabled"))
    plugin_profile_cleared = bool((cli_overrides or {}).get("_plugin_profile_cleared"))
    if plugin_disabled:
        directive_raw = dict(raw)
        directive_raw.pop("plugin", None)
        directive_raw.pop("plugin_profile", None)
    else:
        cli_directives = {
            k: v for k, v in (cli_overrides or {}).items()
            if k in _DIRECTIVE_KEYS and v is not None
        }
        directive_raw = deep_merge(raw, cli_directives)
        if plugin_profile_cleared:
            directive_raw.pop("plugin_profile", None)
    extra_search_dirs = [
        Path(p) for p in (cli_overrides or {}).get("_plugin_search_dirs", [])
        if p
    ]

    plugin = _load_plugin(directive_raw, path, extra_search_dirs, strict=True)

    # Layer 1+2: plugin config_overrides, then the active profile. Profiles are
    # an independent precedence layer — they apply even when config_overrides
    # is empty.
    merged: dict[str, Any] = {}
    if plugin is not None:
        if plugin.config_overrides:
            merged = deep_merge(merged, plugin.config_overrides)
        profile_name = directive_raw.get("plugin_profile")
        profile = _plugin_profile(plugin, profile_name, strict=True)
        if profile is not None:
            log.info("Applying plugin profile: %s", profile_name)
            merged = deep_merge(merged, profile)

    # Layer 3: user YAML (shared keys + role section). Drop top-level None so a
    # ``model: null`` "reset to default" never fails non-Optional validation
    # (mirrors the old loader, which stripped None before merging).
    section = _split_section(raw, role)
    section = {k: v for k, v in section.items() if v is not None}
    merged = deep_merge(merged, section)

    # Layer 4: CLI overrides (drop None so they don't clobber lower layers).
    if cli_overrides:
        provided = {
            k: v for k, v in cli_overrides.items()
            if v is not None and k not in _DIRECTIVE_KEYS and not k.startswith("_plugin_")
        }
        merged = deep_merge(merged, provided)

    if plugin is not None:
        merged["plugin"] = plugin

    # Single validation point — the model's before-validators fold flat keys
    # into subgroups and coerce structured blocks.
    if role == "executor":
        from .config import AgentConfig

        return AgentConfig(**merged)

    from ..coordinator.config import CoordinatorConfig

    return CoordinatorConfig(**merged)
