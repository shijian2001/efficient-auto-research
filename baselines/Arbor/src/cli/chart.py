"""Terminal progress chart for the live dashboard.

Renders the run's score-over-time as a small character-grid scatter +
step-frontier plot, the terminal counterpart of the "progress over
experiment cycles" figure: a baseline line, every scored attempt as a
colour-coded dot (green = merged, dim = kept-but-not-merged, red = failed),
and the best-so-far frontier as a connected green line.

A character grid (one coloured glyph per cell) is used rather than braille
because a braille glyph can only carry a single colour — and the whole point
of this chart is to colour-distinguish merged vs. not-merged attempts.

This module is pure (no I/O, no global state) so it can be unit-tested in
isolation; the dashboard feeds it points and embeds the returned Text rows.
"""

from __future__ import annotations

from rich.text import Text

from .style import format_duration

# Glyph + rich-style per attempt status. Anything not listed renders as a
# faint dot. Order in the grid: dots overwrite the frontier line, which
# overwrites the baseline, which overwrites the axes.
_POINT_STYLE: dict[str, tuple[str, str]] = {
    "merged": ("●", "bold green"),
    "done":   ("·", "bright_black"),
    "failed": ("×", "red"),
}
_FRONTIER_STYLE = "green"
_BASELINE_STYLE = "yellow"
_AXIS_STYLE = "bright_black"


def _fmt_score(v: float) -> str:
    """Compact y-axis label: 0.2083, 1.5, 12.34 — never scientific."""
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 1000:
        return f"{v:.0f}"
    if a >= 1:
        return f"{v:.3f}".rstrip("0").rstrip(".")
    return f"{v:.4f}"


def render_progress_chart(
    points: list[tuple[float, float, str]],
    *,
    baseline: float | None,
    metric_direction: str,
    width: int,
    height: int,
    now_elapsed: float = 0.0,
) -> list[Text]:
    """Render score-over-elapsed-time as a list of ``height`` rich ``Text`` rows.

    ``points`` is ``(elapsed_seconds, score, status)`` per scored attempt.
    ``metric_direction`` is ``"maximize"`` or ``"minimize"`` and only affects
    the running-best frontier. ``now_elapsed`` is the run's current elapsed time;
    it floors the x-axis so early points are not pinned to the right edge and the
    axis advances even before the first result.

    Returns a single hint row only when there is nothing at all to anchor on
    (no scored attempt *and* no baseline); otherwise the returned list has
    exactly ``height`` rows. With a baseline but no attempts yet, a baseline-only
    chart is drawn so the panel is visible from the start of the run.
    """
    scored = [(e, sc, st) for (e, sc, st) in points if sc is not None]
    if not scored and baseline is None:
        return [Text("  (no scored attempts yet)", style="dim")]

    # ── plot box geometry ──────────────────────────────────────────────
    # Reserve the bottom two rows for the x-axis line + its tick labels.
    plot_h = max(3, height - 2)
    ys = [sc for _, sc, _ in scored] + ([baseline] if baseline is not None else [])
    ymin, ymax = min(ys), max(ys)
    if ymax - ymin < 1e-9:                       # flat / single value: synthesise a span
        pad = abs(ymax) * 0.1 or 1.0
        ymin, ymax = ymin - pad, ymax + pad
    else:                                        # 5% breathing room
        pad = (ymax - ymin) * 0.05
        ymin, ymax = ymin - pad, ymax + pad

    xs = [e for e, _, _ in scored]
    xmax = max([now_elapsed, *xs])
    if xmax <= 0:
        xmax = 1.0

    # Left gutter width = widest y label we will print (top / baseline / bottom).
    label_vals = [ymax, ymin] + ([baseline] if baseline is not None else [])
    lab_w = max(len(_fmt_score(v)) for v in label_vals)
    plot_w = width - lab_w - 2                    # gutter + axis glyph + space
    if plot_w < 10:                               # too narrow to be useful
        return [Text("  (terminal too narrow for chart)", style="dim")]

    def y_to_row(v: float) -> int:
        frac = (v - ymin) / (ymax - ymin)
        return min(plot_h - 1, max(0, round((1 - frac) * (plot_h - 1))))

    def x_to_col(e: float) -> int:
        if xmax <= 0:
            return 0
        return min(plot_w - 1, max(0, round(e / xmax * (plot_w - 1))))

    # ── grid: each cell is (glyph, style) or None ──────────────────────
    grid: list[list[tuple[str, str] | None]] = [
        [None] * plot_w for _ in range(plot_h)
    ]
    baseline_row = y_to_row(baseline) if baseline is not None else None

    # Layer 1: baseline dashed line spans the whole width.
    if baseline_row is not None:
        for c in range(plot_w):
            grid[baseline_row][c] = ("┄", _BASELINE_STYLE)

    # Layer 2: best-so-far frontier as a connected step line.
    _draw_frontier(grid, scored, metric_direction, plot_w, x_to_col, y_to_row)

    # Layer 3: every attempt as a colour-coded dot (highest priority).
    for e, sc, st in scored:
        r, c = y_to_row(sc), x_to_col(e)
        grid[r][c] = _POINT_STYLE.get(st, ("·", "bright_black"))

    # Baseline-only state: no attempts yet — leave a hint so the empty plot
    # reads as "warming up" rather than "broken".
    if not scored:
        note = "waiting for first result…"
        for i, ch in enumerate(note):
            if i < plot_w:
                grid[0][i] = (ch, "dim")

    # ── assemble rows ──────────────────────────────────────────────────
    rows: list[Text] = []
    for r in range(plot_h):
        line = Text()
        # y label only on top / bottom / baseline rows; axis glyph otherwise.
        if r == 0:
            label, axis = _fmt_score(ymax), "┤"
        elif r == plot_h - 1:
            label, axis = _fmt_score(ymin), "┤"
        elif r == baseline_row:
            label, axis = _fmt_score(baseline), "┼"
        else:
            label, axis = "", "│"
        line.append(label.rjust(lab_w) + " ", style="dim")
        line.append(axis, style=_AXIS_STYLE)
        for cell in grid[r]:
            if cell is None:
                line.append(" ")
            else:
                line.append(cell[0], style=cell[1])
        rows.append(line)

    rows.append(_axis_line(lab_w, plot_w))
    rows.append(_tick_labels(lab_w, plot_w, xmax))
    return rows


