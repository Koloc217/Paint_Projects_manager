"""locales.json: every language has every key the code uses (runs tools/check_locales.py)."""
import os
import subprocess
import sys

from hub.paths import BASE_DIR


def test_locales_complete():
    tool = os.path.join(BASE_DIR, "tools", "check_locales.py")
    res = subprocess.run([sys.executable, tool], cwd=BASE_DIR, capture_output=True, text=True,
                         encoding="utf-8")
    assert res.returncode == 0, res.stdout + res.stderr
