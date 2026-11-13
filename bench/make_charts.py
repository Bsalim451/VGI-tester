# Copyright 2026 Salim - https://github.com/Bsalim451/VGI-tester

"""Turn results/*.csv|json into the PNG charts used in docs/.

    python bench/make_charts.py
"""

from __future__ import annotations

import csv
import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, "results")
DOCS = os.path.join(ROOT, "docs", "img")

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
GRID = "#e4e3df"
BLUE = "#2a78d6"    # series 1
ORANGE = "#eb6834"  # series 2

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 11, "text.color": TEXT,
    "axes.edgecolor": GRID, "axes.labelcolor": TEXT_2, "xtick.color": TEXT_2, "ytick.color": TEXT_2,
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
})


def slowdown_chart() -> None:
    with open(os.path.join(RESULTS, "bench_throughput.csv")) as f:
        rows = [r for r in csv.DictReader(f) if int(r["rows"]) == 100_000]
    nice = {
        "passthrough": "passthrough  (no work, pure overhead)",
        "upper": "upper_text  vs upper()",
        "add": "add_numbers  vs a + b",
        "sha256": "sha256_hex  vs sha256()",
        "levenshtein": "levenshtein_distance  vs levenshtein()",
        "series (table)": "number_series  vs range()",
    }
    rows.sort(key=lambda r: float(r["vgi_ms"]) / float(r["native_ms"]))
    labels = [nice[r["function"]] for r in rows]
    values = [float(r["vgi_ms"]) / float(r["native_ms"]) for r in rows]

    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=150)
    bars = ax.barh(labels, values, color=BLUE, height=0.55)
    for b, v, r in zip(bars, values, rows):
        ax.text(b.get_width() + 0.5, b.get_y() + b.get_height() / 2,
                f"{v:.1f}×   ({float(r['native_ms']):.1f} ms → {float(r['vgi_ms']):.1f} ms)",
                va="center", fontsize=9.5, color=TEXT)
    ax.axvline(1, color=TEXT_2, linewidth=1)
    ax.set_xlim(0, max(values) * 1.8)
    ax.set_xlabel("times slower than native DuckDB (1× = same speed)")
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.set_title("VGI worker vs native DuckDB, 100,000 rows (median of 5 runs)", loc="left", fontsize=12.5, pad=12)
    fig.text(0.01, 0.01, "DuckDB v2.0.0 · vgi-python 0.51.1 · 2 vCPU Linux · warm worker", fontsize=8.5, color=TEXT_2)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(os.path.join(DOCS, "vgi_vs_native_slowdown.png"))
    plt.close(fig)


def laya_vs_keyword_chart() -> None:
    with open(os.path.join(RESULTS, "laya_results.json")) as f:
        d = json.load(f)
    ii = d["is_interesting"]
    groups = [
        ("Headlines\nall\n(n=56)", ii["laya_all"]["accuracy"], ii["keyword_all"]["accuracy"]),
    ]
    for kind, title in (("keyword_trap", "Keyword traps"), ("no_keyword", "No keywords"), ("dutch", "Dutch")):
        k = ii["by_kind"][kind]
        groups.append((f"Headlines\n{title.lower()}\n(n={k['n']})", k["laya_correct"] / k["n"], k["keyword_correct"] / k["n"]))
    ch = d["choice"]
    groups.append((f"Tickets\nrouting\n(n={sum(v['n'] for v in ch['by_kind'].values())})", ch["laya_accuracy"], ch["keyword_accuracy"]))

    fig, ax = plt.subplots(figsize=(9.5, 4.4), dpi=150)
    x = range(len(groups))
    w = 0.36
    b1 = ax.bar([i - w / 2 - 0.01 for i in x], [g[1] * 100 for g in groups], width=w, color=BLUE, label="vgi-laya (ModernBERT)")
    b2 = ax.bar([i + w / 2 + 0.01 for i in x], [g[2] * 100 for g in groups], width=w, color=ORANGE, label="Keyword match in plain SQL")
    for bars in (b1, b2):
        for b in bars:
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1.5, f"{b.get_height():.0f}%",
                    ha="center", fontsize=9.5, color=TEXT)
    ax.set_xticks(list(x), [g[0] for g in groups], fontsize=9.5)
    ax.set_ylim(0, 112)
    ax.set_yticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    ax.set_ylabel("accuracy")
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.tick_params(axis="x", length=0)
    ax.legend(loc="upper left", bbox_to_anchor=(0, 1.13), ncol=2, frameon=False, fontsize=9.5)
    ax.set_title("vgi-laya vs a keyword filter, hand-labelled test sets", loc="left", fontsize=12.5, pad=30)
    fig.text(0.01, 0.01, "Threshold 0.5 for headlines. Small sets: read the gaps, not the decimals.", fontsize=8.5, color=TEXT_2)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(os.path.join(DOCS, "laya_vs_keyword.png"))
    plt.close(fig)


if __name__ == "__main__":
    os.makedirs(DOCS, exist_ok=True)
    slowdown_chart()
    laya_vs_keyword_chart()
    print("charts written to", DOCS)
