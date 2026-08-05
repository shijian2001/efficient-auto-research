"""Unit tests for the pure helpers in run_dashboard.py.

These functions hold the dashboard's layout math and text formatting — the
parts that don't touch the terminal or rich rendering. Locking their behavior
here is the safety net that makes future decomposition of the RunDashboard
class safe.
"""

from __future__ import annotations

import itertools

from arbor.cli.run_dashboard import (
    _FOOTER_H,
    _GATE_H,
    _HEADER_H,
    _REPLY_MIN_H,
    _compose_input_status_hint,
    _compose_input_title,
    _compose_line_mode_prompt,
    _fmt_tokens,
    _gate_command_value,
    _plan_section_sizes,
    _sparkline,
    _spinner,
    _trim_lines,
)


# ── _plan_section_sizes — the layout invariant ───────────────────────

def test_plan_never_exceeds_available_height() -> None:
    """Non-expanded layout: header + footer + sum(panels) always fits `avail`,
    for every flag combination and every height.

    (reply_expanded mode is intentionally exempt — there the reply panel claims
    at least its minimum height even if that overflows; see
    test_plan_expanded_claims_min_on_tiny_screen.)
    """
    flags = list(itertools.product([False, True], repeat=4))  # gate/reply/chart/reasoning
    for avail in range(1, 80):
        effective = max(_HEADER_H + _FOOTER_H + 1, avail)
        for has_gate, has_reply, has_chart, has_reasoning in flags:
            out = _plan_section_sizes(
                avail,
                has_gate=has_gate,
                has_reply=has_reply,
                has_chart=has_chart,
                has_reasoning=has_reasoning,
                reply_expanded=False,
            )
            total = _HEADER_H + _FOOTER_H + sum(out.values())
            assert total <= effective, (avail, out)
            assert all(v > 0 for v in out.values())


def test_plan_nonexpanded_never_exceeds_even_tiny_heights() -> None:
    """Non-expanded layout respects `avail` down to the smallest sizes."""
    for avail in range(1, 40):
        out = _plan_section_sizes(
            avail, has_gate=True, has_reply=True, has_chart=True,
            has_reasoning=True, reply_expanded=False,
        )
        effective = max(_HEADER_H + _FOOTER_H + 1, avail)
        assert _HEADER_H + _FOOTER_H + sum(out.values()) <= effective, (avail, out)


def test_plan_expanded_claims_min_on_tiny_screen() -> None:
    """Documented trade-off: in expanded mode the reply claims at least its
    minimum height even when the terminal is too short to hold it."""
    out = _plan_section_sizes(
        5, has_gate=False, has_reply=True, has_chart=False, has_reasoning=False,
        reply_expanded=True,
    )
    assert out.get("reply") == _REPLY_MIN_H


def test_plan_gate_has_priority_when_it_fits() -> None:
    out = _plan_section_sizes(
        40, has_gate=True, has_reply=True, has_chart=True, has_reasoning=True,
    )
    assert out.get("gate") == _GATE_H


def test_plan_reply_expanded_drops_ambient_panels() -> None:
    out = _plan_section_sizes(
        50, has_gate=False, has_reply=True, has_chart=True, has_reasoning=True,
        reply_expanded=True,
    )
    assert "chart" not in out
    assert "reasoning" not in out
    assert out.get("reply", 0) >= 1


def test_plan_tiny_height_drops_optional_panels() -> None:
    out = _plan_section_sizes(
        _HEADER_H + _FOOTER_H + 1,
        has_gate=True, has_reply=True, has_chart=True, has_reasoning=True,
    )
    # Only header+footer+1 of room: nothing optional can fit.
    assert out == {}


# ── _sparkline ───────────────────────────────────────────────────────

def test_sparkline_empty() -> None:
    assert _sparkline([]) == ""


def test_sparkline_single_value() -> None:
    assert len(_sparkline([5.0])) == 1


