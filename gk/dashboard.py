"""Static dashboard: one HTML file, results embedded, no server, no API calls.
Recruiters open it and see everything; it can be hosted on any static site."""
from __future__ import annotations

import html
import os

LABEL = {"naive": "Naive (train score)", "heldout_only": "Held-out only",
         "gated": "Full gate + rollback"}
COLOR = {"naive": "#e5484d", "heldout_only": "#f5a623", "gated": "#3ecf8e"}


def _chart(series: dict, title: str, ymax: float, fmt) -> str:
    w, h, pl, pb, pt, pr = 520, 260, 52, 34, 16, 16
    n = len(next(iter(series.values())))
    x = lambda i: pl + i * (w - pl - pr) / (n - 1)
    y = lambda v: h - pb - v / ymax * (h - pb - pt)
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{html.escape(title)}">']
    for k in range(5):
        v = ymax * k / 4
        out.append(f'<line x1="{pl}" x2="{w-pr}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="grid"/>'
                   f'<text x="{pl-8}" y="{y(v)+4:.1f}" class="ax" text-anchor="end">{fmt(v)}</text>')
    for i in range(n):
        out.append(f'<text x="{x(i):.1f}" y="{h-10}" class="ax" text-anchor="middle">{i}</text>')
    out.append(f'<text x="{(pl+w-pr)/2}" y="{h-0}" class="ax" text-anchor="middle" '
               f'style="opacity:.0">round</text>')
    for m, vals in series.items():
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
        out.append(f'<polyline points="{pts}" fill="none" stroke="{COLOR[m]}" stroke-width="2.5" '
                   f'stroke-linejoin="round"/>')
        out.append(f'<circle cx="{x(n-1):.1f}" cy="{y(vals[-1]):.1f}" r="4" fill="{COLOR[m]}"/>')
    out.append("</svg>")
    return "".join(out)


def _log_table(log: list) -> str:
    if not log:
        return ""
    rows = "".join(
        f'<tr><td>{e["round"]}</td><td><span class="pill {"good" if e["kind"]=="placebo" else "bad"}">'
        f'{html.escape(e["kind"])}</span></td><td>{"PROMOTED" if e["accepted"] else "-"}</td>'
        f'<td>{html.escape(e["reason"])}</td><td>{html.escape(e.get("note", ""))}</td></tr>' for e in log)
    return ('<h3 style="margin-top:18px">Every real gate decision in this run</h3>'
            '<p class="lede" style="font-size:13px">"placebo" rows are a control: a content-free note of '
            'similar length. If it gains as much as a lesson, the lesson\'s content is not what helped.</p>'
            '<table><thead><tr><th>Round</th><th>Edit</th><th>Outcome</th><th>Why</th><th>Lesson</th></tr>'
            f'</thead><tbody>{rows}</tbody></table>')


