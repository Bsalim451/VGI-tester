# Copyright 2026 Salim - https://github.com/Bsalim451/VGI-tester

"""VGI-tester worker: functions with native DuckDB twins, plus failure probes.

    INSTALL vgi FROM community;
    LOAD vgi;
    ATTACH 'tester' (TYPE vgi, LOCATION 'uv run tester_worker.py');

    SELECT tester.main.upper_text('duckdb');          -- 'DUCKDB'
    SELECT * FROM tester.main.number_series(3);       -- 0, 1, 2
"""

from __future__ import annotations

import sys

from vgi import Worker
from vgi.catalog import Catalog, ReadOnlyCatalogInterface, Schema
from vgi.catalog.catalog_interface import CatalogInfo

from vgi_tester import __version__
from vgi_tester.probes import ALL_PROBES
from vgi_tester.scalars import ALL_SCALARS
from vgi_tester.tables import ALL_TABLES

SOURCE_URL = "https://github.com/Bsalim451/VGI-tester"

_CATALOG = Catalog(
    name="tester",
    default_schema="main",
    comment="VGI-tester: functions with native DuckDB twins, for comparing VGI against native",
    source_url=SOURCE_URL,
    tags={
        "vgi.title": "VGI tester",
        "vgi.source_url": SOURCE_URL,
        "vgi.license": "Apache-2.0",
        "vgi.author": "Salim (HvA HBO-ICT, internship at QXIP)",
    },
    schemas=[
        Schema(
            path=["main"],
            comment="Scalar + table functions that mirror DuckDB built-ins, and failure probes",
            functions=[*ALL_SCALARS, *ALL_TABLES, *ALL_PROBES],
        ),
    ],
)


class TesterCatalog(ReadOnlyCatalogInterface):
    """Read-only catalog, nothing to store."""

    catalog = _CATALOG
    catalog_name = _CATALOG.name
    secret_types = []

    def catalogs(self) -> list[CatalogInfo]:
        """Advertise the catalog and its version."""
        return [
            CatalogInfo(
                name=self._effective_catalog_name,
                implementation_version=__version__,
                data_version_spec=f"=={__version__}",
                source_url=SOURCE_URL,
            )
        ]


class TesterWorker(Worker):
    """The worker process DuckDB starts."""

    catalog = _CATALOG
    catalog_interface = TesterCatalog


def main() -> None:
    """Serve over stdio (default). Pass ``--http`` or ``--unix <path>`` for the other transports."""
    TesterWorker.main()


def main_http() -> None:
    """Serve over HTTP."""
    argv = sys.argv[1:]
    if "--http" not in argv:
        argv = ["--http", *argv]
    sys.argv = [sys.argv[0], *argv]
    TesterWorker.main()


if __name__ == "__main__":
    main()
