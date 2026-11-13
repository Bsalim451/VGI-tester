# Copyright 2026 Salim - https://github.com/Bsalim451/VGI-tester

"""Scalar functions that each have a native DuckDB twin.

Every function here does the same job as a built-in DuckDB function, so the
benchmark can check two things per function:

1. Correctness: does the VGI result match the native result row for row?
2. Speed: how much slower is the round trip through the worker?

| VGI function             | Native DuckDB twin     | Kind of work          |
| ------------------------ | ---------------------- | --------------------- |
| passthrough(s)           | s                      | none (pure overhead)  |
| upper_text(s)            | upper(s)               | vectorised (Arrow)    |
| add_numbers(a, b)        | a + b                  | vectorised (Arrow)    |
| sha256_hex(s)            | sha256(s)              | per-row Python loop   |
| levenshtein_distance(a,b)| levenshtein(a, b)      | per-row Python loop   |
"""

from __future__ import annotations

import hashlib
from typing import Annotated

import pyarrow as pa
import pyarrow.compute as pc
from vgi.arguments import Param, Returns
from vgi.metadata import FunctionExample
from vgi.scalar_function import ScalarFunction


class PassthroughFunction(ScalarFunction):
    """Return the input unchanged.

    Zero compute. The whole cost is the trip from DuckDB to the worker and
    back, so this is the clean way to measure VGI overhead.
    """

    class Meta:
        """Catalog metadata."""

        name = "passthrough"
        description = "Returns the input string unchanged (measures pure VGI overhead)"
        examples = [FunctionExample(sql="SELECT tester.main.passthrough('hi')", description="Echo a value")]

    @classmethod
    def compute(
        cls,
        value: Annotated[pa.StringArray, Param(doc="Any string")],
    ) -> Annotated[pa.StringArray, Returns()]:
        """Echo the batch back."""
        return value


class UpperTextFunction(ScalarFunction):
    """Uppercase a string. Native twin: ``upper(s)``."""

    class Meta:
        """Catalog metadata."""

        name = "upper_text"
        description = "Uppercase a string (same as DuckDB upper())"
        examples = [FunctionExample(sql="SELECT tester.main.upper_text('duckdb')", description="Uppercase")]

    @classmethod
    def compute(
        cls,
        value: Annotated[pa.StringArray, Param(doc="String to uppercase")],
    ) -> Annotated[pa.StringArray, Returns()]:
        """Uppercase the whole batch at once with Arrow compute."""
        return pc.utf8_upper(value)


class AddNumbersFunction(ScalarFunction):
    """Add two integers. Native twin: ``a + b``."""

    class Meta:
        """Catalog metadata."""

        name = "add_numbers"
        description = "Add two BIGINT columns (same as a + b)"
        examples = [FunctionExample(sql="SELECT tester.main.add_numbers(1, 2)", description="Returns 3")]

    @classmethod
    def compute(
        cls,
        a: Annotated[pa.Int64Array, Param(doc="Left operand")],
        b: Annotated[pa.Int64Array, Param(doc="Right operand")],
    ) -> Annotated[pa.Int64Array, Returns()]:
        """Add the batch at once. ``add_checked`` errors on overflow, like DuckDB does."""
        return pc.add_checked(a, b)


class Sha256HexFunction(ScalarFunction):
    """SHA-256 of a string as lowercase hex. Native twin: ``sha256(s)``."""

    class Meta:
        """Catalog metadata."""

        name = "sha256_hex"
        description = "Lowercase hex SHA-256 of the UTF-8 string (same as DuckDB sha256())"
        examples = [FunctionExample(sql="SELECT tester.main.sha256_hex('abc')", description="Hash a string")]

    @classmethod
    def compute(
        cls,
        value: Annotated[pa.StringArray, Param(doc="String to hash")],
    ) -> Annotated[pa.StringArray, Returns()]:
        """Hash row by row in Python (hashlib is C, the loop is Python)."""
        out = [None if s is None else hashlib.sha256(s.encode("utf-8")).hexdigest() for s in value.to_pylist()]
        return pa.array(out, type=pa.string())


def _levenshtein(a: str, b: str) -> int:
    """Classic two-row dynamic programming edit distance."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


class LevenshteinFunction(ScalarFunction):
    """Edit distance between two strings. Native twin: ``levenshtein(a, b)``."""

    class Meta:
        """Catalog metadata."""

        name = "levenshtein_distance"
        description = "Levenshtein edit distance (same as DuckDB levenshtein())"
        examples = [
            FunctionExample(sql="SELECT tester.main.levenshtein_distance('duck', 'luck')", description="Returns 1")
        ]

    @classmethod
    def compute(
        cls,
        a: Annotated[pa.StringArray, Param(doc="First string")],
        b: Annotated[pa.StringArray, Param(doc="Second string")],
    ) -> Annotated[pa.Int64Array, Returns()]:
        """Pure Python on purpose: shows the cost of CPU-heavy work in a Python worker."""
        out = [
            None if x is None or y is None else _levenshtein(x, y)
            for x, y in zip(a.to_pylist(), b.to_pylist(), strict=True)
        ]
        return pa.array(out, type=pa.int64())


ALL_SCALARS = [
    PassthroughFunction,
    UpperTextFunction,
    AddNumbersFunction,
    Sha256HexFunction,
    LevenshteinFunction,
]
