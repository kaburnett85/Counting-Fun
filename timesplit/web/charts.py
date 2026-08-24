"""Charts, drawn as inline SVG on the server.

No Chart.js, no D3, no CDN: the dashboard has to work with the network off,
and a charting library would cost more memory than the entire tracker. These
are a few hundred lines of arithmetic and string building.

Colours come from the category records, so the palette follows whatever you
chose in the wizard.
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass

from ..core.clock import fmt_duration


def esc(text: str) -> str:
    return html.escape(str(text), quote=True)


@dataclass
class Slice:
    label: str
    seconds: float
    color: str


def donut(slices: list[Slice], size: int = 190, thickness: int = 30) -> str:
    """Today's split. Renders a full ring for a single category."""
    total = sum(s.seconds for s in slices)
    cx = cy = size / 2
    radius = (size - thickness) / 2
    if total <= 0:
        return (
            f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" role="img"'
            f' aria-label="No time tracked yet">'
            f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none"'
            f' stroke="var(--muted-bg)" stroke-width="{thickness}"/>'
            f'<text x="{cx}" y="{cy + 5}" text-anchor="middle" class="donut-empty">no data</text>'
            f"</svg>"
        )

    parts = [
        f'<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" role="img"'
        f' aria-label="Time split by job">'
    ]
    if len(slices) == 1:
        s = slices[0]
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" stroke="{esc(s.color)}"'
            f' stroke-width="{thickness}"><title>{esc(s.label)}: {fmt_duration(s.seconds)}</title>'
            f"</circle>"
        )
    else:
        angle = -math.pi / 2
        for s in slices:
            if s.seconds <= 0:
                continue
            sweep = (s.seconds / total) * 2 * math.pi
            end = angle + sweep
            large = 1 if sweep > math.pi else 0
            x1, y1 = cx + radius * math.cos(angle), cy + radius * math.sin(angle)
            x2, y2 = cx + radius * math.cos(end), cy + radius * math.sin(end)
            parts.append(
                f'<path d="M {x1:.2f} {y1:.2f} A {radius:.2f} {radius:.2f} 0 {large} 1'
                f' {x2:.2f} {y2:.2f}" fill="none" stroke="{esc(s.color)}"'
                f' stroke-width="{thickness}">'
                f"<title>{esc(s.label)}: {fmt_duration(s.seconds)}"
                f" ({s.seconds / total:.0%})</title></path>"
            )
            angle = end

    parts.append(
        f'<text x="{cx}" y="{cy - 2}" text-anchor="middle" class="donut-total">'
        f"{esc(fmt_duration(total))}</text>"
    )
    parts.append(
        f'<text x="{cx}" y="{cy + 16}" text-anchor="middle" class="donut-label">tracked</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def stacked_days(
    days: list[str],
    series: list[tuple[str, str, list[float]]],
    width: int = 720,
    height: int = 220,
) -> str:
    """One bar per day, stacked by job. ``series`` is (label, color, per-day seconds)."""
    if not days:
        return '<p class="empty">Nothing tracked this week yet.</p>'

    pad_left, pad_bottom, pad_top = 46, 28, 12
    plot_w = width - pad_left - 12
    plot_h = height - pad_bottom - pad_top
    totals = [sum(s[2][i] for s in series) for i in range(len(days))]
    peak = max(totals + [1.0])
    # Round the axis up to a whole hour so gridlines read cleanly.
    axis_max = max(3600.0, math.ceil(peak / 3600.0) * 3600.0)
    slot = plot_w / len(days)
    bar_w = min(46.0, slot * 0.62)

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}"'
        f' role="img" aria-label="Hours per day by job" class="chart">'
    ]

    steps = max(1, int(axis_max // 3600))
    steps = min(steps, 8)
    for i in range(steps + 1):
        value = axis_max * i / steps
        y = pad_top + plot_h - (value / axis_max) * plot_h
        parts.append(
            f'<line x1="{pad_left}" y1="{y:.1f}" x2="{width - 12}" y2="{y:.1f}"'
            f' class="gridline"/>'
        )
        parts.append(
            f'<text x="{pad_left - 8}" y="{y + 4:.1f}" text-anchor="end" class="axis">'
            f"{value / 3600:.0f}h</text>"
        )

    for index, day in enumerate(days):
        x = pad_left + slot * index + (slot - bar_w) / 2
        y = pad_top + plot_h
        for label, color, values in series:
            seconds = values[index]
            if seconds <= 0:
                continue
            bar_h = (seconds / axis_max) * plot_h
            y -= bar_h
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}"'
                f' fill="{esc(color)}" rx="2">'
                f"<title>{esc(day)} — {esc(label)}: {fmt_duration(seconds)}</title></rect>"
            )
        parts.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{height - 10}" text-anchor="middle"'
            f' class="axis">{esc(day[5:])}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


