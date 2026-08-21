# tests/evals/conftest.py
"""Fixtures for tier 3. Every fixture here spends money when it is used."""
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS = REPO_ROOT / "runs"
DEMO = REPO_ROOT / "tools" / "run_demo.py"


def _run_dirs() -> set:
    if not RUNS.exists():
        return set()
    return {p for p in RUNS.iterdir() if p.is_dir()}


@pytest.fixture
def demo_run():
    """Run one demonstration and return the directory it wrote.

    The run directory is identified by diffing the set of directories before and
    after rather than by taking the last entry. Run ids are random hex, so sorting
    does not order them by time, and `runs/` also holds memory.db, which sorts
    after every hex name. A refused run writes no directory at all, and that has to
    fail loudly here rather than silently hand back an earlier run's audit file.
    """
    def _run(case: str, keep_memory: bool = False) -> pathlib.Path:
        before = _run_dirs()
        cmd = [sys.executable, str(DEMO), case]
        if keep_memory:
            cmd.append("--keep-memory")
        subprocess.run(cmd, check=True, cwd=str(REPO_ROOT))
        new = _run_dirs() - before
        if not new:
            pytest.fail("the " + case + " demo wrote no run directory; the CLI "
                        "refused to start or died before opening a browser")
        return max(new, key=lambda p: p.stat().st_mtime)

    return _run
