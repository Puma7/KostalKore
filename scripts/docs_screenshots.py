#!/usr/bin/env python3
"""Take the documentation screenshots from a running lab Home Assistant.

    python scripts/docs_screenshots.py --url http://127.0.0.1:8123 --token-file token.txt

Needs ``playwright`` and ``pillow`` (Python). Use it only against a lab instance,
for example the Home Assistant lab of Inverter Charge Night with its PLENTICORE
emulator (https://github.com/Puma7/InverterChargeNight/tree/main/lab): the
screenshots show the emulator's values, never a real installation.

Side effects on that instance: for each language the server language and the
user's language are switched and the KOSTAL KORE entry is reloaded (so entity
names follow); afterwards both languages are set back.

Output: docs/images/screenshots/<lang>/<name>.png
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
from pathlib import Path
from typing import Any

from PIL import Image
from playwright.async_api import Page, async_playwright

REPO = Path(__file__).resolve().parents[1]
DOMAIN = "kostal_kore"
LOCALES = {"en": "en-US", "de": "de-DE"}
WIDTH = 1280
HELPERS = """
window.__hass = () => document.querySelector('home-assistant').hass;
window.__deep = (sel, root = document) => { const out = [];
  const walk = (r) => { out.push(...r.querySelectorAll(sel)); r.querySelectorAll('*').forEach((e) => e.shadowRoot && walk(e.shadowRoot)); };
  walk(root); return out; };
"""


def save(png: bytes, path: Path) -> None:
    """Store a screenshot as an optimised PNG."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.open(io.BytesIO(png))
    image.save(path, optimize=True)
    print(f"wrote {path.relative_to(REPO) if path.is_relative_to(REPO) else path}")


class Shooter:
    def __init__(self, page: Page, base: str, out: Path) -> None:
        self.page, self.base, self.out = page, base, out
        self._id = 0

    async def ws(self, message: dict[str, Any]) -> Any:
        return await self.page.evaluate("(m) => __hass().callWS(m)", message)

    async def goto(self, path: str, wait_ms: int = 2500) -> None:
        await self.page.goto(self.base + path)
        await self.page.wait_for_function("() => window.__hass && __hass() && __hass().states")
        await self.page.wait_for_timeout(wait_ms)

    async def shot(self, name: str, height: int | None = None) -> None:
        clip = {"x": 0, "y": 0, "width": WIDTH, "height": height or self.page.viewport_size["height"]}
        save(await self.page.screenshot(clip=clip), self.out / f"{name}.png")

    async def set_language(self, lang: str, entry_id: str) -> None:
        await self.ws({"type": "frontend/set_user_data", "key": "language", "value": {
            "language": lang, "number_format": "language", "time_format": "language",
            "date_format": "language", "time_zone": "server", "first_weekday": "language"}})
        await self.page.evaluate("(l) => localStorage.setItem('selectedLanguage', JSON.stringify(l))", lang)
        await self.ws({"type": "config/core/update", "language": lang})
        await self.page.evaluate(
            "(id) => __hass().callApi('POST', `config/config_entries/entry/${id}/reload`)", entry_id)
        await self.page.wait_for_timeout(8000)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:8123")
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--lang", nargs="+", default=["en", "de"], choices=sorted(LOCALES))
    parser.add_argument("--out", type=Path, default=REPO / "docs" / "images" / "screenshots")
    parser.add_argument("--chromium", help="path to a Chromium binary (default: Playwright's own)")
    args = parser.parse_args()
    token = args.token_file.read_text("utf-8").strip() if args.token_file else os.environ["HA_TOKEN"]
    base = args.url.rstrip("/")
    tokens = {"access_token": token, "token_type": "Bearer", "expires_in": 1800, "refresh_token": "",
              "hassUrl": base, "clientId": base + "/", "expires": 4102444800000}

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=args.chromium)
        original: tuple[str, Any, str] | None = None
        try:
            for lang in args.lang:
                context = await browser.new_context(
                    viewport={"width": WIDTH, "height": 900}, device_scale_factor=1.5, color_scheme="light",
                    locale=LOCALES[lang], timezone_id="Europe/Berlin")
                await context.add_init_script(
                    f"localStorage.setItem('hassTokens', {json.dumps(json.dumps(tokens))});"
                    "localStorage.setItem('dockedSidebar', '\"docked\"');"
                    f"localStorage.setItem('selectedLanguage', '\"{lang}\"');" + HELPERS)
                page = await context.new_page()
                shooter = Shooter(page, base, args.out / lang)
                await shooter.goto("/config/integrations/dashboard", 1000)
                entry = next(e for e in await shooter.ws({"type": "config_entries/get", "domain": DOMAIN}))
                if original is None:
                    original = ((await shooter.ws({"type": "get_config"}))["language"],
                                (await shooter.ws({"type": "frontend/get_user_data", "key": "language"}))["value"],
                                entry["entry_id"])
                await shooter.set_language(lang, entry["entry_id"])

                await shooter.goto(f"/config/integrations/integration/{DOMAIN}")
                await shooter.shot("integration", 560)

                devices = [d for d in await shooter.ws({"type": "config/device_registry/list"})
                           if entry["entry_id"] in d["config_entries"]]
                device = max(devices, key=lambda d: d.get("name_by_user") is None and d.get("model") is not None)
                await shooter.goto(f"/config/devices/device/{device['id']}", 4000)
                await shooter.shot("device", 900)

                registry = await shooter.ws({"type": "config/entity_registry/list"})
                limit = next((e["entity_id"] for e in registry if e["platform"] == DOMAIN
                              and e["entity_id"].endswith("battery_max_charge_limit_modbus")), None)
                if limit:
                    await page.evaluate("""(id) => document.querySelector('home-assistant').dispatchEvent(
                        new CustomEvent('hass-more-info', {detail: {entityId: id}, bubbles: true, composed: true}))""", limit)
                    await page.wait_for_timeout(2500)
                    await shooter.shot("battery-limit", 900)
                await context.close()
        finally:
            if original is not None:  # leave the instance as it was found, also after an error
                await restore(browser, base, tokens, args.out, original)
            await browser.close()


async def restore(browser: Any, base: str, tokens: dict[str, Any], out: Path,
                  original: tuple[str, Any, str]) -> None:
    """Set the server and user language back and reload the entry under it."""
    core_language, user_language, entry_id = original
    context = await browser.new_context()
    await context.add_init_script(f"localStorage.setItem('hassTokens', {json.dumps(json.dumps(tokens))});" + HELPERS)
    shooter = Shooter(await context.new_page(), base, out)
    await shooter.goto("/config/integrations/dashboard", 1000)
    await shooter.ws({"type": "frontend/set_user_data", "key": "language", "value": user_language})
    await shooter.ws({"type": "config/core/update", "language": core_language})
    await shooter.page.evaluate(
        "(id) => __hass().callApi('POST', `config/config_entries/entry/${id}/reload`)", entry_id)
    await context.close()

if __name__ == "__main__":
    asyncio.run(main())
