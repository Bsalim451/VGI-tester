# Copyright 2026 Salim - https://github.com/Bsalim451/VGI-tester

"""Speed: VGI worker vs native DuckDB, same work, same data.

Measures three things:

1. Cold start: LOAD vgi + ATTACH + first call, in a fresh connection.
2. Warm per-query overhead: a 1-row call when the worker is already running.
3. Throughput: same function on 1k .. 1M rows, VGI vs native.

Every query wraps the function in max() so DuckDB must compute every row, but
only one value comes back to Python. That way we time the function, not the
result transfer.

    uv run python bench/bench_native_vs_vgi.py            # writes results/bench_*.csv
"""

from __future__ import annotations

import csv
import json
import os
import platform
import statistics
import sys
import time

import haybarn

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULTS = os.path.join(ROOT, "results")
WORKER = f"{sys.executable} {os.path.join(ROOT, 'tester_worker.py')}"

REPEATS = 5
SIZES = [1_000, 10_000, 100_000, 1_000_000]

# name, vgi expression, native expression, max rows (pure-Python levenshtein is slow)
PAIRS = [
    ("passthrough", "tester.main.passthrough(s)", "s", 1_000_000),
    ("upper", "tester.main.upper_text(s)", "upper(s)", 1_000_000),
    ("add", "tester.main.add_numbers(a, b)", "a + b", 1_000_000),
    ("sha256", "tester.main.sha256_hex(s)", "sha256(s)", 1_000_000),
    ("levenshtein", "tester.main.levenshtein_distance(s, s2)", "levenshtein(s, s2)", 100_000),
]


def connect():
    """Fresh connection with the vgi extension and the worker attached."""
    con = haybarn.connect()
    con.execute("LOAD vgi")
    con.execute(f"ATTACH 'tester' (TYPE vgi, LOCATION '{WORKER}')")
    return con


def timed(con, sql: str) -> float:
    """Wall time of one query in milliseconds."""
    start = time.perf_counter()
    con.execute(sql).fetchall()
    return (time.perf_counter() - start) * 1000


def median_ms(con, sql: str, repeats: int = REPEATS) -> tuple[float, list[float]]:
    """Run once to warm up, then median of ``repeats`` runs."""
    con.execute(sql).fetchall()
    runs = [timed(con, sql) for _ in range(repeats)]
    return statistics.median(runs), runs


_COLD_SCRIPT = """
import json, sys, time
import haybarn
t0 = time.perf_counter()
con = haybarn.connect()
con.execute("LOAD vgi")
t1 = time.perf_counter()
con.execute("ATTACH 'tester' (TYPE vgi, LOCATION '" + sys.argv[1] + "')")
t2 = time.perf_counter()
con.execute("SELECT tester.main.passthrough('x')").fetchall()
t3 = time.perf_counter()
con.execute("SELECT tester.main.passthrough('x')").fetchall()
t4 = time.perf_counter()
print(json.dumps({"load_vgi_ms": (t1-t0)*1000, "attach_ms": (t2-t1)*1000,
                  "first_call_ms": (t3-t2)*1000, "second_call_ms": (t4-t3)*1000}))
"""


def cold_start(runs: int = 5) -> list[dict]:
    """Time LOAD + ATTACH + first call, each run in a brand-new Python process.

    A new process matters: inside one process the vgi extension keeps a pool of
    worker subprocesses alive across connections, so a second connection would
    hit a warm worker and look much faster than a real first start.
    """
    import subprocess

    rows = []
    for i in range(runs):
        out = subprocess.run(
            [sys.executable, "-c", _COLD_SCRIPT, WORKER], capture_output=True, text=True, check=True
        ).stdout.strip().splitlines()[-1]
        rows.append({"run": i, **json.loads(out)})
    return rows


