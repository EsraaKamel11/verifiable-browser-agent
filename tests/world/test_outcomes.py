# tests/world/test_outcomes.py
import re

import httpx
import pytest
from playwright.async_api import async_playwright

from vba.oracle.client import OracleClient
from vba.oracle.delta import Baseline, Outcome, PageVerdict, adjudicate
from vba.perceive.snapshot import snapshot

pytestmark = pytest.mark.world

SILENT_FAIL_NPI = "1700000005"
NORMAL_NPI = "1700000001"

USERNAME = "ops@cascade-credentialing.example"
PASSWORD = "Staging2026!"
OTP_CODE = "246810"

# The world's success page mints a confirmation number of this shape (see
# world/system_of_record.py: f"PC-{...:06d}" and world/app.py's silent-fail branch).
CONFIRMATION_RE = re.compile(r"PC-\d+")


def _client(world):
    return OracleClient(world, "{base}/api/sor/enrollment/{npi}")


def _extract_confirmation(page_text: str) -> str | None:
    m = CONFIRMATION_RE.search(page_text)
    return m.group(0) if m else None


async def test_the_oracle_reads_the_real_record_store(world, reset_world):
    """Spec 7.1: tier 2's defense is that its oracle side is real HTTP, not a stub."""
    reading = await _client(world).read(NORMAL_NPI)
    assert reading.reachable is True
    assert reading.count == 0


async def test_the_silent_failure_provider_produces_a_discrepancy(world, reset_world):
    """The planted case, adjudicated end to end against the real store.

    The portal shows success and mints a confirmation number; nothing posts.

    Controller ruling R15: adjudicate() was hardened so a None page_confirmation on
    a delta-of-one path never yields CONFIRMED. Here the delta stays 0 (nothing
    posted), so the page-mints-a-confirmation-but-nothing-matches property is
    exercised through whole-table reconciliation instead: the confirmation number
    the page shows appears in no row of the real table, so reconciliation finds no
    match and the outcome falls through to DISCREPANCY rather than MISFILED.
    """
    oracle = _client(world)
    before = await oracle.read(SILENT_FAIL_NPI)

    with httpx.Client(base_url=world, follow_redirects=True, timeout=10) as c:
        c.post("/login", data={"username": USERNAME, "password": PASSWORD})
        c.post("/verify", data={"code": OTP_CODE, "captcha": "1"})
        page = c.post("/provider/" + SILENT_FAIL_NPI + "/enroll", data={"payer": "Aetna"})

    assert "Submitted successfully" in page.text        # the page claims success
    page_confirmation = _extract_confirmation(page.text)
    assert page_confirmation is not None, "the world's success page always mints one"

    after = await oracle.read(SILENT_FAIL_NPI)
    outcome = adjudicate(Baseline(before, epoch=1), after, PageVerdict.PASSED,
                         {"npi": SILENT_FAIL_NPI}, page_confirmation, await oracle.read_all())
    assert outcome is Outcome.DISCREPANCY


async def test_a_normal_provider_confirms(world, reset_world):
    oracle = _client(world)
    before = await oracle.read(NORMAL_NPI)
    with httpx.Client(base_url=world, follow_redirects=True, timeout=10) as c:
        c.post("/login", data={"username": USERNAME, "password": PASSWORD})
        c.post("/verify", data={"code": OTP_CODE, "captcha": "1"})
        page = c.post("/provider/" + NORMAL_NPI + "/enroll", data={"payer": "Aetna"})

    page_confirmation = _extract_confirmation(page.text)
    assert page_confirmation is not None

    after = await oracle.read(NORMAL_NPI)
    outcome = adjudicate(Baseline(before, epoch=1), after, PageVerdict.PASSED,
                         {"npi": NORMAL_NPI, "payer": "Aetna"}, page_confirmation, [])
    assert outcome is Outcome.CONFIRMED


async def test_a_portal_outage_yields_verified_not_done_because_the_oracle_answers(
        world, reset_world):
    """Spec 10.2: the outage flag gates the page routes and not the reconciliation
    route, which is why this is verified-not-done rather than unconfirmable. That
    independence is simulated, and the spec says so.

    Task 14 concern 4: after the outage flag is cleared, prove recovery is real
    rather than assuming the finally-block POST worked -- fetch healthz again and
    check the state it reports, not just the HTTP status of the /admin call.
    """
    oracle = _client(world)
    before = await oracle.read(NORMAL_NPI)
    httpx.post(world + "/admin/portal/down", timeout=5)
    try:
        after = await oracle.read(NORMAL_NPI)
        assert after.reachable is True          # the oracle stays up
        outcome = adjudicate(Baseline(before, epoch=1), after,
                             PageVerdict.INFRASTRUCTURAL, {"npi": NORMAL_NPI}, None, [])
        assert outcome is Outcome.VERIFIED_NOT_DONE
    finally:
        r = httpx.post(world + "/admin/portal/up", timeout=5)
        assert r.status_code == 200
        # Recovery is provable, not assumed: a subsequent healthz call must both
        # succeed and report the flag actually cleared.
        healthz = httpx.get(world + "/healthz", timeout=5)
        assert healthz.status_code == 200
        assert healthz.json()["state"]["portal"] == "up"


