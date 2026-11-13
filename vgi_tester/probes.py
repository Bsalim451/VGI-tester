# Copyright 2026 Salim - https://github.com/Bsalim451/VGI-tester

"""Failure probes: functions that break on purpose.

A native DuckDB extension runs inside the DuckDB process. If it crashes, DuckDB
crashes with it. A VGI worker is a separate process. These probes check what
DuckDB does when a worker misbehaves:

- ``fail_with(msg)``     raises a normal Python error -> should become a SQL error.
- ``kill_worker(x)``     hard-exits the worker process -> DuckDB should survive.
- ``slow_echo(s, ms)``   sleeps per batch -> used to test timeouts / cancel.
"""

from __future__ import annotations

import os
import time
from typing import Annotated

import pyarrow as pa
from vgi.arguments import ConstParam, Param, Returns
from vgi.scalar_function import ScalarFunction


class FailWithFunction(ScalarFunction):
    """Always raise ``ValueError(message)``."""

    class Meta:
        """Catalog metadata."""

        name = "fail_with"
        description = "Raises an error with the given message (error-path probe)"

    @classmethod
    def compute(
        cls,
        value: Annotated[pa.StringArray, Param(doc="Any value, ignored")],
        message: Annotated[str, ConstParam(doc="Error text to raise")],
    ) -> Annotated[pa.StringArray, Returns()]:
        """Raise straight away."""
        raise ValueError(message)


class KillWorkerFunction(ScalarFunction):
    """Kill the worker process with ``os._exit(3)``. Simulates a segfault-style crash."""

    class Meta:
        """Catalog metadata."""

        name = "kill_worker"
        description = "Hard-exits the worker process (crash-isolation probe)"

    @classmethod
    def compute(
        cls,
        value: Annotated[pa.Int64Array, Param(doc="Any value, ignored")],
    ) -> Annotated[pa.Int64Array, Returns()]:
        """Exit without cleanup, like a crash."""
        os._exit(3)


class SlowEchoFunction(ScalarFunction):
    """Sleep ``ms`` milliseconds per batch, then echo."""

    class Meta:
        """Catalog metadata."""

        name = "slow_echo"
        description = "Echo after sleeping ms per batch (latency probe)"

    @classmethod
    def compute(
        cls,
        value: Annotated[pa.StringArray, Param(doc="Value to echo")],
        ms: Annotated[int, ConstParam(doc="Milliseconds to sleep per batch", ge=0)],
    ) -> Annotated[pa.StringArray, Returns()]:
        """Sleep, then return the input."""
        time.sleep(ms / 1000)
        return value


ALL_PROBES = [FailWithFunction, KillWorkerFunction, SlowEchoFunction]
