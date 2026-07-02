"""
tests/test_main_dbstats.py

`main.py --dbstats` must behave like `--dashboard`: report a clear error and
exit non-zero when the DB file doesn't exist yet, instead of silently
creating an empty one and printing a misleadingly clean "0 rows" summary.
"""

import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_dbstats(db_path):
    env = dict(os.environ)
    env["DB_PATH"] = db_path
    env["RPC_URL"] = env.get("RPC_URL", "https://example.invalid")
    return subprocess.run(
        [sys.executable, "main.py", "--dbstats"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_dbstats_missing_db_errors_without_creating_file(tmp_path):
    db_path = str(tmp_path / "nested" / "watcher.db")

    result = _run_dbstats(db_path)

    assert result.returncode != 0
    assert "not found" in result.stdout.lower()
    assert not os.path.exists(db_path)


def test_dbstats_existing_db_still_works(tmp_path):
    from src.storage import SQLiteStore

    db_path = str(tmp_path / "watcher.db")
    store = SQLiteStore(db_path)
    store.connect()
    store.close()

    result = _run_dbstats(db_path)

    assert result.returncode == 0
    assert os.path.exists(db_path)