async def test_a_blackholed_oracle_yields_unverifiable_not_absent(world, reset_world):
    """Spec 7.3. The world has no control that makes the record store unreachable,
    so without this the unconfirmable branch ships UNEXERCISED.

    This is the most dangerous latent chain in the design: an oracle failure read as
    not-enrolled leads to a retry, and a keyless retry duplicates.
    """
    dead = OracleClient("http://127.0.0.1:9", "{base}/api/sor/enrollment/{npi}",
                        timeout=0.5)
    reading = await dead.read(NORMAL_NPI)
    assert reading.reachable is False
    assert reading.count == 0                   # count is meaningless when unreachable

    before = await _client(world).read(NORMAL_NPI)
    outcome = adjudicate(Baseline(before, epoch=1), reading, PageVerdict.PASSED,
                         {"npi": NORMAL_NPI}, None, [])
    assert outcome is Outcome.UNVERIFIABLE      # never DISCREPANCY, never a retry


async def test_layout_b_record_page_and_bounce_page_fingerprint_differently(
        world, reset_world):
    """Controller ruling R21: the property capture slicing depends on.

    On layout B, submitting without the review checkbox refuses the submit and
    shows a different page (the "review required" bounce) than the record/entry
    page. If those two pages fingerprinted the same, step-boundary detection could
    not tell "still on the form" apart from "bounced back", which would break
    self-healing and retries alike. Driven with a real headless browser against the
    real layout-B world, not a stub.
    """
    reset_world("B")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        try:
            await page.goto(world + "/")
            await page.fill("#username", USERNAME)
            await page.fill("#password", PASSWORD)
            await page.click("#sign-in")

            await page.wait_for_selector("#otp")
            await page.fill("#otp", OTP_CODE)
            await page.check("#not-a-robot")
            await page.click("#verify")

            await page.wait_for_selector("a[href='/provider/" + NORMAL_NPI + "']")
            await page.goto(world + "/provider/" + NORMAL_NPI)
            entry = await snapshot(page, epoch=1, contract="world-b", step_key="record")

            # Submit WITHOUT checking "reviewed": layout B refuses and bounces.
            await page.click("#confirm-and-submit")
            await page.wait_for_url("**/enroll")
            bounce = await snapshot(page, epoch=1, contract="world-b", step_key="record")
        finally:
            await browser.close()

    # Bounce-only text: the entry page also contains "I have reviewed this
    # enrollment" (the checkbox label), so that phrase alone would not prove the
    # snapshot actually landed on the bounce page rather than the entry page.
    assert "please confirm you have reviewed" in bounce.text.lower()
    assert entry.fingerprint != bounce.fingerprint, (
        "the record page and the review-required bounce page fingerprinted "
        "identically; capture slicing cannot distinguish these page boundaries"
    )


async def test_no_identifier_ever_exceeds_its_baseline_by_more_than_one(world, reset_world):
    """Spec 7.2: a global postcondition over the real table.

    This test provisions its own row rather than relying on residue left by tests
    that ran earlier in the file: every other test's setup calls reset_world,
    which wipes the SoR, so a version of this test that only reads the table
    without writing to it first would run over an empty table and pass
    vacuously -- exactly what the docstring used to (wrongly) claim it guarded
    against. Reset (layout A), perform one real enrollment over HTTP for a
    normal provider (the same login/verify/enroll flow as
    test_a_normal_provider_confirms), then assert both that the table is
    genuinely non-empty and that no identifier appears more than once. Since the
    reset just zeroed every baseline to 0, "exceeds its baseline by more than
    one" here means "appears more than once".
    """
    with httpx.Client(base_url=world, follow_redirects=True, timeout=10) as c:
        c.post("/login", data={"username": USERNAME, "password": PASSWORD})
        c.post("/verify", data={"code": OTP_CODE, "captcha": "1"})
        c.post("/provider/" + NORMAL_NPI + "/enroll", data={"payer": "Aetna"})

    rows = httpx.get(world + "/api/sor/enrollments", timeout=5).json()["enrollments"]
    assert rows, "the table must be non-empty here, or this postcondition is vacuous"
    counts = {}
    for r in rows:
        counts[r["npi"]] = counts.get(r["npi"], 0) + 1
    assert all(v <= 1 for v in counts.values()), counts
