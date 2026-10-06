"""The mix-gain math runs in the browser, so its test runs it in node."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JS_TEST = Path(__file__).parent / "js" / "mix.test.mjs"


def test_effective_gain_math():
    if shutil.which("node") is None:
        pytest.skip("node not available")
    proc = subprocess.run(
        [shutil.which("node"), str(JS_TEST)],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
