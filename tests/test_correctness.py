# Copyright 2026 Salim - https://github.com/Bsalim451/VGI-tester

"""Correctness: every VGI function must give the same answer as its native twin.

Runs real SQL through DuckDB (haybarn build) with the vgi extension, and compares
the VGI result with the native result on tricky inputs: NULLs, empty strings,
unicode, emoji, long strings.

    uv run pytest tests/test_correctness.py
"""

from __future__ import annotations

import os
import sys

import pytest

haybarn = pytest.importorskip("haybarn")

WORKER = f"{sys.executable} {os.path.join(os.path.dirname(__file__), '..', 'tester_worker.py')}"

# Edge cases on purpose. Each one has bitten someone, somewhere.
TRICKY_STRINGS = [
    None,
    "",
    " ",
    "duckdb",
    "DuckDB 2.0",
    "straße",          # German sharp s: upper() is 'STRASSE' or 'STRAßE' depending on the rules
    "ĳsselmeer",       # Dutch ij ligature
    "istanbul İı",     # Turkish dotted/dotless i
    "émile zola",
    "日本語のテキスト",
    "emoji 🦆🚜",
    "tab\tand\nnewline",
    "x" * 10_000,
]


@pytest.fixture(scope="module")
def con():
    """DuckDB connection with the worker attached and a table of tricky strings."""
    c = haybarn.connect()
    c.execute("INSTALL vgi FROM community")
    c.execute("LOAD vgi")
    c.execute(f"ATTACH 'tester' (TYPE vgi, LOCATION '{WORKER}')")
    c.execute("CREATE TABLE s (id INTEGER, a VARCHAR, b VARCHAR)")
    rows = [(i, x, TRICKY_STRINGS[(i + 3) % len(TRICKY_STRINGS)]) for i, x in enumerate(TRICKY_STRINGS)]
    c.executemany("INSERT INTO s VALUES (?, ?, ?)", rows)
    yield c
    c.close()


def mismatches(con, vgi_expr: str, native_expr: str, table: str = "s") -> list:
    """Rows where VGI and native disagree (NULL-safe compare)."""
    return con.execute(
        f"SELECT id, a, {vgi_expr} AS vgi, {native_expr} AS native FROM {table} "
        f"WHERE ({vgi_expr}) IS DISTINCT FROM ({native_expr})"
    ).fetchall()


def test_passthrough(con):
    assert mismatches(con, "tester.main.passthrough(a)", "a") == []


def test_upper(con):
    bad = mismatches(con, "tester.main.upper_text(a)", "upper(a)")
    assert bad == [], f"upper_text differs from upper() on: {[(r[1][:20], r[2][:20], r[3][:20]) for r in bad]}"


def test_sha256(con):
    assert mismatches(con, "tester.main.sha256_hex(a)", "sha256(a)") == []


def test_levenshtein_ascii(con):
    # Only plain ASCII pairs, and skip the 10k string (pure-Python DP on it is 100M steps).
    con.execute(
        "CREATE OR REPLACE TEMP VIEW s_ascii AS SELECT * FROM s "
        "WHERE (a IS NULL OR (length(a) < 100 AND strlen(a) = length(a))) "
        "AND (b IS NULL OR (length(b) < 100 AND strlen(b) = length(b)))"
    )
    assert con.execute("SELECT count(*) FROM s_ascii").fetchone()[0] >= 3
    bad = mismatches(con, "tester.main.levenshtein_distance(a, b)", "levenshtein(a, b)", table="s_ascii")
    assert bad == [], f"levenshtein differs on ASCII: {bad}"


def test_levenshtein_non_ascii_finding(con):
    """FINDING: DuckDB v2.0.0 levenshtein() counts UTF-8 bytes, not characters.

    'ß' -> 's' is one edit, but 'ß' is 2 bytes, so native says 2. The worker
    counts characters and says 1. This test pins the difference so we notice
    if DuckDB ever changes it.
    """
    vgi, native = con.execute(
        "SELECT tester.main.levenshtein_distance('ß', 's'), levenshtein('ß', 's')"
    ).fetchone()
    assert vgi == 1
    assert native in (1, 2)  # 2 on DuckDB v2.0.0 (bytes); 1 would mean DuckDB switched to characters


def test_add_numbers(con):
    bad = con.execute(
        "SELECT a, b FROM (SELECT (random() * 1e12)::BIGINT a, (random() * -1e12)::BIGINT b FROM range(50000)) "
        "WHERE tester.main.add_numbers(a, b) IS DISTINCT FROM a + b"
    ).fetchall()
    assert bad == []


def test_add_numbers_null(con):
    assert con.execute("SELECT tester.main.add_numbers(NULL, 1)").fetchone()[0] is None


def test_number_series_matches_range(con):
    for n in (0, 1, 2047, 2048, 2049, 100_000):
        vgi = con.execute(f"SELECT count(*), coalesce(sum(n), 0) FROM tester.main.number_series({n})").fetchone()
        native = con.execute(f"SELECT count(*), coalesce(sum(range), 0) FROM range({n})").fetchone()
        assert vgi == native, n


def test_python_error_becomes_sql_error(con):
    with pytest.raises(Exception, match="boom"):
        con.execute("SELECT tester.main.fail_with('x', 'boom')").fetchall()
    assert con.execute("SELECT 1").fetchone()[0] == 1


def test_worker_crash_does_not_kill_duckdb(con):
    with pytest.raises(Exception):
        con.execute("SELECT tester.main.kill_worker(1)").fetchall()
    # DuckDB is still alive and the next call starts a fresh worker.
    assert con.execute("SELECT tester.main.upper_text('alive')").fetchone()[0] == "ALIVE"


def test_overflow_errors_like_native(con):
    with pytest.raises(Exception, match="(?i)overflow"):
        con.execute("SELECT tester.main.add_numbers(9223372036854775807, 1)").fetchall()


def test_wrong_type_fails_at_bind(con):
    with pytest.raises(Exception, match="(?i)binder"):
        con.execute("SELECT tester.main.upper_text(42)").fetchall()
