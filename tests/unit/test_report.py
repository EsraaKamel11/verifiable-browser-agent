from vba.report.render import render_report
from vba.report.rederive import rederivation_rows


AUDIT = [
    {"event": "verification", "step_key": "enrollment.submit", "outcome": "discrepancy",
     "baseline": {"count": 0}, "after": {"count": 0},
     "page_confirmation": "PC-481920", "entity": {"npi": "1700000005"},
     "ts": "2026-08-20T14:22:05Z"},
]


def test_the_report_names_the_confirmation_number_and_its_absence():
    """Spec 8.2: the strongest exhibit is a confirmation number that corresponds to
    nothing in the record store."""
    text = render_report([], AUDIT)
    assert "PC-481920" in text
    assert "not enrolled" in text.lower()
    assert "escalated" in text.lower()


def test_the_report_contains_no_em_dashes():
    """House rule for this document family."""
    assert "\\u2014" not in render_report([], AUDIT).encode("unicode_escape").decode()


def test_rederivation_rows_carry_the_inputs_and_the_rule():
    """Spec 8.3: a skeptical reviewer recomputes every verdict by hand without
    trusting the harness."""
    rows = rederivation_rows(AUDIT)
    assert rows[0]["baseline_count"] == 0
    assert rows[0]["after_count"] == 0
    assert rows[0]["page_claimed"] is True
    assert "rule" in rows[0]
