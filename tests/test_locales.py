"""locales.json: every language has every key the code uses (runs tools/check_locales.py on the test copy)."""
import os
import subprocess
import sys

import common


def test_locales_complete():
    tool = os.path.join(common.BASE_DIR, "tools", "check_locales.py")
    res = subprocess.run([sys.executable, tool], cwd=common.BASE_DIR, capture_output=True, text=True,
                         encoding="utf-8")
    assert res.returncode == 0, res.stdout + res.stderr
