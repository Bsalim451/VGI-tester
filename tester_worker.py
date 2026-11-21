# Copyright 2026 Salim - https://github.com/Bsalim451/VGI-tester

# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python>=0.51",
#     "pyarrow>=15.0.0",
#     "numpy>=1.26",
# ]
# ///
"""Stdio entry point. This is what goes in the ATTACH LOCATION.

    ATTACH 'tester' (TYPE vgi, LOCATION 'uv run /path/to/tester_worker.py');
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vgi_tester.worker import main  # noqa: E402

if __name__ == "__main__":
    main()