def _real_section(rl: dict) -> str:
    if not rl:
        return ""
    b = rl["report"][0]
    hp, hc = rl["held"]["parent"], rl["held"]["child"]
    nm = lambda h: h["events"].get("no_materials", 0)
    sl = lambda h: h["events"].get("slip", 0)
    eps = "".join(
        f'<tr><td>{e["seed"]}</td><td>{e["task"]}</td><td>{e["p_steps"]} → {e["c_steps"]}</td>'
        f'<td>{e["p_nomat"]} → {e["c_nomat"]}</td><td>{e["p_slip"]} → {e["c_slip"]}</td></tr>'
        for e in rl["episodes"])
    tr = lambda t: "".join(f"<li>{html.escape(x)}</li>" for x in t)
    rt = "".join(f'<tr><td><span class="pill bad">{html.escape(x["kind"])}</span></td>'
                 f'<td>rejected</td><td>{html.escape(x["reason"])}</td></tr>' for x in rl["red_team"])
    plc = next((e for e in rl.get("log", []) if e["kind"] == "placebo"), None)
    les_g = f'{rl["gate_metrics"].get("gain", 0):+.3f}'
    plc_g = f'{plc["metrics"]["gain"]:+.3f}' if plc else "n/a"
    if plc:
        story = (f"Mean steps moved {hp['mean_steps']:.2f} → {hc['mean_steps']:.2f} and slips were "
                 f"{sl(hp)} vs {sl(hc)} (paired noise). But a content-free note gained {plc_g} against the "
                 f"lesson's {les_g}: most of the improvement came from adding <i>any</i> text, not from "
                 "what the lesson said. The gate did not credit it.")
    else:
        story = (f"Mean steps moved {hp['mean_steps']:.2f} → {hc['mean_steps']:.2f}; slips {sl(hp)} → "
                 f"{sl(hc)}. With 10 episodes the gain could not be shown to be real, so the gate "
                 "refused to promote it.")
    return f"""
<h2 id="real">Real model run: {html.escape(rl["model"])} (free tier, env v{rl.get("env", 1)})</h2>
<div class="warn" style="background:#10202b;border-color:#1e4a63;color:#a8d8f0">These numbers are
<b>real Gemini calls</b>, replayed from the response cache. The sample is small (10 held-out
episodes) because the free quota is 500 requests a day.</div>
<div class="cards">
<div class="card"><h3>Baseline (10 episodes)</h3><div class="big">{b["success"]*100:.0f}%</div>
<div class="sub">success; mean steps {b["mean_steps"]:.1f} (ideal 7.8), score {b["score"]:.3f}</div></div>
<div class="card"><h3>Proposed lesson</h3><p style="margin:6px 0">“{html.escape(rl["lesson"])}”</p>
<div class="sub">written by Gemini from failed training episodes</div></div>
<div class="card"><h3>Lesson vs placebo (gain)</h3><div class="big" style="font-size:28px">{les_g} / {plc_g}</div>
<div class="sub">mean held-out gain of the lesson / of a content-free note</div></div>
<div class="card"><h3>Gate decision</h3><div class="big" style="font-size:26px;color:#ff9aa0">Rejected</div>
<div class="sub">{html.escape(rl["gate_reason"])}</div></div></div>
<p class="lede">{story}</p>
<details><summary>Per-episode held-out results</summary>
<table><thead><tr><th>Seed</th><th>Task</th><th>Steps</th><th>Not-enough-materials</th><th>Slips</th></tr></thead>
<tbody>{eps}</tbody></table></details>
<details><summary>Same world (seed {rl["trace_seed"]}), before and after the lesson</summary>
<div class="charts"><div class="panel"><h3>Before</h3><ol>{tr(rl["trace_parent"])}</ol></div>
<div class="panel"><h3>After</h3><ol>{tr(rl["trace_child"])}</ol></div></div></details>
{_log_table(rl.get("log", []))}
<h3 style="margin-top:18px">Planted bad edits against the real agent</h3>
<p class="lede" style="font-size:13px">Recorded from console output of a red-team run (that run's log
file was overwritten; the runner now keeps every run).</p>
<table><thead><tr><th>Edit</th><th>Outcome</th><th>Why</th></tr></thead><tbody>{rt}</tbody></table>"""


