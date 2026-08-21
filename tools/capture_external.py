# tools/capture_external.py
"""Capture public form pages as committed fixtures.

These pages were NOT authored for this project. Perception and fingerprinting are
validated against them so the differentiating layer is not only ever tested against
a world built alongside it (spec 10.1, mitigation 2).

Pick pages that are static HTML forms and whose terms permit local copies. Record
the source URL and capture date in a header comment inside each saved file.
"""
import asyncio
import datetime
import sys
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path("tests/fixtures/external")


async def capture(url: str, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle")
        html = await page.content()
        today = datetime.date.today().isoformat()
        header = "<!-- source: " + url + " captured: " + today + " -->\n"
        (OUT / (name + ".html")).write_text(header + html, encoding="utf-8")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(capture(sys.argv[1], sys.argv[2]))
