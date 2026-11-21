# Copyright 2026 Salim - https://github.com/Bsalim451/VGI-tester

"""Outside SQL: is Laya's predict_batch faster than vgi-laya's per-row predict loop?

vgi-laya calls agent.predict() once per row. Laya also has predict_batch().
This checks speed and whether the answers stay the same. Also looks at the two
confidence fields Laya returns for choice questions.

    python bench/laya_batch_check.py
"""

from __future__ import annotations

import csv
import json
import os
import time

from laya import Agent

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(os.path.dirname(HERE), "results")

with open(os.path.join(HERE, "laya_data", "headlines.csv")) as f:
    headlines = [r["text"] for r in csv.DictReader(f)]
with open(os.path.join(HERE, "laya_data", "tickets.csv")) as f:
    tickets = [r["text"] for r in csv.DictReader(f)]

agent = Agent()
q = {"q": {"type": "noul", "instructions": "About databases, SQL or data engineering"}}
agent.predict("warm up", q)

t = time.perf_counter()
one_by_one = [agent.predict(s, q)["answers"]["q"]["noul"] for s in headlines]
loop_s = time.perf_counter() - t

batch_runs = {}
for bs in (8, 16, 32):
    t = time.perf_counter()
    batched = [r["answers"]["q"]["noul"] for r in agent.predict_batch(headlines, q, batch_size=bs)]
    batch_runs[bs] = {
        "seconds": round(time.perf_counter() - t, 2),
        "max_abs_diff_vs_loop": round(max(abs(a - b) for a, b in zip(one_by_one, batched)), 4),
        "same_decision_pct": round(100 * sum((a > 0.5) == (b > 0.5) for a, b in zip(one_by_one, batched)) / len(batched), 1),
    }

teams = {"type": "choice", "instructions": "Which team should handle this ticket?", "criteria": {
    "shipping": "Delivery issues, lost or late packages",
    "billing": "Payment problems, refunds and invoices",
    "support": "General product questions and how-to help"}}
answers = [agent.predict(s, {"q": teams})["answers"]["q"] for s in tickets]
conf = [a["confidence"] for a in answers]
ans_conf = [a["answer_confidence"] for a in answers]

out = {
    "rows": len(headlines),
    "loop_seconds": round(loop_s, 2),
    "loop_ms_per_row": round(1000 * loop_s / len(headlines), 1),
    "predict_batch": batch_runs,
    "best_speedup_x": round(loop_s / min(r["seconds"] for r in batch_runs.values()), 2),
    "choice_confidence_fields": {
        "rows": len(answers),
        "confidence_over_0.8": sum(c > 0.8 for c in conf),
        "answer_confidence_over_0.8": sum(c > 0.8 for c in ans_conf),
        "mean_confidence": round(sum(conf) / len(conf), 3),
        "mean_answer_confidence": round(sum(ans_conf) / len(ans_conf), 3),
    },
}
os.makedirs(RESULTS, exist_ok=True)
with open(os.path.join(RESULTS, "laya_batch_check.json"), "w") as f:
    json.dump(out, f, indent=2)
print(json.dumps(out, indent=2))