def render(res: dict, path: str, real: dict | None = None) -> None:
    s = res["summary"]
    cards = "".join(
        f'<div class="card" style="border-top:3px solid {COLOR[m]}"><h3>{LABEL[m]}</h3>'
        f'<div class="big">{s[m]["final_score"]:.2f}</div><div class="sub">honest held-out score</div>'
        f'<dl><dt>Bad edits kept</dt><dd>{s[m]["bad_surviving"]:.2f}</dd>'
        f'<dt>Bad edits ever promoted</dt><dd>{s[m]["bad_promoted"]:.2f}</dd>'
        f'<dt>Rollbacks</dt><dd>{s[m]["rollbacks"]:.2f}</dd>'
        f'<dt>Tokens / episode</dt><dd>{s[m]["final_tokens"]:.0f}</dd></dl></div>'
        for m in ("naive", "heldout_only", "gated"))
    rows = ""
    for m in ("naive", "heldout_only", "gated"):
        for e in res["logs"][m]:
            cls = "ok" if e["accepted"] else ("rb" if "ROLLBACK" in e["reason"] else "no")
            tag = "bad" if e["bad"] else "good"
            rows += (f'<tr class="{cls}"><td>{LABEL[m]}</td><td>{e["round"]}</td>'
                     f'<td><span class="pill {tag}">{html.escape(e["kind"])}</span></td>'
                     f'<td>{"PROMOTED" if e["accepted"] else "rejected"}</td>'
                     f'<td>{html.escape(e["reason"])}</td></tr>')
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gatekeeper: a promotion gate for self-improving agents</title><style>
:root{{--bg:#0e1116;--panel:#161b22;--line:#2a313c;--tx:#e6edf3;--mu:#8b97a6}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--tx);
font:15px/1.55 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1080px;margin:0 auto;padding:32px 20px 64px}}
h1{{font-size:28px;margin:0 0 6px}}h2{{font-size:18px;margin:36px 0 12px}}
.lede{{color:var(--mu);max-width:760px}}
.warn{{background:#2b2210;border:1px solid #5c4a1a;color:#f2d28b;padding:10px 14px;
border-radius:8px;margin:18px 0}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px}}
.card h3{{margin:0;font-size:14px;color:var(--mu);font-weight:600}}
.big{{font-size:38px;font-weight:700}}.sub{{color:var(--mu);font-size:12px;margin-bottom:8px}}
dl{{display:grid;grid-template-columns:1fr auto;gap:2px 10px;margin:0;font-size:13px}}
dt{{color:var(--mu)}}dd{{margin:0;text-align:right;font-variant-numeric:tabular-nums}}
.charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:14px}}
.panel{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}}
.panel h3{{margin:0 0 6px;font-size:14px}}svg{{width:100%;height:auto}}
.grid{{stroke:var(--line)}}.ax{{fill:var(--mu);font-size:11px}}
.legend{{display:flex;gap:16px;flex-wrap:wrap;font-size:13px;color:var(--mu);margin:8px 0 0}}
.legend i{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px}}
table{{width:100%;border-collapse:collapse;font-size:13px;background:var(--panel);
border:1px solid var(--line);border-radius:10px;overflow:hidden}}
th,td{{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{color:var(--mu);font-weight:600}}tr.ok td:nth-child(4){{color:#3ecf8e;font-weight:600}}
tr.rb td{{background:#2a1c10}}.pill{{padding:1px 8px;border-radius:99px;font-size:12px}}
.pill.bad{{background:#3a1d20;color:#ff9aa0}}.pill.good{{background:#16301f;color:#7ee2a8}}
details{{margin-top:10px}}summary{{cursor:pointer;color:var(--mu)}}
</style></head><body><main>
<h1>Gatekeeper</h1>
<p class="lede">A promotion gate for self-improving agent harnesses. An agent proposes edits to its own
memory and instructions; the gate decides which edits are real improvements, using unseen tasks, verified
outcomes, cost limits and automatic rollback.</p>
<p class="lede"><a href="#real" style="color:#7cc4ff">Jump to the real Gemini run</a></p>
<div class="warn"><b>Read this first.</b> The first results below are from a <b>simulated agent and a scripted
proposer</b> in a purpose-built toy game, averaged over {res["trials"]} worlds. They test the
<i>gate logic</i> against planted bad edits whose labels are known. They are <b>not</b> measurements of an
LLM. The real-model path (Gemini adapter, cache, rate limiter) is implemented and tested against mocks.</div>
<h2>Final result after {res["rounds"]} improvement rounds</h2><div class="cards">{cards}</div>
<h2>How the honest score and the cost evolve</h2><div class="charts">
<div class="panel"><h3>Honest held-out score (verified, never used for decisions)</h3>
{_chart(res["curves"], "score", 0.8, lambda v: f"{v:.1f}")}</div>
<div class="panel"><h3>Tokens per episode</h3>
{_chart(res["tokens"], "tokens", max(max(v) for v in res["tokens"].values()) * 1.1, lambda v: f"{v:.0f}")}</div></div>
<p class="lede" style="font-size:13px">Note: the naive loop's falling token count is not efficiency. Its
accepted "cheat" edit makes the agent report success without acting, so it does no work at all.</p>
<div class="legend">{"".join(f'<span><i style="background:{COLOR[m]}"></i>{LABEL[m]}</span>' for m in COLOR)}</div>
{_real_section(real or {})}
<h2>Every decision, one example world (simulation)</h2>
<p class="lede">Red pills are planted bad edits (ground truth known because this is a simulation).
Shaded rows are promotions that the regression suite rolled back.</p>
<details open><summary>Show / hide decision log</summary>
<table><thead><tr><th>Loop</th><th>Round</th><th>Edit</th><th>Outcome</th><th>Why</th></tr></thead>
<tbody>{rows}</tbody></table></details>
</main></body></html>"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
