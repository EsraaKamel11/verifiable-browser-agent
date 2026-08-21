# tests/heldout/test_case_3_id_reused_changed_text.py
"""Held-out case 3: a layout that reuses an existing control id.

The base world only exercises the easy drift branch. Its three layouts rename the
submit control outright (submit-enrollment, confirm-and-submit, place-enrollment),
so a stored fix fails to resolve and pre-apply is defeated by resolution. The hard
branch, where the stored fix still resolves but the page's semantics changed, has
no representative in that world, which is why this case exists.

Two variants, both built by editing the world's own record-page bytes:

  A. the submit control keeps its id and its text changes. Both mechanisms can see
     this one; the assertion is that the fingerprint sees it FIRST, before the
     stored action is ever consulted.
  B. the submit control is untouched, id and accessible name alike, and a mandatory
     review checkbox appears next to it. still_resolves answers true here, so
     resolution cannot defeat pre-apply and the fingerprint is the only thing that
     can. This is the branch the README's own stated limit names: "a control that
     keeps its id and its accessible name but changes what it does will be
     replayed. The structural fingerprint is the defense, and it is a coarse one."

A control case runs the unchanged page through the same path and requires the fix
to be pre-applied, so a variant that fails for some unrelated reason cannot pass
for the wrong one.

No model. The resolution session is stubbed at its own module boundary, which is
the only LLM call in the loop; every other line executed here is the frozen agent.
"""
import pytest
from playwright.async_api import async_playwright

from vba.audit.log import AuditLog
from vba.contract.gate import evaluate_gate
from vba.contract.loader import load_contract
from vba.guard.credentials import CredentialVault
from vba.guard.scrub import Scrubber
from vba.memory.capture import to_stored_actions
from vba.memory.store import FixStore, LearnedFix
from vba.oracle.delta import OracleReading
from vba.perceive.snapshot import snapshot
from vba.run.deps import Deps
from vba.run.drive import CtxHolder, run_step

from .conftest import CONTRACT, portal_session

pytestmark = [pytest.mark.heldout, pytest.mark.world]

NPI = "1700000001"
BINDINGS = {"npi": NPI, "payer": "Aetna"}
SUBMIT_ID = "submit-enrollment"

# The world's layout-A submit control, verbatim from world/app.py's _SUBMIT_A.
BASE_BUTTON = '<button id="submit-enrollment" type="submit">Submit enrollment</button>'
# Variant A: the same control, the same id, different text.
RENAMED_BUTTON = ('<button id="submit-enrollment" type="submit">'
                  'Submit enrollment for review</button>')
# Variant B: the control is untouched and the form gains a required control.
REVIEW_CHECKBOX = ('<label><input type="checkbox" name="reviewed" id="reviewed" '
                   'value="1"/> I have reviewed this enrollment</label>')


class _FakeOracle:
    """The record store is not what case 3 is about. This one answers, reachably,
    that nothing is enrolled, so the step is neither short-circuited as already
    satisfied nor refused for want of a baseline."""

    async def read(self, npi: str) -> OracleReading:
        return OracleReading(reachable=True, enrolled=False, count=0, latest=None,
                             raw={"npi": npi, "enrolled": False, "count": 0})

    async def read_all(self) -> list:
        return []


@pytest.fixture
def record_html(world, reset_world):
    """The world's real record page for one provider on layout A."""
    reset_world("A")
    with portal_session(world) as client:
        response = client.get("/provider/" + NPI)
    assert BASE_BUTTON in response.text, (
        "the world's layout-A record page no longer carries the submit control this "
        "case edits; the fixture would be testing nothing"
    )
    return response.text


def _submit_step(contract):
    return next(s for s in contract.steps if s.step_key == "enrollment.submit")


async def _observe(pages, html, contract, step):
    """Serve one page body and snapshot it with the frozen perception layer."""
    pages.serve(html)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        try:
            await page.goto(pages.url)
            return await snapshot(page, epoch=1, contract=contract.name,
                                  step_key=step.step_key)
        finally:
            await browser.close()


def _seed_promoted_fix(store, contract, step, observation):
    """A promoted fix for the submit step, built through the frozen capture path
    so its stored identity is whatever capture would really have written."""
    element = next(e for e in observation.elements if e.element_id == SUBMIT_ID)
    actions = to_stored_actions([(element, "click", None)], BINDINGS)
    fix = LearnedFix.new(
        site=contract.site, contract=contract.name, step_key=step.step_key,
        intent=step.intent, page_fingerprint=observation.fingerprint,
        actions=actions, match_mode="exact_identity", action_tier=3,
        verif_strength="cross_system", trials=1, successes=1, confidence=0.9,
    )
    store.write_candidate(fix)
    store.promote(fix.fix_id)
    return fix


