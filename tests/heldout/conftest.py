# tests/heldout/conftest.py
"""Fixtures for the held-out pass. Spec 7.4, 10.1 mitigation 1.

Everything in this directory was authored AFTER the agent was frozen at commit
5598656 and is run once against that commit. Nothing here may modify the agent,
the contract or the world: a held-out pass that edits the system under test is
worthless.

Module level is kept free of servers and world access on purpose. Pytest imports
every test module at collection even when a marker deselects it, so anything that
binds a port or spawns a process at import time would break the default keyless
suite for a reader who never asked for this tier.
"""
import contextlib
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

BASE = "http://127.0.0.1:8799"
REPO_ROOT = Path(__file__).resolve().parents[2]
WORLD_SCRIPT = REPO_ROOT / "world" / "run_world.py"
CONTRACT = REPO_ROOT / "contracts" / "payer_enrollment.yaml"

# The simulation's own staging fixtures, the same three tools/run_demo.py sets.
# The CLI exits before it opens a browser if they are missing.
STAGING_CREDENTIALS = {
    "PORTAL_EMAIL": "ops@cascade-credentialing.example",
    "PORTAL_PASSWORD": "Staging2026!",
    "PORTAL_OTP": "246810",
}

USERNAME = STAGING_CREDENTIALS["PORTAL_EMAIL"]
PASSWORD = STAGING_CREDENTIALS["PORTAL_PASSWORD"]
OTP_CODE = STAGING_CREDENTIALS["PORTAL_OTP"]


def _world_is_up() -> bool:
    try:
        return httpx.get(BASE + "/healthz", timeout=0.5).status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="session")
def world():
    """The frozen simulation, reused if one is already listening.

    The live cases need a world that outlives a single pytest session, so this
    fixture attaches to a running one rather than fighting it for the port.
    """
    if _world_is_up():
        yield BASE
        return
    proc = subprocess.Popen([sys.executable, str(WORLD_SCRIPT)])
    try:
        for _ in range(50):
            if _world_is_up():
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("the target world did not come up on " + BASE)
        yield BASE
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


@pytest.fixture
def reset_world(world):
    """Reset reverts the layout as a side effect, so a case that needs a layout
    re-pins it after resetting (spec 9)."""
    def _reset(layout: str = "A"):
        httpx.post(world + "/admin/reset", timeout=5)
        httpx.post(world + "/admin/layout/" + layout, timeout=5)
    _reset()
    return _reset


@contextlib.contextmanager
def portal_session(base: str):
    """A logged-in HTTP client. The record page is behind the auth wall, and the
    fixture pages case 3 mutates are the world's own bytes, not hand-written HTML."""
    client = httpx.Client(base_url=base, follow_redirects=True, timeout=10)
    try:
        client.post("/login", data={"username": USERNAME, "password": PASSWORD})
        client.post("/verify", data={"code": OTP_CODE, "captcha": "1"})
        yield client
    finally:
        client.close()


PROXY_PORT = int(os.environ.get("VBA_HELDOUT_PROXY_PORT", "8801"))
PROXY_BASE = "http://127.0.0.1:" + str(PROXY_PORT)


@pytest.fixture
def record_store_proxy(world):
    """The harness-side record-store proxy the frozen world cannot substitute for.

    Started and stopped exactly the way tools/run_demo.py handles the blackhole
    proxy, because a proxy left running with a suppression armed would silently
    corrupt every case after it.
    """
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "heldout_proxy:app", "--host", "127.0.0.1",
         "--port", str(PROXY_PORT), "--log-level", "warning"],
        cwd=str(Path(__file__).resolve().parent))
    try:
        for _ in range(60):
            try:
                if httpx.get(PROXY_BASE + "/control/state", timeout=0.5).status_code == 200:
                    break
            except Exception:
                time.sleep(0.25)
        else:
            raise RuntimeError("the held-out record-store proxy did not come up on "
                               + PROXY_BASE)
        yield PROXY_BASE
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


async def observe_provider_page(world: str, npi: str, contract, step):
    """Sign in with a real browser, open a provider record, and return what the
    frozen perception layer saw plus the status the frozen response listener
    recorded for the main document.

    The listener is the field page_verify reads to tell a server error apart from a
    missing control, so both halves of that decision come from the agent's own code
    rather than from the test's idea of what the page returned.
    """
    from playwright.async_api import async_playwright

    from vba.perceive.snapshot import snapshot
    from vba.run.deps import Deps

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        try:
            deps = Deps(page=page, audit=None, vault=None, scrubber=None,
                        store=None, oracle=None, ctx_holder=None, grant=None)
            deps.attach_response_listener()
            await page.goto(world + "/")
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click("#sign-in")
            await page.wait_for_selector("#otp")
            await page.fill("#otp", OTP_CODE)
            await page.check("#not-a-robot")
            await page.click("#verify")
            await page.wait_for_url("**/dashboard")
            await page.goto(world + "/provider/" + npi)
            observation = await snapshot(page, epoch=1, contract=contract.name,
                                         step_key=step.step_key)
            return observation, deps.last_http_status
        finally:
            await browser.close()


class _Pages:
    """A one-page static server whose body can be swapped in place.

    Case 3 needs two versions of one page at the SAME url: the fingerprint hashes
    the normalized url, so serving the variants at different paths would make them
    differ for a reason that has nothing to do with the drift being probed.
    """

    def __init__(self, server, port: int, state: dict):
        self._server = server
        self._state = state
        self.url = "http://127.0.0.1:" + str(port) + "/record"

    def serve(self, html: str) -> None:
        self._state["html"] = html


@pytest.fixture
def static_pages():
    state = {"html": "<html><body>empty</body></html>"}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):                                   # noqa: N802
            body = state["html"].encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):                                  # noqa: N802
            # The fixture page keeps the world's form action, which resolves
            # against THIS server, so a replayed submit posts here and nothing
            # reaches the real record store.
            body = b"<html><body>method not allowed</body></html>"
            self.send_response(405)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):                       # keep pytest output clean
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield _Pages(server, server.server_address[1], state)
    finally:
        server.shutdown()
        server.server_close()


def run_cli(providers, payer="Aetna", runs_dir=None, chaos=None, oracle_base=None,
            timeout=1800):
    """One live run of the frozen agent, invoked exactly as the demo driver does.

    Returns (returncode, run_dir, records, stdout, stderr). run_dir is None when
    the process died before it wrote anything, which is itself a result.
    """
    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in runs_dir.iterdir() if p.is_dir()}

    env = dict(os.environ)
    env.update(STAGING_CREDENTIALS)
    env["PORTAL_BASE"] = BASE
    env["ORACLE_BASE"] = oracle_base or BASE
    if chaos:
        env["VBA_CHAOS"] = chaos
    else:
        env.pop("VBA_CHAOS", None)

    cmd = [sys.executable, "-m", "vba.cli", "--contract", str(CONTRACT),
           "--runs-dir", str(runs_dir), "--payer", payer,
           "--providers", *providers]
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, timeout=timeout,
                          capture_output=True, text=True)

    fresh = [p for p in runs_dir.iterdir()
             if p.is_dir() and p.name not in before]
    run_dir = fresh[0] if fresh else None
    records = []
    if run_dir is not None and (run_dir / "audit.jsonl").exists():
        records = [json.loads(line) for line
                   in (run_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
                   if line.strip()]
    return proc.returncode, run_dir, records, proc.stdout, proc.stderr


def events(records, event: str) -> list:
    return [r for r in records if r.get("event") == event]
