from vba.oracle.delta import Baseline, Outcome, OracleReading, PageVerdict, adjudicate


def R(count, enrolled=None, latest=None, reachable=True):
    if enrolled is None:
        enrolled = count > 0
    return OracleReading(reachable=reachable, enrolled=enrolled, count=count,
                         latest=latest, raw={})


IDENT = {"npi": "1700000001", "payer": "Aetna"}
GOOD_ROW = {"npi": "1700000001", "payer": "Aetna", "confirmation_id": "PC-000123"}


def _adj(base, after, page, conf="PC-000123", ident=IDENT, table=None):
    return adjudicate(Baseline(base, epoch=1), after, page, ident, conf, table or [])


def test_count_up_by_one_with_matching_identity_and_confirmation_is_confirmed():
    assert _adj(R(0), R(1, latest=GOOD_ROW), PageVerdict.PASSED) is Outcome.CONFIRMED


def test_a_page_success_with_no_new_row_is_a_discrepancy():
    """Spec 5.3 and the planted silent-failure case: the page says submitted and
    nothing posted. This is the requirement the whole project exists for."""
    assert _adj(R(0), R(0), PageVerdict.PASSED) is Outcome.DISCREPANCY


def test_a_confirmation_number_that_appears_under_another_entity_is_misfiled():
    """Spec 5.3: a per-entity read cannot see a record filed under the wrong entity,
    so a discrepancy triggers a whole-table reconciliation."""
    table = [{"npi": "1700000002", "payer": "Aetna", "confirmation_id": "PC-000123"}]
    assert _adj(R(0), R(0), PageVerdict.PASSED, table=table) is Outcome.MISFILED


def test_a_new_row_with_the_wrong_payer_is_misfiled():
    """The failure a literal-valued memory fix would produce."""
    wrong = {"npi": "1700000001", "payer": "Cigna", "confirmation_id": "PC-000123"}
    assert _adj(R(0), R(1, latest=wrong), PageVerdict.PASSED) is Outcome.MISFILED


def test_a_confirmation_number_matching_nothing_is_not_confirmed():
    """Spec 5.3: CONFIRMED requires three agreements, and the third catches a page
    that mints a confirmation number corresponding to nothing."""
    row = {"npi": "1700000001", "payer": "Aetna", "confirmation_id": "PC-999999"}
    assert _adj(R(0), R(1, latest=row), PageVerdict.PASSED) is not Outcome.CONFIRMED


def test_two_new_rows_is_duplicated():
    assert _adj(R(0), R(2, latest=GOOD_ROW), PageVerdict.PASSED) is Outcome.DUPLICATED


def test_an_unchanged_count_after_an_infrastructural_failure_is_verified_not_done():
    """Spec 5.3 and the portal-outage case: the record answered and shows nothing
    posted. Stronger than merely failing to confirm."""
    assert _adj(R(0), R(0), PageVerdict.INFRASTRUCTURAL) is Outcome.VERIFIED_NOT_DONE


def test_an_unchanged_count_after_a_stated_refusal_is_rejected():
    assert _adj(R(0), R(0), PageVerdict.REJECTED) is Outcome.REJECTED


def test_an_unchanged_count_after_a_mechanical_failure_is_not_acted():
    assert _adj(R(0), R(0), PageVerdict.MECHANICAL) is Outcome.NOT_ACTED


def test_an_unreachable_oracle_is_unverifiable_regardless_of_the_page():
    """Spec 5.4: unknown is not the same as verified absent. Misreading this as
    not-enrolled is what leads to a retry and then a duplicate."""
    assert _adj(R(0), R(0, reachable=False), PageVerdict.PASSED) is Outcome.UNVERIFIABLE


def test_an_already_enrolled_baseline_is_already_satisfied():
    """Spec 5.3: never submit when the record already shows the work is done."""
    assert _adj(R(1, latest=GOOD_ROW), R(1, latest=GOOD_ROW),
                PageVerdict.PASSED) is Outcome.ALREADY_SATISFIED
