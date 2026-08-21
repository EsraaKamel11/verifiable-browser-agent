# tests/heldout/test_case_1_malformed_oracle.py
"""Held-out case 1: a malformed oracle response.

Ranked first in the plan because its worst failure mode is the chain the whole
project exists to prevent: a record store that did not really answer, read as
"nothing posted", leading to a retry, leading to a duplicate.

The plan names two shapes, a 5xx and truncated JSON. Both are exercised here. Four
more are exercised alongside them, and they are extensions authored for this pass
rather than shapes the plan named: a 502 carrying an HTML error page, a 200 whose
JSON body omits the count, a 200 whose count is null, and a whole-table read that
cannot be served. They are included because "malformed" in the wild is mostly not
a clean 5xx, and because the client's own docstring makes a promise about all of
them: "Unreachable, refused, malformed: all are unknown, never absent."

No model and no world. The oracle is a stub that misbehaves on demand, and the
frozen OracleClient and adjudicate() are the code under test.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from vba.oracle.client import OracleClient
from vba.oracle.delta import Baseline, Outcome, OracleReading, PageVerdict, adjudicate

from .conftest import REPO_ROOT, STAGING_CREDENTIALS

pytestmark = pytest.mark.heldout

# One identifier per response shape, so a single stub serves the whole matrix.
HEALTHY = "1700000001"
FIVE_HUNDRED = "1700000500"
BAD_GATEWAY_HTML = "1700000502"
TRUNCATED = "1700000900"
NO_COUNT = "1700000901"
NULL_COUNT = "1700000902"

_HEALTHY_BODY = json.dumps({"npi": HEALTHY, "enrolled": False, "count": 0,
                            "latest": None})


class _Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: str, ctype: str = "application/json"):
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):                                       # noqa: N802
        path = self.path
        if path.endswith("/enrollments"):
            # The whole-table read used to reconcile a discrepancy. This stub
            # cannot serve it, which is the point of the last case below.
            return self._send(503, "<html>upstream unavailable</html>", "text/html")
        npi = path.rstrip("/").rsplit("/", 1)[-1]
        if npi == FIVE_HUNDRED:
            return self._send(500, '{"error": "internal"}')
        if npi == BAD_GATEWAY_HTML:
            return self._send(502, "<html><body>502 Bad Gateway</body></html>",
                              "text/html")
        if npi == TRUNCATED:
            return self._send(200, '{"npi": "' + TRUNCATED + '", "enrolled": fa')
        if npi == NO_COUNT:
            return self._send(200, json.dumps({"npi": NO_COUNT, "enrolled": False,
                                               "latest": None}))
        if npi == NULL_COUNT:
            return self._send(200, json.dumps({"npi": NULL_COUNT, "enrolled": True,
                                               "count": None, "latest": None}))
        return self._send(200, _HEALTHY_BODY)

    def log_message(self, *args):
        return


@pytest.fixture
def bad_oracle():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield "http://127.0.0.1:" + str(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()


def _client(base: str) -> OracleClient:
    return OracleClient(base, "{base}/api/sor/enrollment/{npi}", timeout=2.0)


def _healthy_baseline() -> Baseline:
    """A real, reachable, count-zero baseline. The delta arithmetic is only
    meaningful against one, and every case below is about the read AFTER the act."""
    return Baseline(OracleReading(reachable=True, enrolled=False, count=0,
                                  latest=None, raw={"count": 0}), epoch=1)


async def test_the_stub_serves_a_healthy_read(bad_oracle):
    """Non-vacuity. If the stub answered nothing at all, every case below would
    pass for the wrong reason."""
    reading = await _client(bad_oracle).read(HEALTHY)
    assert reading.reachable is True
    assert reading.count == 0


async def test_a_500_is_unknown_and_never_absent(bad_oracle):
    """Plan shape one. Spec 5.4: the oracle answering nothing is UNVERIFIABLE."""
    reading = await _client(bad_oracle).read(FIVE_HUNDRED)
    assert reading.reachable is False
    outcome = adjudicate(_healthy_baseline(), reading, PageVerdict.PASSED,
                         {"npi": FIVE_HUNDRED}, "PC-000001", [])
    assert outcome is Outcome.UNVERIFIABLE


async def test_truncated_json_is_unknown_and_never_absent(bad_oracle):
    """Plan shape two: the body starts to arrive and stops."""
    reading = await _client(bad_oracle).read(TRUNCATED)
    assert reading.reachable is False
    outcome = adjudicate(_healthy_baseline(), reading, PageVerdict.PASSED,
                         {"npi": TRUNCATED}, "PC-000001", [])
    assert outcome is Outcome.UNVERIFIABLE


async def test_a_502_carrying_html_is_unknown_and_never_absent(bad_oracle):
    """An extension: the shape a proxy or a load balancer actually returns."""
    reading = await _client(bad_oracle).read(BAD_GATEWAY_HTML)
    assert reading.reachable is False
    outcome = adjudicate(_healthy_baseline(), reading, PageVerdict.PASSED,
                         {"npi": BAD_GATEWAY_HTML}, "PC-000001", [])
    assert outcome is Outcome.UNVERIFIABLE


async def test_a_body_with_no_count_is_unknown_and_never_absent(bad_oracle):
    """An extension, and the dangerous one.

    A 200 whose JSON parses but carries no count is malformed. The client's own
    docstring says malformed is unknown, never absent. If it is instead read as a
    confirmed zero, a page that claimed success becomes DISCREPANCY on evidence
    that was never actually served: the run reports "the payer's records show no
    enrollment" when the payer's records said nothing of the kind.
    """
    reading = await _client(bad_oracle).read(NO_COUNT)
    outcome = adjudicate(_healthy_baseline(), reading, PageVerdict.PASSED,
                         {"npi": NO_COUNT}, "PC-000001", [])
    assert reading.reachable is False, (
        "a 200 with no count in the body was read as reachable with count "
        + str(reading.count) + "; malformed became absent"
    )
    assert outcome is Outcome.UNVERIFIABLE


async def test_a_null_count_is_unknown_rather_than_an_exception(bad_oracle):
    """An extension. int(None) sits outside the client's try block.

    Spec 5.1 and 5.5: the read after a tier-3 act is what decides whether an
    irreversible act landed. A read that raises instead of answering takes the
    whole entity loop with it, and the act it was supposed to adjudicate stays
    filed and unadjudicated.
    """
    try:
        reading = await _client(bad_oracle).read(NULL_COUNT)
    except Exception as exc:                                # noqa: BLE001
        pytest.fail("read() raised " + type(exc).__name__ + ": " + str(exc)
                    + "; a malformed body must be an unknown reading, not an "
                      "exception that escapes the run loop")
    assert reading.reachable is False


async def test_a_table_read_that_fails_is_not_an_empty_table(bad_oracle):
    """An extension. read_all() answers [] for both "no rows" and "no answer".

    Spec 5.3 reconciles a DISCREPANCY against the whole table before concluding,
    and that reconciliation is what turns a wrong-entity filing into MISFILED. A
    table read that could not be served returns the same value as a table with
    nothing in it, so the reconciliation silently does not happen.
    """
    unreadable = await _client(bad_oracle).read_all()
    healthy_but_empty = []          # what the real endpoint returns with no rows
    assert unreadable != healthy_but_empty, (
        "an unreadable table returned " + repr(unreadable) + ", the same value a "
        "healthy empty table returns, so the whole-table reconciliation spec 5.3 "
        "requires is skipped without a trace"
    )


def test_the_cli_refuses_at_intake_when_the_oracle_answers_malformed(bad_oracle,
                                                                     tmp_path):
    """Spec 4.2: "oracle declared but unreachable at start: refuse to start."

    The intake probe is the first read of every run and runs before a browser
    exists. This drives the frozen CLI as a subprocess with the record store
    pointed at the malformed stub, and asks for the refusal the contract gate
    promises. No model is reached: the probe happens first.
    """
    import os
    import subprocess
    import sys

    env = dict(os.environ)
    env.update(STAGING_CREDENTIALS)
    env["PORTAL_BASE"] = "http://127.0.0.1:8799"
    env["ORACLE_BASE"] = bad_oracle
    env.pop("VBA_CHAOS", None)
    proc = subprocess.run(
        [sys.executable, "-m", "vba.cli", "--contract",
         str(REPO_ROOT / "contracts" / "payer_enrollment.yaml"),
         "--runs-dir", str(tmp_path / "runs"), "--providers", NULL_COUNT],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=120)

    assert "REFUSED." in proc.stdout, (
        "the CLI did not refuse at intake. stdout=" + repr(proc.stdout[-400:])
        + " stderr=" + repr(proc.stderr[-400:])
    )
