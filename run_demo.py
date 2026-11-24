"""Run the simulated experiment and write results.json + the static dashboard."""
import json
import statistics as st
import sys

from gk.dashboard import render
from gk.loop import MODES, run_trial

TRIALS = int(sys.argv[1]) if len(sys.argv) > 1 else 8
ROUNDS = 6


def main():
    runs = {m: [run_trial(m, t, ROUNDS) for t in range(TRIALS)] for m in MODES}
    summary, curves, tokens = {}, {}, {}
    for m, rs in runs.items():
        finals = [r["final_score"] for r in rs]
        summary[m] = {
            "final_score": st.mean(finals),
            "final_score_sd": st.pstdev(finals),
            "final_tokens": st.mean(r["final_tokens"] for r in rs),
            "bad_promoted": st.mean(r["bad_promoted"] for r in rs),
            "bad_surviving": st.mean(r["bad_surviving"] for r in rs),
            "rollbacks": st.mean(r["rollbacks"] for r in rs),
        }
        curves[m] = [st.mean(r["curve"][i] for r in rs) for i in range(ROUNDS + 1)]
        tokens[m] = [st.mean(r["tokens"][i] for r in rs) for i in range(ROUNDS + 1)]
    out = {"trials": TRIALS, "rounds": ROUNDS, "summary": summary, "curves": curves,
           "tokens": tokens, "logs": {m: runs[m][1]["log"] for m in MODES}}
    with open("results.json", "w") as f:
        json.dump(out, f, indent=1)
    try:
        real = json.load(open("real_analysis.json"))
    except FileNotFoundError:
        real = None
    render(out, "dashboard/index.html", real)
    render(out, "docs/index.html", real)  # GitHub Pages serves /docs
    for m in MODES:
        s = summary[m]
        print(f"{m:13s} score {s['final_score']:.3f}±{s['final_score_sd']:.3f}  "
              f"tokens {s['final_tokens']:.0f}  bad promoted {s['bad_promoted']:.2f}  "
              f"bad surviving {s['bad_surviving']:.2f}  rollbacks {s['rollbacks']:.2f}")


if __name__ == "__main__":
    main()
