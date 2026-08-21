# tests/heldout/test_case_5_identifier_absent.py
"""Held-out case 5: an identifier that is not in the portal at all.

Spec 5.4 makes this a distinct verdict, and says who adjudicates it:

    | oracle answered, count unchanged | verified absent | NOT ENROLLED |
    | oracle unreachable               | unknown         | UNVERIFIABLE |
    | identifier absent from the portal| wrong question  | INVALID      |

    INVALID is adjudicated by the portal, not the oracle: the reconciliation
    endpoint answers "not enrolled" for any identifier, including ones that do
    not exist.

So the record store cannot help here by construction, and the portal's own
not-found page is the only evidence. No model and no live run: what the agent does
with that page is decided by the frozen page verifier, the frozen transition table
and the frozen outcome taxonomy, all of which are deterministic.
"""
import pytest

from vba.contract.loader import load_contract
from vba.oracle.client import OracleClient
from vba.oracle.delta import Outcome, PageVerdict
from vba.report.render import render_report
from vba.run.escalate import reason_for
from vba.run.machine import RESOLVE_BUDGET, next_transition
from vba.verify.page import page_verify

from .conftest import CONTRACT, observe_provider_page, portal_session

pytestmark = [pytest.mark.heldout, pytest.mark.world]

# Well formed, ten digits, and in no seed row: the shape of a typo in a work order.
ABSENT_NPI = "1700000099"


def _open_step(contract):
    return next(s for s in contract.steps if s.step_key == "provider.open")


async def test_the_record_store_answers_not_enrolled_for_an_identifier_that_does_not_exist(
        world, reset_world):
    """The premise spec 5.4 states. This one is expected to hold, and if it did not
    the rest of the case would be about something else."""
    reading = await OracleClient(world, "{base}/api/sor/enrollment/{npi}").read(ABSENT_NPI)
    assert reading.reachable is True
    assert reading.enrolled is False
    assert reading.count == 0


def test_the_portal_says_no_such_provider(world, reset_world):
    """The portal is the adjudicator here, so its answer is the input to everything
    below. Recorded as a fact of the frozen world rather than as a defect. The read
    is authenticated, because the record route redirects to the sign-in page for
    anyone who is not."""
    with portal_session(world) as client:
        response = client.get("/provider/" + ABSENT_NPI)
    assert "No such provider" in response.text
    assert response.status_code == 200          # the not-found page is served as 200


async def test_an_absent_identifier_is_not_read_as_a_mechanical_failure(
        world, reset_world):
    """The agent's own reading of that page.

    A not-found record is not a click that failed to land. Routing it as one sends
    the run into resolution against a page that will never contain the record,
    which is the same shape as the resolution spiral spec 5.2 forbids for outages.
    """
    contract = load_contract(CONTRACT)
    step = _open_step(contract)
    observation, status = await observe_provider_page(world, ABSENT_NPI, contract, step)
    assert "No such provider" in observation.text, (
        "the browser did not land on the not-found page, so this is measuring "
        "something else: " + observation.text[:200])
    verdict = page_verify(step, observation, status)
    assert verdict is not PageVerdict.MECHANICAL, (
        "the portal's not-found page was classified " + str(verdict)
        + " with http status " + str(status) + ", so an identifier that does not "
        "exist is routed to resolution as if the click had missed"
    )


def test_the_outcome_taxonomy_can_name_an_invalid_identifier():
    """Spec 5.4's third verdict. Without a name for it, the run has to report an
    absent identifier as something it is not."""
    names = {o.name for o in Outcome}
    assert "INVALID" in names, (
        "the outcome taxonomy is " + ", ".join(sorted(names))
        + "; spec 5.4's INVALID verdict has no representative, so a wrong question "
        "cannot be reported as one"
    )


def test_what_the_transition_table_does_with_the_verdict_it_actually_produces():
    """Characterization, not a spec claim.

    Routing a genuine mechanical failure to resolution is correct and spec 5.3 asks
    for it. The cost recorded here follows from the misclassification above rather
    than from this table: an identifier that does not exist is resolved against
    until the budget is gone, once per live model session, and is then reported as
    a resolution that did not converge.
    """
    routes = [next_transition(Outcome.NOT_ACTED, attempt, RESOLVE_BUDGET)
              for attempt in range(RESOLVE_BUDGET + 1)]
    assert routes == ["resolve", "resolve", "resolve", "escalate"]
    assert reason_for(Outcome.NOT_ACTED, RESOLVE_BUDGET) == (
        "Resolution did not converge after 3 attempts.")


def test_the_report_names_the_entity_that_was_escalated():
    """Spec 8.2: the report is the human-readable deliverable, one entry per
    enrollment. A run that escalates before the submit step writes no verification
    record, and the renderer reads verification records and nothing else.
    """
    escalation_only = [
        {"event": "run_started", "run_id": "r", "ts": "t", "config": {}},
        {"event": "escalation", "run_id": "r", "ts": "t", "step_key": "provider.open",
         "outcome": "not_acted",
         "reason": reason_for(Outcome.NOT_ACTED, RESOLVE_BUDGET)},
    ]
    report = render_report([], escalation_only)
    assert ABSENT_NPI in report or "not_acted" in report, (
        "the report for an escalated run is " + repr(report) + "; a reader is told "
        "nothing at all about the identifier that failed, and the escalation record "
        "the renderer would have to read carries no entity to name"
    )
