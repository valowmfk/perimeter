"""Shared pytest fixtures for Q Branch Automation tests."""

import json
import sys
from pathlib import Path

import pytest

# Ensure python/ directory is importable (mirrors what workflow scripts do)
REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON_DIR = REPO_ROOT / "python"

if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def tmp_json(tmp_path):
    """Return a helper that writes a dict to a temp JSON file and returns the path."""
    def _write(data, name="data.json"):
        p = tmp_path / name
        p.write_text(json.dumps(data, indent=2))
        return p
    return _write