def _draw_frontier(grid, scored, metric_direction, plot_w, x_to_col, y_to_row) -> None:
    """Forward-fill a running-best value per column, then connect the
    resulting samples with box-drawing line segments."""
    minimize = metric_direction == "minimize"
    ordered = sorted(scored, key=lambda p: p[0])

    # running best at each attempt's column
    best: float | None = None
    col_val: dict[int, float] = {}
    for e, sc, _ in ordered:
        if best is None or (sc < best if minimize else sc > best):
            best = sc
        col_val[x_to_col(e)] = best

    if not col_val:
        return
    first_col = min(col_val)
    # forward-fill so the frontier is defined for every column after the first
    frontier_row: dict[int, int] = {}
    cur: float | None = None
    for c in range(first_col, plot_w):
        if c in col_val:
            cur = col_val[c]
        if cur is not None:
            frontier_row[c] = y_to_row(cur)

    prev_r = None
    for c in range(first_col, plot_w):
        if c not in frontier_row:
            continue
        r = frontier_row[c]
        if prev_r is None:
            _set_frontier(grid, r, c, "─")
        elif r == prev_r:
            _set_frontier(grid, r, c, "─")
        else:
            # vertical step at column c, with corners joining the two flats
            lo, hi = sorted((r, prev_r))
            for rr in range(lo + 1, hi):
                _set_frontier(grid, rr, c, "│")
            if r < prev_r:                      # improved (rose on screen)
                _set_frontier(grid, prev_r, c, "╯")
                _set_frontier(grid, r, c, "╭")
            else:                               # regressed frontier (rare)
                _set_frontier(grid, prev_r, c, "╮")
                _set_frontier(grid, r, c, "╰")
        prev_r = r


def _set_frontier(grid, r, c, glyph) -> None:
    if 0 <= r < len(grid) and 0 <= c < len(grid[0]):
        grid[r][c] = (glyph, _FRONTIER_STYLE)


def _axis_line(lab_w: int, plot_w: int) -> Text:
    line = Text()
    line.append(" " * (lab_w + 1), style="dim")
    body = ["─"] * plot_w
    for frac in (1 / 3, 2 / 3):
        body[min(plot_w - 1, round(frac * (plot_w - 1)))] = "┬"
    line.append("└" + "".join(body), style=_AXIS_STYLE)
    return line


def _tick_labels(lab_w: int, plot_w: int, xmax: float) -> Text:
    """Time labels under the axis ticks (start / 1/3 / 2/3), with an
    ``elapsed →`` caption right-aligned into any spare room on the right."""
    slots = [" "] * plot_w
    for frac in (0.0, 1 / 3, 2 / 3):
        col = min(plot_w - 1, round(frac * (plot_w - 1)))
        label = format_duration(xmax * frac)
        for i, ch in enumerate(label):
            if col + i < plot_w:
                slots[col + i] = ch
    caption = "elapsed →"
    start = plot_w - len(caption)
    if start > 0 and all(slots[i] == " " for i in range(start - 1, plot_w)):
        for i, ch in enumerate(caption):
            slots[start + i] = ch
    line = Text()
    line.append(" " * (lab_w + 2), style="dim")    # gutter + below '└'
    line.append("".join(slots), style="dim")
    return line
