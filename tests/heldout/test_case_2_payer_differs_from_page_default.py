# tests/heldout/test_case_2_payer_differs_from_page_default.py
"""Held-out case 2: a provider whose correct payer differs from the page default.

The contract's identity key is [npi, payer], and the payer is an invocation
parameter rather than a contract constant. The record page pre-selects the
provider's own payer, so for most providers the work order and the page agree and
the dropdown never has to be touched. The rubric ran two providers, and the page
default matched the requested payer for both, so the select step was never load
bearing in a scored run.

This case asks for a provider where they disagree. Provider 1700000002's record
page pre-selects UnitedHealthcare; the work order says Aetna. The agent has to
change the selection, and if it does not, the record is filed under the page's
choice rather than the contract's.

Pass criterion, fixed before the run: the submit step is CONFIRMED and the row in
the record store carries the requested payer. MISFILED is a failure of the case,
and a specific one: it would mean the adjudicator held, correctly refusing to call
a wrong-entity filing a success, while the capability being scored did not.

One live run.
"""
import httpx
import pytest

from .conftest import events, run_cli

pytestmark = [pytest.mark.heldout, pytest.mark.world, pytest.mark.evals]

NPI = "1700000002"                  # Dr. James Okafor, page default UnitedHealthcare
REQUESTED_PAYER = "Aetna"
PAGE_DEFAULT = "UnitedHealthcare"


def test_the_page_default_really_does_differ(world, reset_world):
    """Non-vacuity, and cheap to check before spending a live run on it."""
    from .conftest import portal_session
    with portal_session(world) as client:
        page = client.get("/provider/" + NPI)
    assert "<option value='" + PAGE_DEFAULT + "' selected>" in page.text, (
        "provider " + NPI + " no longer defaults to " + PAGE_DEFAULT
        + "; this case would be scoring nothing"
    )


def test_live_the_record_is_filed_under_the_requested_payer(world, reset_world,
                                                            tmp_path):
    reset_world("A")
    code, run_dir, records, stdout, stderr = run_cli(
        [NPI], payer=REQUESTED_PAYER, runs_dir=tmp_path / "runs")

    assert run_dir is not None, ("the run wrote nothing. rc=" + str(code)
                                 + " stderr=" + stderr[-600:])
    verifications = events(records, "verification")
    row = httpx.get(world + "/api/sor/enrollment/" + NPI, timeout=5).json()
    context = (" verifications=" + str([(v["outcome"], v.get("entity"))
                                        for v in verifications])
               + " record_store=" + str(row)
               + " escalations=" + str([(e["outcome"], e["reason"])
                                        for e in events(records, "escalation")]))

    assert verifications, "the run never reached the step that files anything." + context
    assert verifications[-1]["outcome"] == "confirmed", (
        "the submit step ended as " + verifications[-1]["outcome"] + "." + context)
    assert row["count"] == 1, "the record store holds " + str(row["count"]) + " rows." + context
    assert row["latest"]["payer"] == REQUESTED_PAYER, (
        "the enrollment was filed under " + repr(row["latest"]["payer"])
        + " rather than the requested " + repr(REQUESTED_PAYER) + "." + context)
