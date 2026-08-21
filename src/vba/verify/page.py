# src/vba/verify/page.py
from vba.contract.schema import Step
from vba.oracle.delta import PageVerdict
from vba.perceive.elements import Observation


def page_verify(step: Step, obs: Observation, http_status: int | None) -> PageVerdict:
    """Spec 5.2. Steers the loop; it can never decide whether work posted.

    Three categories, because collapsing them is how an outage becomes a resubmit:
    infrastructural never routes to resolution, a stated refusal carries its reason
    into the next attempt, and a mechanical failure means the act did not land.
    """
    if http_status is not None and http_status >= 500:
        return PageVerdict.INFRASTRUCTURAL

    text = obs.text or ""

    # A stated refusal is checked first: it is a more specific signal than a missing
    # success string, and the two co-occur by construction.
    for pc in step.postconditions:
        if pc.text_absent and pc.text_absent in text:
            return PageVerdict.REJECTED

    for pc in step.postconditions:
        if pc.text_present and pc.text_present not in text:
            return PageVerdict.MECHANICAL

    return PageVerdict.PASSED
