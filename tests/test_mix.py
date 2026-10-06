"""The browser-side math (mix gains, player scheduling) runs in node."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JS_TESTS = sorted((Path(__file__).parent / "js").glob("*.test.mjs"))


@pytest.mark.parametrize("js_test", JS_TESTS, ids=lambda p: p.name)
def test_browser_logic(js_test):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    proc = subprocess.run(
        [node, str(js_test)], capture_output=True, text=True, cwd=str(ROOT)
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip(), "the JS suite prints its assertions"
