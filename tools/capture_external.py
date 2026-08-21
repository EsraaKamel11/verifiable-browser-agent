# tools/capture_external.py
"""Capture public form pages as committed fixtures.

These pages were NOT authored for this project. Perception and fingerprinting are
validated against them so the differentiating layer is not only ever tested against
a world built alongside it (spec 10.1, mitigation 2).

Pick pages that are static HTML forms and whose terms permit local copies. Record
the source URL and capture date in a header comment inside each saved file.

Guard: a captured page can silently be a bot-wall interstitial (Cloudflare's
"Just a moment...", Akamai's "Attention Required!") instead of the real content -
the raw HTTP response can still be a 200 with a plausible-looking title. Before
this tool exits 0, it re-loads the saved fixture through the SAME extractor the
test suite uses and refuses to keep the file if the extractor found too few
interactive elements or the title matches a known challenge marker. This is what
made the defect in an earlier capture (a Cloudflare challenge page saved as if it
were the real W3C tutorial) loud instead of silent.
"""
import asyncio
import datetime
import sys
from pathlib import Path

from playwright.async_api import async_playwright

from vba.perceive.snapshot import snapshot

OUT = Path("tests/fixtures/external")

MIN_ELEMENTS = 5
CHALLENGE_TITLE_MARKERS = ("just a moment", "attention required")


async def capture(url: str, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / (name + ".html")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle")
        html = await page.content()
        today = datetime.date.today().isoformat()
        header = "<!-- source: " + url + " captured: " + today + " -->\n"
        dest.write_text(header + html, encoding="utf-8")

        # Verify the saved fixture, not the live page: reload exactly what the
        # test suite will read, through the same extractor it uses.
        await page.goto(dest.resolve().as_uri())
        title = (await page.title()).strip()
        obs = await snapshot(page, epoch=1, contract="external", step_key="probe")
        await browser.close()

    lowered = title.lower()
    if any(marker in lowered for marker in CHALLENGE_TITLE_MARKERS):
        dest.unlink()
        sys.exit(
            "capture_external: refused to keep fixture for " + url
            + " - page title '" + title + "' looks like a bot-challenge"
            + " interstitial, not the real page"
        )
    if len(obs.elements) < MIN_ELEMENTS:
        dest.unlink()
        sys.exit(
            "capture_external: refused to keep fixture for " + url
            + " - only " + str(len(obs.elements)) + " interactive element(s)"
            + " found (need >= " + str(MIN_ELEMENTS) + "); this is not a usable"
            + " form-rich fixture"
        )

    print(
        "captured " + str(len(obs.elements)) + " interactive elements from "
        + url + " -> " + str(dest)
    )


if __name__ == "__main__":
    asyncio.run(capture(sys.argv[1], sys.argv[2]))
