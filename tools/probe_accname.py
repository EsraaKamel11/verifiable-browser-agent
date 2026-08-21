# tools/probe_accname.py
"""Answer spec open question 1: do accessible names absorb field values?

The record page renders the NPI field as an input inside its own label. Under the
accname algorithm an embedded control inside its own label can contribute its VALUE
to its own name. If that happens here, the fingerprint must avoid accessible names
entirely (spec 6.2), because the name would differ per provider and one layout would
mint one fingerprint per provider.

Run this against a real snapshot. Do not reason about it.
"""
import asyncio
import json

from playwright.async_api import async_playwright

URL = "http://127.0.0.1:8799"


async def main() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.goto(URL + "/")
        await page.fill("#username", "ops@cascade-credentialing.example")
        await page.fill("#password", "Staging2026!")
        await page.click("#sign-in")
        await page.fill("#otp", "246810")
        await page.check("#not-a-robot")
        await page.click("#verify")
        await page.goto(URL + "/provider/1700000001")
        snapshot = await page.accessibility.snapshot()
        print(json.dumps(snapshot, indent=2))
        await browser.close()


asyncio.run(main())
