"""Drive the demonstrations in the exact order they must run.

Spec 9. Prose steps cannot carry this: /admin/reset reverts the layout AND clears
sessions AND clears the record store, but does NOT touch agent memory or the audit
file. A third party running the demo twice would get warm memory on the second run,
and the cold-heal demonstration would silently become the memory-reuse one.

Two of the seven cases also need the world to change UNDER a run rather than
before it, at a point no operator could hit by hand. Those are composed here as
VBA_CHAOS directives (ruling R24) and the world is restored afterwards even when
the run fails, because a portal left down or a proxy left blackholed silently
corrupts every later case.
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

BASE = os.environ.get("PORTAL_BASE", "http://127.0.0.1:8799")
REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = REPO_ROOT / "runs"
CONTRACT = REPO_ROOT / "contracts" / "payer_enrollment.yaml"

PROXY_PORT = int(os.environ.get("VBA_PROXY_PORT", "8800"))
PROXY_BASE = "http://127.0.0.1:" + str(PROXY_PORT)

# The world's own staging fixtures, so a third party can run the demo with zero
# setup. They are the vendored simulation's seed values, not anyone's secrets; a
# real deployment overrides all three from its own environment.
STAGING_CREDENTIALS = {
    "PORTAL_EMAIL": "ops@cascade-credentialing.example",
    "PORTAL_PASSWORD": "Staging2026!",
    "PORTAL_OTP": "246810",
}


@dataclass
class Case:
    layout: str
    providers: list
    memory: bool = True
    chaos: str = ""
    proxy: bool = False
    note: str = ""


CASES = {
    "verification": Case("A", ["1700000001", "1700000005"],
                         note="one provider posts, one silently does not"),
    "heal": Case("B", ["1700000001"],
                 note="a vendor layout change, resolved without a code edit"),
    # A DIFFERENT entity on the same layout, so the fix learned by the heal is
    # replayed rather than re-resolved.
    "reuse": Case("B", ["1700000002"],
                  note="the learned fix, replayed on another provider"),
    "supersede": Case("C", ["1700000003"],
                      note="a third layout, so the learned fix is stale"),
    "memory-off": Case("A", ["1700000001", "1700000005"], memory=False,
                       note="the control for the memory claim"),
    "outage": Case("A", ["1700000001"],
                   chaos="portal_down_before:enrollment.submit",
                   note="the portal fails at the moment of filing"),
    "blackhole": Case("A", ["1700000001"],
                      chaos="blackhole_after_baseline:enrollment.submit",
                      proxy=True,
                      note="the record store stops answering after the act"),
}


def preflight() -> None:
    try:
        httpx.get(BASE + "/healthz", timeout=2).raise_for_status()
    except Exception:
        sys.exit("the target world is not running. Start it with: "
                 "python world/run_world.py")


def reset_agent_state() -> None:
    """Step 0, and the one a third party would otherwise miss."""
    if STATE_DIR.exists():
        shutil.rmtree(STATE_DIR)
    STATE_DIR.mkdir(parents=True)


def reset_world(layout: str) -> None:
    httpx.post(BASE + "/admin/reset", timeout=5)
    httpx.post(BASE + "/admin/layout/" + layout, timeout=5)


def restore_world() -> None:
    """Always, including after a failure. A portal left down or a layout left
    pinned turns every later case into a different case without saying so."""
    try:
        httpx.post(BASE + "/admin/portal/up", timeout=5)
    except Exception:
        pass


def start_proxy() -> subprocess.Popen:
    """Spec 7.3: the world exposes no control that makes the record store
    unreachable, so the oracle is routed through a proxy that can stop answering."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "blackhole_proxy:app",
         "--host", "127.0.0.1", "--port", str(PROXY_PORT), "--log-level", "warning"],
        cwd=str(REPO_ROOT / "tools"),
    )
    for _ in range(60):
        try:
            # Posting "off" is both the readiness probe and the known-good starting
            # state: a proxy left blackholed by an earlier run answers this.
            if httpx.post(PROXY_BASE + "/control/blackhole/off",
                          timeout=0.5).status_code == 200:
                return proc
        except Exception:
            time.sleep(0.25)
    stop_proxy(proc)
    sys.exit("the blackhole proxy did not come up on " + PROXY_BASE)


def stop_proxy(proc: subprocess.Popen) -> None:
    try:
        httpx.post(PROXY_BASE + "/control/blackhole/off", timeout=2)
    except Exception:
        pass
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def run(case: Case) -> int:
    cmd = [sys.executable, "-m", "vba.cli", "--contract", str(CONTRACT),
           "--runs-dir", str(STATE_DIR), "--providers", *case.providers]
    if not case.memory:
        cmd.append("--no-memory")

    env = dict(os.environ)
    for name, value in STAGING_CREDENTIALS.items():
        env.setdefault(name, value)
    env["PORTAL_BASE"] = BASE
    env["ORACLE_BASE"] = PROXY_BASE if case.proxy else BASE
    if case.chaos:
        env["VBA_CHAOS"] = case.chaos
    else:
        env.pop("VBA_CHAOS", None)

    return subprocess.run(cmd, check=False, cwd=str(REPO_ROOT), env=env).returncode


def show_records() -> None:
    rows = httpx.get(BASE + "/api/sor/enrollments", timeout=5).json()
    print("\nIndependent verification, read outside the agent:")
    if not rows["enrollments"]:
        print("  (the payer's records hold nothing for this run)")
    for r in rows["enrollments"]:
        print("  " + r["npi"] + "  " + r["payer"] + "  " + r["confirmation_id"])


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run one demonstration against the target world.")
    ap.add_argument("case", choices=sorted(CASES))
    ap.add_argument("--keep-memory", action="store_true",
                    help="do not reset agent state; required for the reuse beat")
    args = ap.parse_args()

    preflight()
    case = CASES[args.case]
    if not args.keep_memory:
        reset_agent_state()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    reset_world(case.layout)

    proxy = start_proxy() if case.proxy else None
    try:
        run(case)
    finally:
        restore_world()
        if proxy is not None:
            stop_proxy(proxy)
    show_records()


if __name__ == "__main__":
    main()
