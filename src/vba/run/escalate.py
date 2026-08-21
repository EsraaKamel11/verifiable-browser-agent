from vba.oracle.delta import Outcome

_WHY = {
    Outcome.DISCREPANCY: (
        "The portal reported success but the record store shows nothing posted for "
        "this entity. This is the silent-rejection case and needs a human."
    ),
    Outcome.MISFILED: (
        "A record was created, but its identity does not match what the contract "
        "asked for. Do not retry; the wrong record must be reviewed first."
    ),
    Outcome.UNVERIFIABLE: (
        "The record store could not be reached, so whether the act posted is unknown. "
        "Not retried, because a retry on an unknown can duplicate."
    ),
    Outcome.VERIFIED_NOT_DONE: (
        "The portal was unavailable and the record store confirms nothing posted. "
        "Safe to retry when the portal returns."
    ),
    Outcome.DUPLICATED: (
        "More than one record appeared for a single act. This should be impossible "
        "under a fresh baseline; the run halted."
    ),
}


def reason_for(outcome: Outcome, attempts: int = 0) -> str:
    if outcome in _WHY:
        return _WHY[outcome]
    return ("Resolution did not converge after " + str(attempts) + " attempts.")
