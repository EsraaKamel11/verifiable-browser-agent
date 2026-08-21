# tests/heldout/test_case_4_second_silent_failure.py
"""Held-out case 4: an additional silently-failing provider.

The frozen world plants exactly one. Provider 1700000005 is flagged silent_fail in
world/seed_data.py, there is no admin route that flags another, and the world is
part of the system under test, so a second one cannot be created the way the first
one exists.

What is created instead is the same situation at the record boundary: the run's
oracle is routed through the harness proxy, and one further provider is suppressed
there, so the portal returns a success page and a confirmation number while the
record store the run is reading answers that nothing posted. Every input the agent
sees is identical to the planted case. The fidelity gap, stated plainly: the real
record store DOES hold that provider's row, so a third party re-deriving this run
against the world's own store would find a row the run reported as absent. The
verdict is correct relative to the oracle the run was given, and this case is
scored as a partial exercise for that reason.

What it buys, and what the single planted provider cannot show: two discrepancies
in one batch. Spec 5.3 says DISCREPANCY stops one provider and the rest of the
batch proceeds. Until now nothing had ever escalated and then continued.

One live run, two providers, both defaulting to the requested payer so nothing
about the payer selection is confounded with what is being scored here.
"""
import httpx
import pytest

from .conftest import events, run_cli

pytestmark = [pytest.mark.heldout, pytest.mark.world, pytest.mark.evals]

PLANTED = "1700000005"          # Dr. Alan Reese, silent_fail in the world's own seed
SUPPRESSED = "1700000001"       # Dr. Maria Santos, made silent at the record boundary
PAYER = "Aetna"                 # the page default for both, so no selection confound


def test_live_a_second_silent_failure_escalates_and_the_batch_continues(
        world, reset_world, record_store_proxy, tmp_path):
    reset_world("A")
    armed = httpx.post(record_store_proxy + "/control/suppress/" + SUPPRESSED, timeout=5)
    assert armed.status_code == 200 and SUPPRESSED in armed.json()["suppressed"], (
        "the suppression was not armed, so this run would be an ordinary batch"
    )

    code, run_dir, records, stdout, stderr = run_cli(
        [PLANTED, SUPPRESSED], payer=PAYER, runs_dir=tmp_path / "runs",
        oracle_base=record_store_proxy)

    assert run_dir is not None, ("the run wrote nothing. rc=" + str(code)
                                 + " stderr=" + stderr[-600:])
    verifications = events(records, "verification")
    escalations = events(records, "escalation")
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    by_entity = {str((v.get("entity") or {}).get("npi")): v for v in verifications}
    context = (" verifications=" + str([(str((v.get('entity') or {}).get('npi')),
                                         v["outcome"], v.get("page_confirmation"))
                                        for v in verifications])
               + " escalations=" + str([(e["outcome"], e["step_key"])
                                        for e in escalations]))

    # The batch reached both entities: the first discrepancy stopped one provider
    # and not the run.
    assert set(by_entity) == {PLANTED, SUPPRESSED}, (
        "the run adjudicated " + str(sorted(by_entity)) + "." + context)
    assert by_entity[PLANTED]["outcome"] == "discrepancy", context
    assert by_entity[SUPPRESSED]["outcome"] == "discrepancy", context
    assert len(escalations) == 2, context

    # Spec 8.2: the report names the confirmation number that appears nowhere in
    # the record the run was reading, for each of them.
    for npi in (PLANTED, SUPPRESSED):
        confirmation = by_entity[npi].get("page_confirmation")
        assert confirmation, "no confirmation number was captured for " + npi + context
        assert confirmation in report, (
            "the report does not name " + npi + "'s confirmation number." + context)
        assert "Not enrolled" in report

    # The fidelity gap, asserted rather than asserted away: the world's own store
    # holds the suppressed provider's row, and this is what a re-derivation against
    # the real store would find.
    truth = httpx.get(world + "/api/sor/enrollment/" + SUPPRESSED, timeout=5).json()
    assert truth["count"] == 1, (
        "the suppressed provider has " + str(truth["count"]) + " rows in the world's "
        "own store; the simulated silent failure did not behave as described")
