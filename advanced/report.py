"""Campaign reporting (ARCHITECTURE.md §10): rich terminal, JSON, and HTML.

All three renderers work off a single ``CampaignResult``. The JSON report is
``CampaignResult.to_dict()`` verbatim. The HTML report is one self-contained
file — inline CSS only, CSS-bar charts, no external assets of any kind —
and is readable in both light and dark color schemes.
"""

from __future__ import annotations

import html
import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from advanced.core_types import CampaignResult, MutantResult, Outcome


@dataclass
class _OperatorRow:
    """Per-operator kill/survive aggregate over a campaign."""

    operator_name: str
    kind: str
    total: int = 0
    detected_original: int = 0
    detected_final: int = 0

    @property
    def survivors_final(self) -> int:
        return self.total - self.detected_final


def _operator_breakdown(campaign: CampaignResult) -> list[_OperatorRow]:
    """Aggregate detection counts per operator, sorted by kind then name."""
    rows: dict[str, _OperatorRow] = {}
    for result in campaign.final_results():
        row = rows.setdefault(
            result.operator_name, _OperatorRow(result.operator_name, result.kind)
        )
        row.total += 1
        if result.detected:
            row.detected_final += 1
    for result in campaign.original_results:
        if result.operator_name in rows and result.detected:
            rows[result.operator_name].detected_original += 1
    return sorted(rows.values(), key=lambda r: (r.kind, r.operator_name))


def _final_survivors(campaign: CampaignResult) -> list[MutantResult]:
    """Bugs still undetected after the best-known (post-heal) run."""
    return [r for r in campaign.final_results() if r.outcome is Outcome.SURVIVED]


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def _gap_points(campaign: CampaignResult) -> float | None:
    """False-confidence gap: line coverage minus pre-heal detection, in points."""
    if campaign.line_coverage_pct is None:
        return None
    return campaign.line_coverage_pct - campaign.score_original


