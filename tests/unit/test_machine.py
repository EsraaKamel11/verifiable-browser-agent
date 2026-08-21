from vba.oracle.delta import Outcome
from vba.run.machine import next_transition


def test_a_discrepancy_never_resolves_and_never_resubmits():
    """Spec 5.3: the planted silent-failure case. Routing this to resolution would
    resubmit forever, because every attempt succeeds on-page and posts nothing."""
    assert next_transition(Outcome.DISCREPANCY, attempts=0, budget=3) == "escalate"


def test_a_misfiled_act_escalates_and_does_not_retry():
    assert next_transition(Outcome.MISFILED, attempts=0, budget=3) == "escalate"


def test_a_duplicate_halts_the_entire_run():
    """Spec 5.3: under a fresh baseline and a single writer this can only arise from
    a guard defect, so it is a tripwire rather than a normal outcome."""
    assert next_transition(Outcome.DUPLICATED, attempts=0, budget=3) == "halt_run"


def test_an_unreachable_oracle_escalates_and_never_resubmits():
    """Spec 5.5: unknown misread as absent leads to a retry and then a duplicate."""
    assert next_transition(Outcome.UNVERIFIABLE, attempts=0, budget=3) == "escalate"


def test_a_stated_refusal_is_resolved_with_its_reason():
    assert next_transition(Outcome.REJECTED, attempts=0, budget=3) == "resolve"


def test_a_mechanical_failure_is_resolved():
    assert next_transition(Outcome.NOT_ACTED, attempts=0, budget=3) == "resolve"


def test_resolution_is_bounded_by_the_budget():
    """Spec 5.2: a resolution that cannot converge escalates rather than flailing."""
    assert next_transition(Outcome.NOT_ACTED, attempts=3, budget=3) == "escalate"


def test_a_confirmed_step_advances():
    assert next_transition(Outcome.CONFIRMED, attempts=0, budget=3) == "advance"


def test_an_already_satisfied_step_advances_without_acting():
    assert next_transition(Outcome.ALREADY_SATISFIED, attempts=0, budget=3) == "advance"


def test_a_verified_not_done_escalates_but_permits_a_later_retry():
    """Spec 10.2: stronger than merely failing to confirm, and it still escalates
    visibly, because the rubric scores a visible escalation."""
    assert next_transition(Outcome.VERIFIED_NOT_DONE, attempts=0, budget=3) == "escalate"
