# Copyright 2026 Salim - https://github.com/Bsalim451/VGI-tester

"""Test Lorenzo's vgi-laya worker through real SQL.

What we check:

1. Quality      is_interesting / choice / score against small hand-labelled sets,
                next to a plain-SQL keyword baseline (what you can do natively).
2. Consistency  same input twice, negated question, shuffled / renamed options,
                paraphrased instructions. A model that "hallucinates" tends to
                fail these even when accuracy looks fine.
3. Errors       NULL, empty and huge inputs, bad arguments. Does it fail loudly
                at bind time, or silently return something?
4. Speed        cold start (model load), warm ms per row, and keyword baseline.

    python bench/test_laya.py "<python> /path/to/vgi-laya/laya_worker.py"

Writes results/laya_*.json and results/laya_rows_*.csv.
"""

from __future__ import annotations

import csv
import json
import os
import statistics
import subprocess
import sys
import time

import haybarn

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(HERE, "laya_data")
RESULTS = os.path.join(ROOT, "results")
WORKER = sys.argv[1]

TOPIC = "About databases, SQL or data engineering"
TOPIC_PARAPHRASE = "Is this headline about storing, querying or processing data in a database system?"
TOPIC_NEGATED = "Is this headline NOT about databases, SQL or data engineering?"
KEYWORDS = r"database|sql|query|queries|index|postgres|mysql|duckdb|sqlite|data pipeline|data lake|warehouse"

TEAMS = "MAP {'shipping': 'Delivery issues, lost or late packages', 'billing': 'Payment problems, refunds and invoices', 'support': 'General product questions and how-to help'}"
TEAMS_SHUFFLED = "MAP {'support': 'General product questions and how-to help', 'billing': 'Payment problems, refunds and invoices', 'shipping': 'Delivery issues, lost or late packages'}"
TEAMS_RENAMED = "MAP {'a': 'Delivery issues, lost or late packages', 'b': 'Payment problems, refunds and invoices', 'c': 'General product questions and how-to help'}"
RENAMED_BACK = {"a": "shipping", "b": "billing", "c": "support"}
SEVERITY = "['minor inconvenience', 'degraded service', 'major outage', 'critical emergency']"


def connect():
    con = haybarn.connect()
    con.execute("LOAD vgi")
    con.execute(f"ATTACH 'laya' (TYPE vgi, LOCATION '{WORKER}')")
    for name in ("headlines", "tickets", "incidents"):
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM read_csv('{DATA}/{name}.csv', header=true)")
    return con


