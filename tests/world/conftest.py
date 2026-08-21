# tests/world/conftest.py
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

BASE = "http://127.0.0.1:8799"

# Anchored on this file rather than the pytest invocation cwd, so the world spawns
# correctly regardless of where `pytest` is run from.
WORLD_SCRIPT = Path(__file__).resolve().parents[2] / "world" / "run_world.py"


@pytest.fixture(scope="session")
def world():
    """Spawns the target world as a subprocess and tears it down after the session.

    Controller ruling R10: must use sys.executable, not the bare string "python".
    The world's fastapi/uvicorn dependencies live only in the venv; pytest itself
    runs under the venv interpreter (sys.executable), so that is the interpreter
    that can import them. The bare "python" on PATH may resolve to a different,
    dependency-less interpreter (or nothing at all) depending on the machine.
    """
    proc = subprocess.Popen([sys.executable, str(WORLD_SCRIPT)])
    try:
        for _ in range(50):
            try:
                if httpx.get(BASE + "/healthz", timeout=0.5).status_code == 200:
                    break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("the target world did not come up on " + BASE)
        yield BASE
    finally:
        # Reliable teardown even on test failure or a raised RuntimeError above.
        # Windows has no SIGTERM; proc.terminate() maps to TerminateProcess there,
        # which is fine for a plain uvicorn child with no children of its own.
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


@pytest.fixture
def reset_world(world):
    """Spec 9: reset reverts the layout as a side effect, so every test that needs a
    specific layout must re-pin it AFTER resetting."""
    def _reset(layout: str = "A"):
        httpx.post(world + "/admin/reset", timeout=5)
        httpx.post(world + "/admin/layout/" + layout, timeout=5)
    _reset()
    return _reset