def _shorten(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# Terminal
# ---------------------------------------------------------------------------

def render_terminal(campaign: CampaignResult, *, console: Console | None = None) -> None:
    """Render the campaign to the terminal with rich tables and panels.

    ``console`` is injectable for testing; the default writes to stdout.
    """
    out = console if console is not None else Console()
    finals = campaign.final_results()
    gap = _gap_points(campaign)

    summary_lines = [
        f"[bold]Target:[/bold] {campaign.target}    [bold]Seed:[/bold] {campaign.seed}",
        (
            f"[bold]Bank:[/bold] {campaign.total_bugs} bugs "
            f"({campaign.code_mutants} code mutants + "
            f"{campaign.prompt_perturbations} prompt perturbations)"
        ),
        f"[bold]Baseline line coverage:[/bold] {_fmt_pct(campaign.line_coverage_pct)}",
        (
            f"[bold]Detection, original suite:[/bold] "
            f"{campaign.detected_original}/{len(campaign.original_results)} "
            f"({campaign.score_original:.1f}%)"
        ),
        (
            f"[bold]Mutation score, healed suite:[/bold] "
            f"{campaign.detected_final}/{len(finals)} ({campaign.score_final:.1f}%)"
        ),
    ]
    if gap is not None:
        summary_lines.append(
            f"[bold]False-confidence gap:[/bold] {_fmt_pct(campaign.line_coverage_pct)} "
            f"line coverage vs {campaign.score_original:.1f}% detection "
            f"= [bold red]{gap:.1f} pts[/bold red] of unearned confidence"
        )
    summary_lines.append(f"[bold]Wall time:[/bold] {campaign.wall_time_s:.1f}s")
    out.print(Panel("\n".join(summary_lines), title="SyntraceAI campaign", box=box.ROUNDED))

    op_table = Table(title="Per-operator breakdown", box=box.SIMPLE_HEAVY)
    op_table.add_column("Operator", no_wrap=True)
    op_table.add_column("Kind")
    op_table.add_column("Bugs", justify="right")
    op_table.add_column("Detected (orig)", justify="right")
    op_table.add_column("Detected (final)", justify="right")
    op_table.add_column("Survivors", justify="right")
    for row in _operator_breakdown(campaign):
        style = "red" if row.survivors_final else "green"
        op_table.add_row(
            row.operator_name,
            row.kind,
            str(row.total),
            str(row.detected_original),
            str(row.detected_final),
            f"[{style}]{row.survivors_final}[/{style}]",
        )
    out.print(op_table)

    survivors = _final_survivors(campaign)
    if survivors:
        sv_table = Table(title=f"Surviving bugs ({len(survivors)})", box=box.SIMPLE_HEAVY)
        sv_table.add_column("Id", no_wrap=True)
        sv_table.add_column("Operator", no_wrap=True)
        sv_table.add_column("Location", no_wrap=True)
        sv_table.add_column("Snippet / description")
        for result in survivors:
            sv_table.add_row(
                result.mutant_id,
                result.operator_name,
                result.location,
                _shorten(result.description, 70) or "—",
            )
        out.print(sv_table)
    else:
        out.print("[bold green]No surviving bugs — every injected bug was detected.[/bold green]")

    if campaign.healed_tests:
        heal_table = Table(
            title=f"Auto-healed tests ({len(campaign.healed_tests)})", box=box.SIMPLE_HEAVY
        )
        heal_table.add_column("Kills", no_wrap=True)
        heal_table.add_column("Test", no_wrap=True)
        heal_table.add_column("Module", no_wrap=True)
        heal_table.add_column("Input")
        heal_table.add_column("Expected")
        for healed in campaign.healed_tests:
            heal_table.add_row(
                healed.mutant_id,
                healed.test_name,
                healed.module,
                _shorten(healed.input_repr, 40),
                _shorten(healed.expected_repr, 40),
            )
        out.print(heal_table)

    if campaign.unhealable_mutant_ids:
        out.print(
            "[yellow]Unhealable (no discriminating input found — likely equivalent): "
            + ", ".join(campaign.unhealable_mutant_ids)
            + "[/yellow]"
        )


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def write_json(campaign: CampaignResult, path: Path) -> None:
    """Write ``CampaignResult.to_dict()`` as pretty-printed JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(campaign.to_dict(), indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_CSS = """
:root {
  color-scheme: light dark;
  --bg: #f6f7f9; --card: #ffffff; --ink: #1c2330; --muted: #5a6472;
  --line: #d9dee6; --good: #1f9d55; --bad: #d64545; --accent: #3563c4;
  --bar-track: #e6e9ef;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14181f; --card: #1d232d; --ink: #e8ecf2; --muted: #9aa5b3;
    --line: #323b48; --good: #3fbf74; --bad: #e06666; --accent: #6d94e8;
    --bar-track: #2a3240;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem 1.25rem; background: var(--bg); color: var(--ink);
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
}
main { max-width: 960px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
h2 { font-size: 1.05rem; margin: 0 0 .75rem; }
.sub { color: var(--muted); margin: 0 0 1.5rem; }
section {
  background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 1.25rem; margin-bottom: 1.25rem;
}
.tiles { display: flex; flex-wrap: wrap; gap: 1rem; }
.tile { flex: 1 1 130px; }
.tile .num { font-size: 1.6rem; font-weight: 700; }
.tile .lbl { color: var(--muted); font-size: .8rem; }
.gap-note { margin-top: .75rem; color: var(--muted); }
.gap-note strong { color: var(--bad); }
.barrow { display: grid; grid-template-columns: 220px 1fr 70px; gap: .6rem; align-items: center; margin: .45rem 0; }
.barrow .lbl { text-align: right; color: var(--muted); overflow-wrap: anywhere; }
.track { background: var(--bar-track); border-radius: 5px; height: 16px; overflow: hidden; display: flex; }
.seg { height: 100%; }
.seg.good { background: var(--good); }
.seg.bad { background: var(--bad); }
.seg.cov { background: var(--accent); }
.val { font-variant-numeric: tabular-nums; }
.legend { color: var(--muted); font-size: .8rem; margin-top: .6rem; }
.dot { display: inline-block; width: .65em; height: .65em; border-radius: 50%; margin-right: .3em; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: .4rem .6rem; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 600; font-size: .8rem; text-transform: uppercase; letter-spacing: .04em; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
code { background: var(--bar-track); border-radius: 4px; padding: .1rem .35rem; font-size: .85em; overflow-wrap: anywhere; }
.scroll { overflow-x: auto; }
.ok { color: var(--good); font-weight: 600; }
.warn { color: var(--bad); font-weight: 600; }
footer { color: var(--muted); text-align: center; font-size: .8rem; }
"""


def _bar_row(label: str, pct: float, value_text: str, seg_class: str) -> str:
    width = max(0.0, min(100.0, pct))
    return (
        '<div class="barrow">'
        f'<span class="lbl">{html.escape(label)}</span>'
        f'<span class="track"><span class="seg {seg_class}" style="width:{width:.1f}%"></span></span>'
        f'<span class="val">{html.escape(value_text)}</span>'
        "</div>"
    )


def _stacked_bar_row(label: str, detected: int, total: int) -> str:
    survived = total - detected
    detected_pct = 100.0 * detected / total if total else 0.0
    return (
        '<div class="barrow">'
        f'<span class="lbl">{html.escape(label)}</span>'
        '<span class="track">'
        f'<span class="seg good" style="width:{detected_pct:.1f}%"></span>'
        f'<span class="seg bad" style="width:{100.0 - detected_pct:.1f}%"></span>'
        "</span>"
        f'<span class="val">{detected}/{total}'
        + (f" · {survived} live" if survived else "")
        + "</span></div>"
    )


def _gap_section(campaign: CampaignResult) -> str:
    rows: list[str] = []
    if campaign.line_coverage_pct is not None:
        rows.append(
            _bar_row(
                "Line coverage (original suite)",
                campaign.line_coverage_pct,
                _fmt_pct(campaign.line_coverage_pct),
                "cov",
            )
        )
    rows.append(
        _bar_row(
            "Bug detection (original suite)",
            campaign.score_original,
            f"{campaign.score_original:.1f}%",
            "bad",
        )
    )
    rows.append(
        _bar_row(
            "Mutation score (healed suite)",
            campaign.score_final,
            f"{campaign.score_final:.1f}%",
            "good",
        )
    )
    gap = _gap_points(campaign)
    note = (
        f'<p class="gap-note">The original suite’s coverage overstates its bug-finding '
        f"power by <strong>{gap:.1f} points</strong> — the false-confidence gap.</p>"
        if gap is not None
        else '<p class="gap-note">No baseline coverage figure was recorded for this run.</p>'
    )
    return (
        "<section><h2>Coverage vs. detection</h2>" + "".join(rows) + note + "</section>"
    )


def _operator_section(campaign: CampaignResult) -> str:
    rows = _operator_breakdown(campaign)
    if not rows:
        return "<section><h2>Per-operator breakdown</h2><p>No results recorded.</p></section>"
    bars = "".join(
        _stacked_bar_row(f"{row.operator_name} ({row.kind})", row.detected_final, row.total)
        for row in rows
    )
    legend = (
        '<p class="legend"><span class="dot" style="background:var(--good)"></span>detected '
        '&nbsp;<span class="dot" style="background:var(--bad)"></span>survived</p>'
    )
    return "<section><h2>Per-operator breakdown (post-heal)</h2>" + bars + legend + "</section>"


def _survivors_section(campaign: CampaignResult) -> str:
    survivors = _final_survivors(campaign)
    if not survivors:
        return (
            "<section><h2>Surviving bugs</h2>"
            '<p class="ok">None — every injected bug was detected.</p></section>'
        )
    body = "".join(
        "<tr>"
        f"<td>{html.escape(r.mutant_id)}</td>"
        f"<td>{html.escape(r.operator_name)}</td>"
        f"<td><code>{html.escape(r.location)}</code></td>"
        f"<td><code>{html.escape(r.description) or '—'}</code></td>"
        f"<td>{html.escape(r.phase)}</td>"
        "</tr>"
        for r in survivors
    )
    return (
        f'<section><h2>Surviving bugs <span class="warn">({len(survivors)})</span></h2>'
        '<div class="scroll"><table><thead><tr><th>Id</th><th>Operator</th><th>Location</th>'
        "<th>Snippet / description</th><th>Phase</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div></section>"
    )


def _healed_section(campaign: CampaignResult) -> str:
    parts: list[str] = ["<section><h2>Auto-healed tests</h2>"]
    if campaign.healed_tests:
        body = "".join(
            "<tr>"
            f"<td>{html.escape(h.mutant_id)}</td>"
            f"<td><code>{html.escape(h.test_name)}</code></td>"
            f"<td><code>{html.escape(h.module)}</code></td>"
            f"<td><code>{html.escape(h.input_repr)}</code></td>"
            f"<td><code>{html.escape(h.expected_repr)}</code></td>"
            "</tr>"
            for h in campaign.healed_tests
        )
        parts.append(
            '<div class="scroll"><table><thead><tr><th>Kills</th><th>Test</th><th>Module</th>'
            "<th>Input</th><th>Expected</th></tr></thead>"
            f"<tbody>{body}</tbody></table></div>"
        )
    else:
        parts.append("<p>No healed tests were generated in this campaign.</p>")
    if campaign.unhealable_mutant_ids:
        ids = ", ".join(html.escape(i) for i in campaign.unhealable_mutant_ids)
        parts.append(
            f'<p class="gap-note">Unhealable (no discriminating input found — '
            f"likely equivalent mutants, reported honestly): {ids}</p>"
        )
    parts.append("</section>")
    return "".join(parts)


def write_html(campaign: CampaignResult, path: Path) -> None:
    """Write a single self-contained HTML report (inline CSS, no external assets)."""
    finals = campaign.final_results()
    tiles = [
        (f"{campaign.total_bugs}", "injected bugs"),
        (f"{campaign.code_mutants}", "code mutants"),
        (f"{campaign.prompt_perturbations}", "prompt perturbations"),
        (_fmt_pct(campaign.line_coverage_pct), "line coverage"),
        (f"{campaign.score_original:.1f}%", "detection, original suite"),
        (f"{campaign.score_final:.1f}%", "mutation score, healed suite"),
    ]
    tile_html = "".join(
        f'<div class="tile"><div class="num">{html.escape(num)}</div>'
        f'<div class="lbl">{html.escape(lbl)}</div></div>'
        for num, lbl in tiles
    )
    doc = (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>SyntraceAI report — {html.escape(campaign.target)}</title>"
        f"<style>{_CSS}</style></head><body><main>"
        "<h1>SyntraceAI mutation campaign</h1>"
        f'<p class="sub">Target <code>{html.escape(campaign.target)}</code> '
        f"· seed {campaign.seed} · "
        f"{campaign.detected_final}/{len(finals)} bugs detected after healing</p>"
        f'<section><div class="tiles">{tile_html}</div></section>'
        + _gap_section(campaign)
        + _operator_section(campaign)
        + _survivors_section(campaign)
        + _healed_section(campaign)
        + f"<footer>Deterministic run · seed {campaign.seed} · "
        f"wall time {campaign.wall_time_s:.1f}s</footer>"
        "</main></body></html>\n"
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc, encoding="utf-8")