def binary_metrics(rows: list[tuple[int, int]]) -> dict:
    """rows = (label, prediction). Returns accuracy, precision, recall, F1."""
    tp = sum(1 for y, p in rows if y == 1 and p == 1)
    fp = sum(1 for y, p in rows if y == 0 and p == 1)
    fn = sum(1 for y, p in rows if y == 1 and p == 0)
    tn = sum(1 for y, p in rows if y == 0 and p == 0)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"n": len(rows), "accuracy": round((tp + tn) / len(rows), 3), "precision": round(precision, 3),
            "recall": round(recall, 3), "f1": round(f1, 3), "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def spearman(xs: list[float], ys: list[float]) -> float:
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    return cov / (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5


def try_sql(con, sql: str) -> dict:
    start = time.perf_counter()
    try:
        rows = con.execute(sql).fetchall()
        return {"ok": True, "ms": round((time.perf_counter() - start) * 1000, 1), "result": [list(r) for r in rows][:3]}
    except Exception as e:  # noqa: BLE001
        msg = str(e).split("Traceback")[0].strip()
        return {"ok": False, "ms": round((time.perf_counter() - start) * 1000, 1), "error": f"{type(e).__name__}: {msg[:300]}"}


_COLD = """
import sys, time, haybarn
t0 = time.perf_counter()
con = haybarn.connect(); con.execute("LOAD vgi")
con.execute("ATTACH 'laya' (TYPE vgi, LOCATION '" + sys.argv[1] + "')")
t1 = time.perf_counter()
con.execute("SELECT laya.main.is_interesting('DuckDB 2.0 is out', 'About databases')").fetchall()
t2 = time.perf_counter()
con.execute("SELECT laya.main.is_interesting('Cat video goes viral', 'About databases')").fetchall()
t3 = time.perf_counter()
print((t1-t0)*1000, (t2-t1)*1000, (t3-t2)*1000)
"""


def main() -> None:
    os.makedirs(RESULTS, exist_ok=True)
    out: dict = {"worker": WORKER}

    # ---- 4a. Cold start, fresh process (includes loading ModernBERT) ----
    cold = []
    for _ in range(3):
        line = subprocess.run([sys.executable, "-c", _COLD, WORKER], capture_output=True, text=True, check=True)
        a, f, s = (float(x) for x in line.stdout.split()[-3:])
        cold.append({"load_and_attach_ms": round(a, 1), "first_call_ms": round(f, 1), "second_call_ms": round(s, 1)})
    out["cold_start"] = cold
    print("cold start:", cold)

    con = connect()

    # ---- 1a. is_interesting vs keyword baseline ----
    t = time.perf_counter()
    rows = con.execute(f"""
        SELECT id, text, label, kind,
               laya.main.is_interesting(text, '{TOPIC}') AS p,
               regexp_matches(lower(text), '{KEYWORDS}') AS kw
        FROM headlines ORDER BY id
    """).fetchall()
    laya_ms = (time.perf_counter() - t) * 1000
    t = time.perf_counter()
    con.execute(f"SELECT count(*) FROM headlines WHERE regexp_matches(lower(text), '{KEYWORDS}')").fetchall()
    kw_ms = (time.perf_counter() - t) * 1000
    with open(os.path.join(RESULTS, "laya_rows_headlines.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "text", "label", "kind", "laya_p", "keyword_match"])
        w.writerows([[r[0], r[1], r[2], r[3], round(r[4], 4), int(r[5])] for r in rows])
    out["is_interesting"] = {
        "instructions": TOPIC,
        "threshold": 0.5,
        "laya_all": binary_metrics([(r[2], int(r[4] > 0.5)) for r in rows]),
        "keyword_all": binary_metrics([(r[2], int(r[5])) for r in rows]),
        "by_kind": {
            kind: {
                "laya_correct": sum(1 for r in rows if r[3] == kind and int(r[4] > 0.5) == r[2]),
                "keyword_correct": sum(1 for r in rows if r[3] == kind and int(r[5]) == r[2]),
                "n": sum(1 for r in rows if r[3] == kind),
            }
            for kind in sorted({r[3] for r in rows})
        },
        "best_threshold": max(
            ({"threshold": th, **binary_metrics([(r[2], int(r[4] > th)) for r in rows])} for th in [x / 20 for x in range(1, 20)]),
            key=lambda m: m["f1"],
        ),
        "mean_p_label1": round(statistics.mean(r[4] for r in rows if r[2] == 1), 3),
        "mean_p_label0": round(statistics.mean(r[4] for r in rows if r[2] == 0), 3),
        "laya_total_ms": round(laya_ms, 1),
        "laya_ms_per_row": round(laya_ms / len(rows), 1),
        "keyword_total_ms": round(kw_ms, 2),
    }
    print("is_interesting:", json.dumps(out["is_interesting"]["laya_all"]), "| keyword:", json.dumps(out["is_interesting"]["keyword_all"]))

    # ---- 2a. Consistency on headlines: repeat, paraphrase, negation ----
    p1 = {r[0]: r[4] for r in rows}
    again = dict(con.execute(f"SELECT id, laya.main.is_interesting(text, '{TOPIC}') FROM headlines").fetchall())
    para = dict(con.execute(f"SELECT id, laya.main.is_interesting(text, '{TOPIC_PARAPHRASE}') FROM headlines").fetchall())
    neg = dict(con.execute(f"""
        SELECT h.id, n.noul FROM headlines h, LATERAL laya.main.noul(h.text, instructions => '{TOPIC_NEGATED}') n
    """).fetchall())
    labels = {r[0]: r[2] for r in rows}
    out["consistency_headlines"] = {
        "repeat_max_abs_diff": round(max(abs(p1[i] - again[i]) for i in p1), 6),
        "paraphrase_same_decision_pct": round(100 * sum((p1[i] > 0.5) == (para[i] > 0.5) for i in p1) / len(p1), 1),
        "paraphrase_metrics": binary_metrics([(labels[i], int(para[i] > 0.5)) for i in p1]),
        "negation_mean_abs(p+q-1)": round(statistics.mean(abs(p1[i] + neg[i] - 1) for i in p1), 3),
        "negation_both_above_0.5": sum(1 for i in p1 if p1[i] > 0.5 and neg[i] > 0.5),
        "negation_metrics_if_flipped": binary_metrics([(labels[i], int(neg[i] < 0.5)) for i in p1]),
    }
    print("consistency:", json.dumps(out["consistency_headlines"]))

    # ---- 1b. choice on tickets, 2b. order / rename consistency ----
    def choice(criteria: str) -> dict:
        return {r[0]: (r[1], r[2]) for r in con.execute(f"""
            SELECT t.id, c.choice, c.confidence FROM tickets t,
                   LATERAL laya.main.choice(t.text, instructions => 'Which team should handle this ticket?',
                                            criteria => {criteria}) c
        """).fetchall()}

    t = time.perf_counter()
    base = choice(TEAMS)
    choice_ms = (time.perf_counter() - t) * 1000
    shuffled = choice(TEAMS_SHUFFLED)
    renamed = choice(TEAMS_RENAMED)
    tickets = con.execute("SELECT id, text, label, kind FROM tickets ORDER BY id").fetchall()
    kw_choice = dict(con.execute("""
        SELECT id, CASE
            WHEN regexp_matches(lower(text), 'deliver|package|parcel|ship|tracking|courier|customs|arriv|sent|address') THEN 'shipping'
            WHEN regexp_matches(lower(text), 'charge|invoice|refund|pay|price|bank|discount|vat|transaction') THEN 'billing'
            ELSE 'support' END
        FROM tickets
    """).fetchall())
    with open(os.path.join(RESULTS, "laya_rows_tickets.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "text", "label", "kind", "laya_choice", "laya_confidence", "shuffled_choice", "renamed_choice", "keyword_choice"])
        for i, text, label, kind in tickets:
            w.writerow([i, text, label, kind, base[i][0], round(base[i][1], 4), shuffled[i][0],
                        RENAMED_BACK.get(renamed[i][0], renamed[i][0]), kw_choice[i]])
    confusion: dict = {}
    for i, _, label, _ in tickets:
        confusion.setdefault(label, {}).setdefault(base[i][0], 0)
        confusion[label][base[i][0]] += 1
    out["choice"] = {
        "laya_accuracy": round(sum(base[i][0] == lab for i, _, lab, _ in tickets) / len(tickets), 3),
        "keyword_accuracy": round(sum(kw_choice[i] == lab for i, _, lab, _ in tickets) / len(tickets), 3),
        "by_kind": {
            kind: {"laya_correct": sum(1 for i, _, lab, k in tickets if k == kind and base[i][0] == lab),
                   "keyword_correct": sum(1 for i, _, lab, k in tickets if k == kind and kw_choice[i] == lab),
                   "n": sum(1 for *_, k in tickets if k == kind)}
            for kind in sorted({k for *_, k in tickets})
        },
        "confusion_label_to_predicted": confusion,
        "shuffled_options_same_answer_pct": round(100 * sum(base[i][0] == shuffled[i][0] for i in base) / len(base), 1),
        "renamed_options_same_answer_pct": round(
            100 * sum(base[i][0] == RENAMED_BACK.get(renamed[i][0]) for i in base) / len(base), 1),
        "mean_confidence_correct": round(statistics.mean(base[i][1] for i, _, lab, _ in tickets if base[i][0] == lab), 3),
        "mean_confidence_wrong": round(statistics.mean([base[i][1] for i, _, lab, _ in tickets if base[i][0] != lab] or [float("nan")]), 3),
        "ms_per_row": round(choice_ms / len(tickets), 1),
    }
    print("choice:", json.dumps({k: v for k, v in out["choice"].items() if k != "confusion_label_to_predicted"}))

    # ---- 1c. score on incidents ----
    sc = con.execute(f"""
        SELECT i.id, i.text, i.label, s.score, s.confidence FROM incidents i,
               LATERAL laya.main.score(i.text, instructions => 'How severe is this incident?', criteria => {SEVERITY}) s
        ORDER BY i.id
    """).fetchall()
    with open(os.path.join(RESULTS, "laya_rows_incidents.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "text", "label_0_to_3", "laya_score", "laya_confidence"])
        w.writerows([[r[0], r[1], r[2], round(r[3], 4), round(r[4], 4)] for r in sc])
    out["score"] = {
        "spearman": round(spearman([r[2] for r in sc], [r[3] for r in sc]), 3),
        "mean_abs_error_vs_label_div_3": round(statistics.mean(abs(r[3] - r[2] / 3) for r in sc), 3),
        "mean_score_per_label": {lab: round(statistics.mean(r[3] for r in sc if r[2] == lab), 3) for lab in range(4)},
    }
    print("score:", json.dumps(out["score"]))

    # ---- 3. Edge cases and error handling ----
    long_text = "databases " * 2000
    cases = {
        "null_input": "SELECT laya.main.is_interesting(NULL, 'About databases')",
        "empty_string": "SELECT laya.main.is_interesting('', 'About databases')",
        "whitespace_only": "SELECT laya.main.is_interesting('   ', 'About databases')",
        "emoji_only": "SELECT laya.main.is_interesting('🦆🦆🦆', 'About databases')",
        "very_long_20k_chars": f"SELECT laya.main.is_interesting('{long_text}', 'About databases')",
        "empty_instructions": "SELECT laya.main.is_interesting('DuckDB', '')",
        "null_instructions": "SELECT laya.main.is_interesting('DuckDB', NULL)",
        "column_as_instructions": "SELECT laya.main.is_interesting(text, text) FROM headlines LIMIT 2",
        "noul_missing_instructions": "SELECT * FROM laya.main.noul('DuckDB')",
        "choice_missing_criteria": "SELECT * FROM laya.main.choice('My package is late', instructions => 'Which team?')",
        "choice_one_option": "SELECT * FROM laya.main.choice('My package is late', instructions => 'Which team?', criteria => MAP {'shipping': 'Delivery'})",
        "choice_empty_map": "SELECT * FROM laya.main.choice('My package is late', instructions => 'Which team?', criteria => MAP {})",
        "score_one_level": "SELECT * FROM laya.main.score('Site is down', instructions => 'How bad?', criteria => ['bad'])",
        "score_empty_list": "SELECT * FROM laya.main.score('Site is down', instructions => 'How bad?', criteria => [])",
        "choice_null_text": "SELECT * FROM laya.main.choice(NULL, instructions => 'Which team?', criteria => MAP {'a': 'x', 'b': 'y'})",
    }
    out["edge_cases"] = {name: try_sql(con, sql) for name, sql in cases.items()}
    for name, r in out["edge_cases"].items():
        print(f"  {name:28} ok={r['ok']}  {r.get('result', r.get('error'))}")

    # ---- 4b. Warm speed per row ----
    speed = []
    for n in (1, 10, 50):
        sql = f"SELECT max(laya.main.is_interesting(text, '{TOPIC}')) FROM (SELECT * FROM headlines LIMIT {n})"
        con.execute(sql).fetchall()
        runs = []
        for _ in range(3):
            s = time.perf_counter()
            con.execute(sql).fetchall()
            runs.append((time.perf_counter() - s) * 1000)
        speed.append({"rows": n, "median_ms": round(statistics.median(runs), 1), "ms_per_row": round(statistics.median(runs) / n, 1)})
    out["warm_speed"] = speed
    print("warm speed:", speed)

    with open(os.path.join(RESULTS, "laya_results.json"), "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
