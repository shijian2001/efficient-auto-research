"""Tiny i18n for the single user-facing confirmation surface (the Research
Contract shown right before launch).

The agent already writes the plan *values* (instruction, rationale) in the
user's language, so we only localize the fixed **labels** and boilerplate.
Language is detected heuristically from the user's text: any CJK character →
``zh``, otherwise ``en``. Extend ``LABELS`` to add more languages.
"""

from __future__ import annotations

import re

# Hiragana/Katakana + CJK Unified Ideographs (+ compat) — good enough to tell a
# Chinese/Japanese goal from an English one.
_CJK = re.compile(r"[぀-ヿ㐀-鿿豈-﫿]")


def detect_lang(*texts: str | None) -> str:
    """Return ``"zh"`` if any input contains a CJK character, else ``"en"``."""
    for t in texts:
        if t and _CJK.search(t):
            return "zh"
    return "en"


# One flat label set per language. The Research Contract panel (run.py) and the
# /contract preview (intake) both read from here. Keep it minimal: target, goal,
# budget, depth, plus the resolved model/provider/review/session rows.
LABELS: dict[str, dict[str, str]] = {
    "en": {
        "contract_title": "Arbor Research Contract",
        "target": "target",
        "optimize": "objective",
        "budget": "budget",
        "branch_cycles": "branch cycles",
        "coordinator_turns": "coordinator turns",
        "budget_defaults": "CLI/config defaults",
        "tree_depth": "max depth",
        "unlimited": "unlimited",
        "model": "model",
        "provider": "provider",
        "endpoint": "endpoint",
        "plugin": "plugin",
        "skills": "skills",
        "interaction_mode": "interaction mode",
        "webui": "webui",
        "session_dir": "session dir",
        "confirm_launch": "Start the run?",
    },
    "zh": {
        "contract_title": "Arbor 研究契约",
        "target": "目标项目",
        "optimize": "研究目标",
        "budget": "预算",
        "branch_cycles": "个分支周期",
        "coordinator_turns": "轮协调",
        "budget_defaults": "CLI/配置默认值",
        "tree_depth": "最大深度",
        "unlimited": "无限",
        "model": "模型",
        "provider": "提供方",
        "endpoint": "端点",
        "plugin": "插件",
        "skills": "技能",
        "interaction_mode": "交互模式",
        "webui": "WebUI",
        "session_dir": "会话目录",
        "confirm_launch": "开始运行？",
    },
}


def t(lang: str, key: str) -> str:
    """Look up a label for ``lang``, falling back to English then the key."""
    return LABELS.get(lang, LABELS["en"]).get(key) or LABELS["en"].get(key, key)
