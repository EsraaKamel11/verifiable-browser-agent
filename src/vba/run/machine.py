from vba.oracle.delta import Outcome
from vba.memory.capture import slice_capture, to_stored_actions
from vba.oracle.delta import Baseline, PageVerdict, adjudicate
from vba.verify.page import page_verify

from .escalate import reason_for
from .outcomes import RunResult, StepOutcome

# Spec 5.3. The routing is a table rather than control flow so it can be tested
# exhaustively and so no branch is reachable only through a live browser.
_ROUTE = {
    Outcome.CONFIRMED:         "advance",
    Outcome.ALREADY_SATISFIED: "advance",
    Outcome.DISCREPANCY:       "escalate",   # never resolve; the page lies
    Outcome.MISFILED:          "escalate",   # something posted, but not what we asked
    Outcome.UNVERIFIABLE:      "escalate",   # unknown is not absent
    Outcome.VERIFIED_NOT_DONE: "escalate",   # provably nothing posted; retry is safe later
    Outcome.DUPLICATED:        "halt_run",   # invariant tripwire
    Outcome.REJECTED:          "resolve",
    Outcome.NOT_ACTED:         "resolve",
}


def next_transition(outcome: Outcome, attempts: int, budget: int) -> str:
    route = _ROUTE[outcome]
    if route == "resolve" and attempts >= budget:
        return "escalate"
    return route


RESOLVE_BUDGET = 3


# run_step() is imported from .drive in Task 14 (src/vba/run/drive.py)
async def run_entity(contract, bindings, deps) -> RunResult:
    """Spec 5.1. One entity through every step of the contract."""
    result = RunResult(entity=dict(bindings))

    for step in contract.steps:
        attempts = 0
        while True:
            outcome = await run_step(step, contract, bindings, attempts, deps)
            result.outcomes.append(outcome)
            route = next_transition(outcome.outcome, attempts, RESOLVE_BUDGET)

            if route == "advance":
                break
            if route == "resolve":
                attempts += 1
                continue

            result.terminal = outcome.outcome
            result.escalated = True
            result.escalation_reason = reason_for(outcome.outcome, attempts)
            deps.audit.escalation(step.step_key, outcome.outcome,
                                  result.escalation_reason)
            if route == "halt_run":
                deps.halt_run = True
            return result

    result.terminal = result.outcomes[-1].outcome if result.outcomes else None
    return result
