# tests/heldout/test_case_6_record_page_unavailable.py
"""Held-out case 6: the record page is unavailable when the run opens it.

The plan's wording: "a record page unavailable at load, which should retry later
without escalating". Spec 5.2 is more precise about the mechanism and states the
danger by name:

    Infrastructural | 5xx, navigation timeout | never resolve; classify via the
    oracle ... Without this, a portal outage sends the agent into a resolution
    spiral against a 503 page.

Spec 5.3 then routes the classified outcome: verified-not-done, retry permitted,
still escalate. The two documents differ on the escalation and agree on everything
that matters here, which is that an outage must never be mistaken for a control
that failed to respond.

The rubric's existing outage case fails the portal at the SUBMIT step, where the
POST returns a real 503 and the oracle decides the outcome. Failing it at the load
of the record page is a different branch: the step has no cross-system predicate,
so nothing but the page verdict decides what happens next.

The deterministic half needs the world and no model. The live half is one run.
"""
import httpx
import pytest

from vba.contract.loader import load_contract
from vba.oracle.delta import Outcome, PageVerdict
from vba.run.escalate import reason_for
from vba.run.machine import RESOLVE_BUDGET, next_transition
from vba.verify.page import page_verify

from .conftest import CONTRACT, events, observe_provider_page, portal_session, run_cli

pytestmark = [pytest.mark.heldout, pytest.mark.world]

NPI = "1700000001"


def _open_step(contract):
    return next(s for s in contract.steps if s.step_key == "provider.open")


@pytest.fixture
def portal_down(world, reset_world):
    """The world's own outage control, and the run_demo discipline of restoring it
    whatever happens: a portal left down turns every later case into a different
    case without saying so."""
    reset_world("A")
    httpx.post(world + "/admin/portal/down", timeout=5)
    try:
        yield world
    finally:
        httpx.post(world + "/admin/portal/up", timeout=5)
        assert httpx.get(world + "/healthz", timeout=5).json()["state"]["portal"] == "up"


def test_the_unavailable_record_page_is_served_with_a_server_error_status(portal_down):
    """The signal the agent's classifier depends on.

    The frozen response listener records the main document's HTTP status and
    page_verify reads it. A page whose body announces an outage while its status
    line says the request succeeded gives that classifier nothing to work with.
    """
    with portal_session(portal_down) as client:
        response = client.get("/provider/" + NPI)
    assert "temporarily unavailable" in response.text, (
        "the outage page did not render; this case is not set up"
    )
    assert response.status_code >= 500, (
        "the record page announced a 503 in its body and returned HTTP "
        + str(response.status_code) + " on the wire"
    )


async def test_an_outage_at_load_is_classified_infrastructural(portal_down):
    """Spec 5.2. Infrastructural never routes to resolution."""
    contract = load_contract(CONTRACT)
    step = _open_step(contract)
    observation, status = await observe_provider_page(portal_down, NPI, contract, step)
    verdict = page_verify(step, observation, status)
    assert verdict is PageVerdict.INFRASTRUCTURAL, (
        "the outage page was classified " + str(verdict) + " with http status "
        + str(status) + ", so the run resolves against a page that is down"
    )


def test_what_the_transition_table_does_with_the_verdict_it_actually_produces():
    """Characterization. The routing is correct for the verdict it is given; the
    cost comes from the verdict, which the test above is about."""
    mechanical = [next_transition(Outcome.NOT_ACTED, attempt, RESOLVE_BUDGET)
                  for attempt in range(RESOLVE_BUDGET + 1)]
    assert mechanical == ["resolve", "resolve", "resolve", "escalate"]
    assert reason_for(Outcome.NOT_ACTED, RESOLVE_BUDGET) == (
        "Resolution did not converge after 3 attempts.")
    # What the same table does with the verdict spec 5.2 asks for.
    assert next_transition(Outcome.VERIFIED_NOT_DONE, 0, RESOLVE_BUDGET) == "escalate"
    assert "Safe to retry" in reason_for(Outcome.VERIFIED_NOT_DONE)


@pytest.mark.evals
def test_live_an_outage_at_load_is_not_reported_as_a_failed_resolution(
        world, reset_world, tmp_path):
    """One live run, with the world taken down under the run by the frozen chaos
    hook at the start of the step that opens the record.

    The hook is the only way to place the outage at that instant, and it is the
    agent's own evaluation tooling rather than anything authored for this pass.
    """
    reset_world("A")
    try:
        code, run_dir, records, stdout, stderr = run_cli(
            [NPI], runs_dir=tmp_path / "runs",
            chaos="portal_down_before:provider.open")
    finally:
        httpx.post(world + "/admin/portal/up", timeout=5)

    assert run_dir is not None, ("the run wrote nothing. rc=" + str(code)
                                 + " stderr=" + stderr[-600:])
    escalations = events(records, "escalation")
    sessions = len(events(records, "session_message"))
    verifications = events(records, "verification")
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    context = (" escalations=" + str([(e["outcome"], e["reason"]) for e in escalations])
               + " session_messages=" + str(sessions)
               + " verification_records=" + str(len(verifications))
               + " report=" + repr(report))

    assert escalations, "the run did not escalate at all." + context
    assert escalations[-1]["outcome"] == "verified_not_done", (
        "an outage at the record page escalated as " + escalations[-1]["outcome"]
        + " with the reason " + repr(escalations[-1]["reason"]) + "." + context
    )
