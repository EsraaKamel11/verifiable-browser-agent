# tests/unit/test_external_pages.py
import pathlib

import pytest
from playwright.async_api import async_playwright

from vba.perceive.fingerprint import fingerprint
from vba.perceive.snapshot import snapshot

FIXTURES = sorted(pathlib.Path("tests/fixtures/external").glob("*.html"))


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
async def test_perception_enumerates_elements_on_a_page_we_did_not_write(path):
    """Spec 10.1: breaks the co-evolution loop for the differentiating layer."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.goto(path.resolve().as_uri())
        obs = await snapshot(page, epoch=1, contract="external", step_key="probe")
        await browser.close()
    assert obs.elements, "no interactive elements found on " + path.name
    assert all(e.target_id == i for i, e in enumerate(obs.elements))
    assert all(isinstance(e.is_submit, bool) for e in obs.elements)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
async def test_the_fingerprint_is_stable_across_two_loads_of_the_same_page(path):
    """If the fingerprint is unstable on a page we did not design, it is unstable."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        prints = []
        for _ in range(2):
            await page.goto(path.resolve().as_uri())
            obs = await snapshot(page, epoch=1, contract="external", step_key="probe")
            prints.append(obs.fingerprint)
        await browser.close()
    assert prints[0] == prints[1]


async def test_different_external_pages_fingerprint_differently():
    """Sanity: the fingerprint must discriminate, not collapse everything.

    Controller ruling R9: the name promises more than a bare count check verifies.
    Actually load each committed fixture, compute its fingerprint, and require the
    three to be pairwise distinct, in addition to keeping the >= 3 guard.
    """
    assert len(FIXTURES) >= 3, "capture at least three external pages"

    prints = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        for path in FIXTURES:
            await page.goto(path.resolve().as_uri())
            obs = await snapshot(page, epoch=1, contract="external", step_key="probe")
            prints.append(obs.fingerprint)
        await browser.close()

    assert len(set(prints)) == len(prints), "fingerprints collapsed on distinct pages"
