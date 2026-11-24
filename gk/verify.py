"""Verify the evidence: python -m gk.verify [experiments.jsonl] [calls.jsonl]

Checks, per record:
  chain      prev_hash links to the previous record and hash matches the record's content
  calls      every cited call_id exists in calls.jsonl
  numbers    held-out gain/p, placebo gain/p, regression drop, integrity drift, canary delta, cost
             growth and audit drop are RECOMPUTED from the stored per-world scores and compared with
             the numbers the gate recorded (and the pass/fail flags are re-derived from the thresholds)
  decision   accepted <=> all seven checks present and passing; rejected <=> some check failed
  hashes     prereg_sha and code_sha equal the current prereg/PREREG.md and gate/env/evaluator/stats code
Prints PASS/FAIL per check; exit code 0 if everything passes, else 1.
"""
from __future__ import annotations

import json
import sys

from .calllog import read_calls
from .chain import GENESIS, ROOT, code_sha, prereg_sha, record_hash
from .gate import GateConfig
from .stats import sign_flip_test

TOL = 2e-3
FULL = ["allowlist", "held-out gain", "beats placebo", "regression suite", "integrity", "canary", "cost"]


def load_rows(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    rows.append({"_corrupt": line[:80]})
    return rows


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def close(a, b, tol=TOL):
    return a is not None and b is not None and abs(a - b) <= tol


def _diffs(a, b):
    return [y - x for x, y in zip(a, b)]


def verify_numbers(r: dict, cfg: GateConfig) -> list[tuple]:
    """[(name, ok, detail)] for everything recomputable from r['suites']."""
    out, S = [], r.get("suites", {})
    by = {c["name"]: c for c in r.get("checks", [])}

    def sc(suite, who):
        return S.get(suite, {}).get(who, {}).get("scores")

    held_p, held_c, plc = sc("held", "parent"), sc("held", "child"), sc("held", "placebo")
    if "held-out gain" in by and held_p and held_c:
        t = sign_flip_test(_diffs(held_p, held_c))
        c = by["held-out gain"]
        ok = (c.get("n") == t["n"] and close(c.get("mean"), t["mean"]) and close(c.get("p"), t["p"], 5e-3)
              and c["pass"] == (t["p"] < cfg.alpha_held and t["mean"] >= cfg.min_gain))
        out.append(("held-out gain recomputed", ok,
                    f"stored mean {c.get('mean')} p {c.get('p')} / recomputed mean {t['mean']:.4f} p {t['p']:.4f}"))
    if "beats placebo" in by and plc and held_c:
        t = sign_flip_test(_diffs(plc, held_c))
        c = by["beats placebo"]
        ok = (close(c.get("mean"), t["mean"]) and close(c.get("p"), t["p"], 5e-3)
              and c["pass"] == (t["p"] < cfg.alpha_placebo and t["mean"] > 0))
        out.append(("placebo recomputed", ok,
                    f"stored mean {c.get('mean')} p {c.get('p')} / recomputed mean {t['mean']:.4f} p {t['p']:.4f}"))
    reg_p, reg_c = sc("regression", "parent"), sc("regression", "child")
    if "regression suite" in by and reg_p and reg_c:
        c, drop, pm = by["regression suite"], mean(reg_p) - mean(reg_c), mean(reg_p)
        want = drop <= cfg.regression_tol and pm >= cfg.regression_floor
        out.append(("regression recomputed", close(c.get("mean"), drop) and c["pass"] == want,
                    f"stored drop {c.get('mean')} / recomputed {drop:.4f} (parent mean {pm:.3f})"))
    can_p, can_c = sc("canary", "parent"), sc("canary", "child")
    if "canary" in by and can_p and can_c:
        c, d = by["canary"], mean(can_c) - mean(can_p)
        out.append(("canary recomputed", close(c.get("mean"), d) and c["pass"] == (d >= -cfg.canary_tol),
                    f"stored {c.get('mean')} / recomputed {d:.4f}"))
    hp, hc = S.get("held", {}).get("parent", {}), S.get("held", {}).get("child", {})
    if "integrity" in by and hp.get("claimed_scores") and hc.get("claimed_scores"):
        gap = lambda h: mean(h["claimed_scores"]) - mean(h["scores"])
        d = gap(hc) - gap(hp)
        c = by["integrity"]
        out.append(("integrity recomputed", close(c.get("mean"), d) and c["pass"] == (d <= cfg.integrity_gap_tol),
                    f"stored {c.get('mean')} / recomputed {d:.4f}"))
    if "cost" in by and hp.get("tokens") and hc.get("tokens"):
        g = mean(hc["tokens"]) / mean(hp["tokens"]) - 1
        c = by["cost"]
        gain = by.get("held-out gain", {}).get("mean", 0) or 0
        lim = cfg.relaxed_cost_growth if gain >= cfg.big_gain else cfg.max_cost_growth
        out.append(("cost recomputed", close(c.get("mean"), g) and c["pass"] == (g <= lim),
                    f"stored {c.get('mean')} / recomputed {g:.4f}"))
    au_p, au_c = sc("audit", "parent"), sc("audit", "child")
    if "post-acceptance audit" in by and au_p and au_c:
        c, drop = by["post-acceptance audit"], mean(au_p) - mean(au_c)
        out.append(("audit recomputed", close(c.get("value"), drop) and c["pass"] == (drop <= cfg.audit_tol),
                    f"stored {c.get('value')} / recomputed {drop:.4f}"))
    return out


def verify_decision(r: dict) -> tuple:
    kind, dec = r.get("kind"), r.get("decision")
    checks = r.get("checks", [])
    if kind == "none":
        return True, "no candidate was produced"
    if kind in ("audit", "rollback"):
        ok = bool(checks) and (dec == "kept") == all(c["pass"] for c in checks)
        return ok, f"{dec}: audit check {'passed' if all(c['pass'] for c in checks) else 'failed'}"
    names = [c["name"] for c in checks]
    if dec == "accepted":
        ok = names == FULL and all(c["pass"] for c in checks)
        return ok, "accepted requires all 7 checks present and passing" if not ok else "all 7 checks passed"
    ok = any(not c["pass"] for c in checks)
    return ok, "rejected requires a failing check" if not ok else f"rejected at '{[c for c in checks if not c['pass']][0]['name']}'"


def verify_files(exp_path: str, calls_path: str, root: str = ROOT) -> tuple:
    """-> (rows, results, row_ok): results = [(exp_id, check, ok, detail)], row_ok[i] = row i fully verified."""
    rows = load_rows(exp_path)
    calls = {c["call_id"] for c in read_calls(calls_path)}
    cfg = GateConfig()
    want_prereg, want_code = prereg_sha(root), code_sha(root)
    results, row_ok, prev, chain_broken = [], [], GENESIS, False
    for i, r in enumerate(rows):
        eid = r.get("exp_id", f"row{i + 1}")
        fails = 0

        def add(check, ok, detail=""):
            nonlocal fails
            results.append((eid, check, bool(ok), detail))
            fails += 0 if ok else 1

        if "_corrupt" in r:
            add("chain", False, "line is not valid JSON")
            chain_broken = True
            row_ok.append(False)
            continue
        link = r.get("prev_hash") == prev
        hash_ok = r.get("hash") == record_hash(r)
        if not (link and hash_ok):
            chain_broken = True
        why = ("ok" if link and hash_ok else
               "prev_hash does not match the previous record" if not link else
               "record content does not match its hash (edited?)")
        if chain_broken and link and hash_ok:
            why = "an earlier record broke the chain"
        add("chain", link and hash_ok and not chain_broken, why)
        prev = r.get("hash", "")
        missing = [c for c in r.get("call_ids", []) if c not in calls]
        add("calls", not missing, f"{len(r.get('call_ids', []))} cited" if not missing
            else f"{len(missing)} missing, e.g. {missing[0]}")
        for name, ok, detail in verify_numbers(r, cfg):
            add(name, ok, detail)
        ok, detail = verify_decision(r)
        add("decision consistent", ok, detail)
        add("prereg hash", r.get("prereg_sha") == want_prereg, "matches current prereg/PREREG.md"
            if r.get("prereg_sha") == want_prereg else "PREREG.md changed since this record")
        add("code hash", r.get("code_sha") == want_code, "matches current gate/env/evaluator/stats code"
            if r.get("code_sha") == want_code else "gate/env/evaluator/stats code changed since this record")
        row_ok.append(fails == 0)
    return rows, results, row_ok


def verified_rows(exp_path: str, calls_path: str, root: str = ROOT) -> tuple:
    rows, results, row_ok = verify_files(exp_path, calls_path, root)
    return [r for r, ok in zip(rows, row_ok) if ok], rows, results


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    exp = argv[0] if argv else "experiments.jsonl"
    calls = argv[1] if len(argv) > 1 else "calls.jsonl"
    try:
        rows, results, row_ok = verify_files(exp, calls)
    except FileNotFoundError as e:
        print(f"FAIL  cannot read {e.filename}")
        return 1
    for eid, check, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {eid}  {check}: {detail}")
    bad = sum(1 for *_, ok, _d in results if not ok)
    print(f"\n{len(rows)} records, {len(results)} checks, {bad} failed -> {'VERIFIED' if not bad else 'NOT VERIFIED'}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
