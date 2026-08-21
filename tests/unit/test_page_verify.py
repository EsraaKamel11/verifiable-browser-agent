# tests/unit/test_page_verify.py
from vba.contract.schema import Postcondition, Step
from vba.oracle.delta import PageVerdict
from vba.perceive.elements import Observation
from vba.verify.page import page_verify


STEP = Step(
    step_key="enrollment.submit", intent="file it", tier=3,
    satisfied_when="oracle.confirmed",
    postconditions=[
        Postcondition(text_present="Submitted successfully"),
        Postcondition(text_absent="Please confirm you have reviewed"),
    ],
)


def _obs(text: str) -> Observation:
    return Observation(url="http://h/p/1", epoch=1, elements=[], text=text,
                       fingerprint="f")


def test_the_expected_text_passes():
    assert page_verify(STEP, _obs("Submitted successfully"), 200) is PageVerdict.PASSED


def test_a_stated_business_refusal_is_rejected_not_mechanical():
    """Spec 5.2: a refusal is a different category from a click that did not land.
    The world bounces this BEFORE writing any record, so nothing was filed."""
    text = "Please confirm you have reviewed this enrollment before submitting."
    assert page_verify(STEP, _obs(text), 200) is PageVerdict.REJECTED


def test_a_5xx_is_infrastructural_and_never_routes_to_resolution():
    """Spec 5.2: without this, a portal outage sends the agent into a resolution
    spiral against an error page."""
    assert page_verify(STEP, _obs("503 - temporarily unavailable"), 503) \
        is PageVerdict.INFRASTRUCTURAL


def test_a_missing_expected_text_with_no_stated_reason_is_mechanical():
    assert page_verify(STEP, _obs("some other page"), 200) is PageVerdict.MECHANICAL


def test_a_step_with_no_postconditions_passes():
    bare = Step(step_key="provider.open", intent="open", tier=1)
    assert page_verify(bare, _obs("anything"), 200) is PageVerdict.PASSED
