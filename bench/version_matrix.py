# Copyright 2026 Salim - https://github.com/Bsalim451/VGI-tester

"""Run the SAME worker file against several DuckDB versions.

The point: a native extension must be rebuilt for every DuckDB version. A VGI
worker should work unchanged, as long as the vgi extension exists for that
DuckDB version. Run this once per DuckDB install:

    python bench/version_matrix.py <engine> <worker command> [sdk label]

engine = "haybarn" or "duckdb" (stock). The worker command is the ATTACH
LOCATION, so the worker can run in a different Python env (different vgi-python
SDK version) than the DuckDB engine.
Prints one JSON line with the result.
"""

from __future__ import annotations

import json
import sys

ENGINE, WORKER = sys.argv[1], sys.argv[2]
SDK_LABEL = sys.argv[3] if len(sys.argv) > 3 else "unknown"

if ENGINE == "haybarn":
    import haybarn as engine
else:
    import duckdb as engine

con = engine.connect()
version = con.execute("SELECT version()").fetchone()[0]
result = {"engine": ENGINE, "duckdb_version": version, "worker_sdk": SDK_LABEL}

try:
    if ENGINE == "haybarn":
        con.execute("INSTALL vgi FROM community")
        con.execute("LOAD vgi")
    else:
        try:
            con.execute("INSTALL vgi FROM community")
            con.execute("LOAD vgi")
        except Exception as community_error:
            if "HTTP 404" in str(community_error):
                raise RuntimeError(
                    f"no vgi extension build published for DuckDB {version} (community repo: HTTP 404)"
                ) from community_error
            con.execute("SET allow_extension_repositories = 'allowed'")
            con.execute(
                "CREATE EXTENSION REPOSITORY IF NOT EXISTS query_farm "
                "WITH PREFIX 'https://duckdb-extensions.query.farm'"
            )
            con.execute("INSTALL vgi FROM query_farm")
            con.execute("LOAD vgi FROM query_farm")
    result["vgi_extension"] = con.execute(
        "SELECT extension_version FROM duckdb_extensions() WHERE extension_name = 'vgi'"
    ).fetchone()[0]
    con.execute(f"ATTACH 'tester' (TYPE vgi, LOCATION '{WORKER}')")
    checks = {
        "upper_text": "SELECT tester.main.upper_text('duckdb') = upper('duckdb')",
        "add_numbers": "SELECT tester.main.add_numbers(40, 2) = 42",
        "sha256_hex": "SELECT tester.main.sha256_hex('abc') = sha256('abc')",
        "number_series": "SELECT sum(n) = 4950 FROM tester.main.number_series(100)",
    }
    result["checks"] = {k: bool(con.execute(q).fetchone()[0]) for k, q in checks.items()}
    result["works"] = all(result["checks"].values())
except Exception as e:  # noqa: BLE001
    result["works"] = False
    result["error"] = f"{type(e).__name__}: {str(e)[:300]}"

print(json.dumps(result))
