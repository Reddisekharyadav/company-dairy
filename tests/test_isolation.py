"""Guards that make accidental production-database test runs fail loudly."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from config.settings import CONFIG_FILE
from database.session import DB_PATH


def test_tests_use_a_temporary_database_and_config():
    assert os.environ["WS_TESTING"] == "1"
    db_path = Path(DB_PATH).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    assert db_path.is_relative_to(temp_root), f"not a temp DB: {db_path}"
    assert "worksense-tests-" in str(db_path)

    config_path = Path(CONFIG_FILE).resolve()
    assert config_path.is_relative_to(temp_root), f"not a temp config: {config_path}"