def main() -> None:
    os.makedirs(RESULTS, exist_ok=True)

    # 1. Cold start (before anything else in this process touches the worker)
    cold = cold_start()
    with open(os.path.join(RESULTS, "bench_cold_start.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(cold[0].keys()))
        w.writeheader()
        w.writerows(cold)
    print("cold start (median ms):", {k: round(statistics.median(r[k] for r in cold), 1) for k in cold[0] if k != "run"})

    con = connect()
    threads = con.execute("SELECT current_setting('threads')").fetchone()[0]

    # 2. Warm per-query overhead on one row
    one_vgi, _ = median_ms(con, "SELECT tester.main.passthrough('x')", repeats=50)
    one_native, _ = median_ms(con, "SELECT 'x'", repeats=50)
    print(f"1-row query: native {one_native:.2f} ms, vgi {one_vgi:.2f} ms")

    # 3. Throughput
    con.execute(
        f"CREATE TABLE t AS SELECT i, md5(i::VARCHAR) AS s, md5((i * 7)::VARCHAR)[1:20] AS s2, "
        f"(i * 31) % 1000003 AS a, i % 977 AS b FROM range({max(SIZES)}) r(i)"
    )
    rows = []
    for name, vgi_expr, native_expr, cap in PAIRS:
        for n in SIZES:
            if n > cap:
                continue
            view = f"(SELECT * FROM t WHERE i < {n})"
            native_ms, native_runs = median_ms(con, f"SELECT max({native_expr}) FROM {view}")
            vgi_ms, vgi_runs = median_ms(con, f"SELECT max({vgi_expr}) FROM {view}")
            same = con.execute(
                f"SELECT count(*) FROM {view} WHERE ({vgi_expr}) IS DISTINCT FROM ({native_expr})"
            ).fetchone()[0]
            row = {
                "function": name, "rows": n,
                "native_ms": round(native_ms, 2), "vgi_ms": round(vgi_ms, 2),
                "slowdown_x": round(vgi_ms / native_ms, 1) if native_ms > 0 else None,
                "vgi_us_per_row": round(vgi_ms * 1000 / n, 2),
                "mismatching_rows": same,
                "native_runs_ms": [round(x, 2) for x in native_runs],
                "vgi_runs_ms": [round(x, 2) for x in vgi_runs],
            }
            rows.append(row)
            print(f"{name:12} {n:>9,} rows  native {native_ms:9.2f} ms  vgi {vgi_ms:9.2f} ms  x{row['slowdown_x']}  diff={same}")

    # 4. Table function: number_series vs range
    for n in [1_000, 100_000, 1_000_000, 10_000_000]:
        native_ms, native_runs = median_ms(con, f"SELECT sum(range) FROM range({n})")
        vgi_ms, vgi_runs = median_ms(con, f"SELECT sum(n) FROM tester.main.number_series({n})")
        rows.append({
            "function": "series (table)", "rows": n,
            "native_ms": round(native_ms, 2), "vgi_ms": round(vgi_ms, 2),
            "slowdown_x": round(vgi_ms / native_ms, 1) if native_ms > 0 else None,
            "vgi_us_per_row": round(vgi_ms * 1000 / n, 3),
            "mismatching_rows": 0 if con.execute(
                f"SELECT (SELECT sum(range) FROM range({n})) = (SELECT sum(n) FROM tester.main.number_series({n}))"
            ).fetchone()[0] else -1,
            "native_runs_ms": [round(x, 2) for x in native_runs],
            "vgi_runs_ms": [round(x, 2) for x in vgi_runs],
        })
        print(f"series       {n:>9,} rows  native {native_ms:9.2f} ms  vgi {vgi_ms:9.2f} ms")

    with open(os.path.join(RESULTS, "bench_throughput.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({**r, "native_runs_ms": json.dumps(r["native_runs_ms"]), "vgi_runs_ms": json.dumps(r["vgi_runs_ms"])})

    env = {
        "duckdb_version": con.execute("SELECT version()").fetchone()[0],
        "vgi_extension": con.execute(
            "SELECT extension_version FROM duckdb_extensions() WHERE extension_name = 'vgi'"
        ).fetchone()[0],
        "threads": threads,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "repeats": REPEATS,
        "one_row_native_ms": round(one_native, 3),
        "one_row_vgi_ms": round(one_vgi, 3),
        "date": time.strftime("%Y-%m-%d %H:%M"),
    }
    with open(os.path.join(RESULTS, "bench_env.json"), "w") as f:
        json.dump(env, f, indent=2)
    print(json.dumps(env, indent=2))


if __name__ == "__main__":
    main()