def timeline_band(blocks, day_start: float, day_end: float, height: int = 58) -> str:
    """A 24-hour band: one pixel per minute, colour by job, grey for idle.

    Hatched blocks are guesses the app is not sure about -- clicking one is how
    you correct it.
    """
    span = max(1.0, day_end - day_start)
    width = 1440  # a minute per pixel; the container scrolls
    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}"'
        f' class="timeline" role="img" aria-label="Timeline of the day">',
        '<defs><pattern id="review-hatch" width="6" height="6"'
        ' patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        '<rect width="6" height="6" fill="rgba(255,255,255,0.35)"/>'
        '<line x1="0" y1="0" x2="0" y2="6" stroke="rgba(0,0,0,0.28)"'
        ' stroke-width="3"/></pattern></defs>',
        f'<rect x="0" y="0" width="{width}" height="{height - 16}" class="timeline-bg"/>',
    ]

    for hour in range(0, 25, 2):
        x = width * (hour / 24.0)
        parts.append(
            f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{height - 16}" class="hourline"/>'
        )
        if hour < 24:
            parts.append(
                f'<text x="{x + 3:.1f}" y="{height - 4}" class="axis">{hour:02d}</text>'
            )

    for block in blocks:
        start = max(day_start, block.start_ts)
        end = min(day_end, block.end_ts)
        if end <= start:
            continue
        x = width * ((start - day_start) / span)
        w = max(1.2, width * ((end - start) / span))
        if block.kind == "idle":
            parts.append(
                f'<rect x="{x:.2f}" y="{(height - 16) * 0.62:.1f}" width="{w:.2f}"'
                f' height="{(height - 16) * 0.38:.1f}" class="idle-block">'
                f"<title>{esc(block.label)}: {fmt_duration(end - start)}</title></rect>"
            )
            continue
        tip = f"{block.title or block.category_name} — {fmt_duration(end - start)}"
        attrs = (
            f'data-session-id="{block.session_id}" class="work-block"'
            if block.session_id
            else 'class="work-block"'
        )
        parts.append(
            f'<rect x="{x:.2f}" y="0" width="{w:.2f}" height="{height - 16}"'
            f' fill="{esc(block.color)}" {attrs}><title>{esc(tip)}</title></rect>'
        )
        if block.needs_review:
            parts.append(
                f'<rect x="{x:.2f}" y="0" width="{w:.2f}" height="{height - 16}"'
                f' fill="url(#review-hatch)" pointer-events="none"/>'
            )

    parts.append("</svg>")
    return "".join(parts)


def bar_row(label: str, seconds: float, peak: float, color: str) -> str:
    """A single horizontal bar, for the top-apps and top-sites lists."""
    pct = 0 if peak <= 0 else min(100.0, seconds / peak * 100.0)
    return (
        f'<div class="bar-row"><span class="bar-label">{esc(label)}</span>'
        f'<span class="bar-track"><span class="bar-fill" style="width:{pct:.1f}%;'
        f'background:{esc(color)}"></span></span>'
        f'<span class="bar-value">{esc(fmt_duration(seconds))}</span></div>'
    )
