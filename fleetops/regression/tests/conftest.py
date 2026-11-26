"""Make the fleetops package importable wherever the suite runs from.

The package lands at <repo>/fleetops/regression; this file lives at
fleetops/regression/tests/conftest.py, so the repo root is three parents
up. Inserting it lets tests import fleetops.regression the same way the
installed package is imported.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