async def _drive_once(pages, html, contract, step, store, audit, monkeypatch):
    """Run the frozen run_step against one served page with memory enabled."""
    import vba.resolve.session as session_module

    async def _no_session(*args, **kwargs):
        """The single LLM call in the loop, stubbed. Case 3 is about what happens
        BEFORE resolution is reached: if the fix is pre-applied, this is never
        called, and that is the property under test."""
        _no_session.calls += 1
    _no_session.calls = 0
    monkeypatch.setattr(session_module, "run_resolution", _no_session)

    pages.serve(html)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        try:
            await page.goto(pages.url)
            deps = Deps(page=page, audit=audit, vault=CredentialVault({}),
                        scrubber=Scrubber(), store=store, oracle=_FakeOracle(),
                        ctx_holder=CtxHolder(), grant=evaluate_gate(contract),
                        contract_name=contract.name)
            outcome = await run_step(step, contract, BINDINGS, 0, deps)
        finally:
            await browser.close()
    return outcome, _no_session.calls


def _memory_actions(audit):
    return [r for r in audit.records()
            if r.get("event") == "action" and str(r.get("source", "")).startswith("memory:")]


def _stale_events(audit):
    return [r for r in audit.records() if r.get("event") == "stale_fix_detected"]


async def test_the_unchanged_page_pre_applies_the_seeded_fix(
        record_html, static_pages, tmp_path, monkeypatch):
    """The control. Without it, a variant that misses for an unrelated reason would
    look like a correct refusal."""
    contract = load_contract(CONTRACT)
    step = _submit_step(contract)
    store = FixStore(tmp_path / "memory.db")
    entry = await _observe(static_pages, record_html, contract, step)
    fix = _seed_promoted_fix(store, contract, step, entry)

    audit = AuditLog(tmp_path / "control.jsonl", run_id="control")
    outcome, sessions = await _drive_once(static_pages, record_html, contract, step,
                                          store, audit, monkeypatch)

    assert _stale_events(audit) == [], "the unchanged page must not look stale"
    replayed = _memory_actions(audit)
    assert replayed, ("the seeded fix was not pre-applied on the page it was learned "
                      "on, so the variants below would prove nothing. outcome="
                      + str(outcome.outcome) + " source=" + outcome.source)
    assert replayed[0]["source"] == "memory:" + fix.fix_id
    assert sessions == 0, "a pre-applied fix must spawn no resolution session"


async def test_a_reused_id_with_changed_text_is_caught_by_the_fingerprint(
        record_html, static_pages, tmp_path, monkeypatch):
    """Variant A: the id survives, the text does not.

    The plan's wording for this case. Both the fingerprint comparison and
    still_resolves can see it, and the ordering is the point: spec 5.1 compares
    fingerprints before the stored action is consulted, so the detection is a
    visible stale_fix_detected event rather than a silent miss.
    """
    contract = load_contract(CONTRACT)
    step = _submit_step(contract)
    store = FixStore(tmp_path / "memory.db")
    entry = await _observe(static_pages, record_html, contract, step)
    fix = _seed_promoted_fix(store, contract, step, entry)

    renamed = record_html.replace(BASE_BUTTON, RENAMED_BUTTON)
    assert renamed != record_html
    variant = await _observe(static_pages, renamed, contract, step)
    assert any(e.element_id == SUBMIT_ID for e in variant.elements), (
        "the control id must survive, or this is the easy branch again"
    )

    audit = AuditLog(tmp_path / "variant-a.jsonl", run_id="variant-a")
    outcome, _sessions = await _drive_once(static_pages, renamed, contract, step,
                                           store, audit, monkeypatch)

    assert _stale_events(audit), (
        "no stale_fix_detected event: the changed text did not move the fingerprint"
    )
    assert _memory_actions(audit) == [], (
        "a fix learned on different page text was replayed. source=" + outcome.source
    )
    assert outcome.source == "cold"
    # Context for the results document rather than the property under test: on this
    # variant the stored identity is also broken, so both mechanisms would refuse.
    assert fix.still_resolves(variant, BINDINGS) is False


async def test_an_intact_control_on_a_changed_form_is_caught_by_the_fingerprint_alone(
        record_html, static_pages, tmp_path, monkeypatch):
    """Variant B: the hard branch.

    The stored control keeps its id and its accessible name, so still_resolves says
    yes and resolution cannot defeat pre-apply. The form around it now requires a
    review step, which the world's layout B shows is a genuine mid-run
    change. Only the structural fingerprint can refuse this, and it must.
    """
    contract = load_contract(CONTRACT)
    step = _submit_step(contract)
    store = FixStore(tmp_path / "memory.db")
    entry = await _observe(static_pages, record_html, contract, step)
    fix = _seed_promoted_fix(store, contract, step, entry)

    gated = record_html.replace(BASE_BUTTON, REVIEW_CHECKBOX + BASE_BUTTON)
    assert gated != record_html
    variant = await _observe(static_pages, gated, contract, step)
    assert fix.still_resolves(variant, BINDINGS) is True, (
        "the stored control was disturbed by the edit, so this variant is testing "
        "resolution failure again rather than the fingerprint"
    )

    audit = AuditLog(tmp_path / "variant-b.jsonl", run_id="variant-b")
    outcome, _sessions = await _drive_once(static_pages, gated, contract, step,
                                           store, audit, monkeypatch)

    assert _stale_events(audit), (
        "a fix whose control still resolves was not detected as stale: the new "
        "required control did not move the fingerprint"
    )
    assert _memory_actions(audit) == [], (
        "the fix was pre-applied on a form that had gained a required control, "
        "which is a submit fired on a page the fix was never learned on. source="
        + outcome.source
    )
    assert outcome.source == "cold"
