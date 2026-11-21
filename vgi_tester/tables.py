# Copyright 2026 Salim - https://github.com/Bsalim451/VGI-tester

"""Table function with a native DuckDB twin.

``number_series(count)`` returns the integers 0 .. count-1 in one column ``n``.
Native twin: ``SELECT range AS n FROM range(count)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

import numpy as np
import pyarrow as pa
from vgi_rpc import ArrowSerializableDataclass
from vgi_rpc.rpc import OutputCollector

from vgi.arguments import Arg
from vgi.metadata import FunctionExample
from vgi.table_function import (
    ProcessParams,
    TableFunctionGenerator,
    bind_fixed_schema,
    init_single_worker,
)


@dataclass(frozen=True)
class SeriesArgs:
    """``number_series(count, batch_size => 2048)``."""

    count: Annotated[int, Arg(0, doc="How many rows to generate", ge=0)]
    batch_size: Annotated[int, Arg("batch_size", default=2048, doc="Rows per Arrow batch", ge=1)] = 2048


@dataclass(kw_only=True)
class SeriesState(ArrowSerializableDataclass):
    """Where we are in the series between process() calls."""

    next_value: int = 0


@init_single_worker
@bind_fixed_schema
class NumberSeriesFunction(TableFunctionGenerator[SeriesArgs, SeriesState]):
    """Generate 0 .. count-1, one batch per tick."""

    FunctionArguments = SeriesArgs
    FIXED_SCHEMA: ClassVar[pa.Schema] = pa.schema([pa.field("n", pa.int64())])

    class Meta:
        """Catalog metadata."""

        name = "number_series"
        description = "Integers 0 .. count-1 (same as DuckDB range(count))"
        examples = [FunctionExample(sql="SELECT * FROM tester.main.number_series(5)", description="0 to 4")]

    @classmethod
    def initial_state(cls, params: ProcessParams[SeriesArgs]) -> SeriesState:
        """Start at zero."""
        return SeriesState()

    @classmethod
    def process(cls, params: ProcessParams[SeriesArgs], state: SeriesState, out: OutputCollector) -> None:
        """Emit the next chunk, or finish when we reached count."""
        remaining = params.args.count - state.next_value
        if remaining <= 0:
            out.finish()
            return
        size = min(remaining, params.args.batch_size)
        values = np.arange(state.next_value, state.next_value + size, dtype=np.int64)
        out.emit(pa.RecordBatch.from_arrays([pa.array(values)], schema=cls.FIXED_SCHEMA))
        state.next_value += size


ALL_TABLES = [NumberSeriesFunction]
