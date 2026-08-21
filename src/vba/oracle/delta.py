from dataclasses import dataclass
from enum import Enum


class Outcome(Enum):
    CONFIRMED = "confirmed"
    DISCREPANCY = "discrepancy"
    MISFILED = "misfiled"
    DUPLICATED = "duplicated"
    NOT_ACTED = "not_acted"
    REJECTED = "rejected"
    VERIFIED_NOT_DONE = "verified_not_done"
    UNVERIFIABLE = "unverifiable"
    ALREADY_SATISFIED = "already_satisfied"


class PageVerdict(Enum):
    PASSED = "passed"
    MECHANICAL = "mechanical"
    REJECTED = "rejected"
    INFRASTRUCTURAL = "infrastructural"


@dataclass(frozen=True)
class OracleReading:
    reachable: bool
    enrolled: bool
    count: int
    latest: dict | None
    raw: dict | None


@dataclass(frozen=True)
class Baseline:
    reading: OracleReading
    epoch: int


def _identity_matches(row: dict | None, expected: dict) -> bool:
    if row is None:
        return False
    return all(str(row.get(k)) == str(v) for k, v in expected.items())


def adjudicate(
    baseline: Baseline,
    after: OracleReading,
    page: PageVerdict,
    expected_identity: dict,
    page_confirmation: str | None,
    table: list[dict],
) -> Outcome:
    """Spec 5.3. Every outcome is a delta against the pre-act baseline.

    Absolute predicates would confirm work the agent never did, because a row left
    over from an earlier run satisfies "enrolled" without the agent having acted.
    """
    if not after.reachable:
        return Outcome.UNVERIFIABLE

    if baseline.reading.reachable and baseline.reading.count > 0:
        return Outcome.ALREADY_SATISFIED

    delta = after.count - baseline.reading.count

    if delta > 1:
        return Outcome.DUPLICATED

    if delta == 1:
        row = after.latest
        if not _identity_matches(row, expected_identity):
            return Outcome.MISFILED
        if page_confirmation and row.get("confirmation_id") != page_confirmation:
            return Outcome.DISCREPANCY
        return Outcome.CONFIRMED

    # delta == 0: nothing posted under the entity we asked about.
    if page is PageVerdict.PASSED:
        # A per-entity read cannot see a record filed under the wrong entity, so
        # reconcile the whole table before concluding.
        if page_confirmation:
            for row in table:
                if row.get("confirmation_id") == page_confirmation:
                    return Outcome.MISFILED
        return Outcome.DISCREPANCY
    if page is PageVerdict.REJECTED:
        return Outcome.REJECTED
    if page is PageVerdict.MECHANICAL:
        return Outcome.NOT_ACTED
    return Outcome.VERIFIED_NOT_DONE