def test_sparkline_flat_when_all_equal() -> None:
    out = _sparkline([3.0, 3.0, 3.0])
    assert len(out) == 3
    assert len(set(out)) == 1  # one repeated glyph


def test_sparkline_length_matches_input() -> None:
    assert len(_sparkline([1.0, 2.0, 3.0, 4.0])) == 4


# ── _fmt_tokens ──────────────────────────────────────────────────────

def test_fmt_tokens_scales() -> None:
    assert _fmt_tokens(999) == "999"
    assert _fmt_tokens(1500) == "1.5k"
    assert _fmt_tokens(2_000_000) == "2.0M"


# ── _trim_lines ──────────────────────────────────────────────────────

def test_trim_lines_no_truncation() -> None:
    out, truncated = _trim_lines("short", max_lines=10, max_chars=100)
    assert out == "short"
    assert truncated is False


def test_trim_lines_char_cap() -> None:
    out, truncated = _trim_lines("a" * 50, max_lines=10, max_chars=10)
    assert truncated is True
    assert out.endswith("…")
    assert len(out) <= 10


def test_trim_lines_line_cap() -> None:
    out, truncated = _trim_lines("a\nb\nc\nd", max_lines=2, max_chars=100)
    assert truncated is True
    assert out.count("\n") == 1
    assert out.endswith("…")


# ── _gate_command_value ──────────────────────────────────────────────

def test_gate_command_value_mappings() -> None:
    assert _gate_command_value("/approve") == "approve"
    assert _gate_command_value("/y") == "approve"
    assert _gate_command_value("/skip") == "skip"
    assert _gate_command_value("/no") == "skip"
    assert _gate_command_value("/edit tweak it") == "edit tweak it"
    assert _gate_command_value("/edit") is None        # needs an argument
    assert _gate_command_value("/answer 42") == "42"
    assert _gate_command_value("plain text") is None   # not a slash command
    assert _gate_command_value("/unknown") is None


# ── _spinner ─────────────────────────────────────────────────────────

def test_spinner_returns_a_frame() -> None:
    from arbor.cli.run_dashboard import _SPINNER_FRAMES
    assert _spinner() in _SPINNER_FRAMES


# ── input-mode string composers ──────────────────────────────────────

def test_input_status_hint_priority() -> None:
    # gate companion thinking takes precedence over everything
    assert "gate companion" in _compose_input_status_hint(
        pending_gate={}, gate_discussion_busy=True,
        companion_busy=True, awaiting_reply=True,
    )
    assert "companion is preparing" in _compose_input_status_hint(
        pending_gate=None, gate_discussion_busy=False,
        companion_busy=True, awaiting_reply=True,
    )
    assert "next turn" in _compose_input_status_hint(
        pending_gate=None, gate_discussion_busy=False,
        companion_busy=False, awaiting_reply=True,
    )
    assert _compose_input_status_hint(
        pending_gate=None, gate_discussion_busy=False,
        companion_busy=False, awaiting_reply=False,
    ) == ""


def test_input_title_reflects_mode() -> None:
    idle = dict(pending_gate=None, gate_discussion_busy=False,
                companion_busy=False, awaiting_reply=False)
    assert _compose_input_title(**idle, input_target="research") == "input · research"
    assert _compose_input_title(**idle, input_target="ask") == "input · ask"
    assert _compose_input_title(
        pending_gate={}, gate_discussion_busy=True, companion_busy=False,
        awaiting_reply=False, input_target="ask",
    ) == "input · gate · companion thinking"


def test_line_mode_prompt_variants() -> None:
    assert "companion reply" in _compose_line_mode_prompt(
        status="[magenta]companion is preparing a reply...[/]",
        gate=False, input_target="ask",
    )
    assert _compose_line_mode_prompt(status="", gate=True, input_target="ask").startswith(
        "line mode — type /approve"
    )
    assert "affects the agent" in _compose_line_mode_prompt(
        status="", gate=False, input_target="research",
    )
