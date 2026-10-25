#!/usr/bin/env python3
"""Build the README banner in every language from one template.

    python scripts/make_banner.py [--chromium PATH]

Writes docs/images/banner.<lang>.png. Needs ``playwright`` and ``pillow``.
The banner shows a crop of docs/images/screenshots/<lang>/device.png, so run
scripts/docs_screenshots.py first when the screenshots change.

The banner is 1280 x 640, the size GitHub uses for a repository's social
preview, so the PNG can be uploaded there as well. To add a language, add an
entry to TEXT and take its screenshots.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "images"
# Controls and the first sensors of the device page, in screenshot pixels.
SCREENSHOT_CROP = (905, 205, 1400, 792)
SCREENSHOT_BOX = (960, 252, 236, 280)  # x, y, width, height in the banner
WIDTH, HEIGHT = 1280, 640

TEXT: dict[str, dict[str, object]] = {
    "en": {
        "subtitle": "Home Assistant integration for KOSTAL PLENTICORE inverters",
        "inverter": "PLENTICORE",
        "inverter_sub": "inverter",
        "link": ["REST API", "Modbus TCP"],
        "link_sub": "local network",
        "features": [
            ("Live values and Energy dashboard", "power, energy, battery, grid"),
            ("Battery limits over Modbus", "optional, confirmed by read-back"),
            ("Diagnostics", "health checks and estimates"),
            ("Modbus proxy and MQTT", "share the connection with evcc"),
        ],
        "footer": "Independent community project · not affiliated with KOSTAL",
    },
    "de": {
        "subtitle": "Home-Assistant-Integration für KOSTAL-PLENTICORE-Wechselrichter",
        "inverter": "PLENTICORE",
        "inverter_sub": "Wechselrichter",
        "link": ["REST-API", "Modbus TCP"],
        "link_sub": "lokales Netzwerk",
        "features": [
            ("Live-Werte und Energie-Dashboard", "Leistung, Energie, Batterie, Netz"),
            ("Batteriegrenzen per Modbus", "optional, per Rücklesen bestätigt"),
            ("Diagnose", "Zustandsprüfungen und Schätzungen"),
            ("Modbus-Proxy und MQTT", "Verbindung mit evcc teilen"),
        ],
        "footer": "Unabhängiges Community-Projekt · nicht mit KOSTAL verbunden",
    },
}

FONT = "Inter, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"

# 32 x 32 icons, drawn around (0, 0) = top left, white strokes.
ICONS = [
    # bolt
    '<path d="M18 2 L6 18 H15 L13 30 L26 13 H17 Z" fill="#FFB300"/>',
    # battery
    '<rect x="3" y="8" width="24" height="16" rx="3" fill="none" stroke="#FFFFFF" stroke-width="3"/>'
    '<rect x="27" y="13" width="3" height="6" rx="1" fill="#FFFFFF"/>'
    '<rect x="7" y="12" width="11" height="8" rx="1" fill="#66BB6A"/>',
    # pulse line
    '<polyline points="2,17 9,17 12,8 17,26 21,13 24,17 30,17" fill="none" stroke="#FFFFFF"'
    ' stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>',
    # share: three nodes
    '<g stroke="#FFFFFF" stroke-width="3"><line x1="8" y1="16" x2="24" y2="7"/>'
    '<line x1="8" y1="16" x2="24" y2="25"/></g>'
    '<circle cx="8" cy="16" r="5" fill="#FFFFFF"/><circle cx="24" cy="7" r="5" fill="#FFFFFF"/>'
    '<circle cx="24" cy="25" r="5" fill="#FFFFFF"/>',
]

HEX = (
    '<path d="M128 12 L228.5 70 L228.5 186 L128 244 L27.5 186 L27.5 70 Z" fill="#FFFFFF" opacity="0.12"/>'
    '<path d="M128 12 L228.5 70 L228.5 186 L128 244 L27.5 186 L27.5 70 Z" fill="none" stroke="#FFFFFF"'
    ' stroke-width="8" opacity="0.9"/>'
    '<g stroke="#FFFFFF" stroke-width="10" stroke-linecap="round"><line x1="128" y1="128" x2="128" y2="62"/>'
    '<line x1="128" y1="128" x2="71" y2="161"/><line x1="128" y1="128" x2="185" y2="161"/></g>'
    '<circle cx="128" cy="62" r="22" fill="#FFB300"/>'
    '<rect x="52" y="146" width="38" height="30" rx="6" fill="#FFFFFF"/>'
    '<rect x="90" y="155" width="5" height="12" rx="1.5" fill="#FFFFFF"/>'
    '<path d="M163 163 L185 143 L207 163 Z" fill="#FFFFFF"/>'
    '<rect x="170" y="160" width="30" height="22" rx="3" fill="#FFFFFF"/>'
    '<circle cx="128" cy="128" r="16" fill="#FFFFFF"/><circle cx="128" cy="128" r="7" fill="#1565C0"/>'
)


def text(x: float, y: float, value: str, size: int, *, weight: int = 400, fill: str = "#FFFFFF",
         anchor: str = "start", opacity: float = 1.0) -> str:
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" font-weight="{weight}"'
            f' fill="{fill}" text-anchor="{anchor}" opacity="{opacity}">{escape(value)}</text>')


def banner(lang: str) -> str:
    t = TEXT[lang]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" height="{HEIGHT}">',
        f"<title>KOSTAL KORE – {escape(str(t['subtitle']))}</title>",
        '<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#0B3D7A"/><stop offset="1" stop-color="#1565C0"/></linearGradient>'
        '<marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">'
        '<path d="M0 0 L10 5 L0 10 Z" fill="#FFFFFF"/></marker></defs>',
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="url(#bg)"/>',
        # header
        f'<g transform="translate(64 52) scale(0.62)">{HEX}</g>',
        text(232, 132, "KOSTAL KORE", 72, weight=700),
        text(234, 182, str(t["subtitle"]), 28, opacity=0.85),
    ]

    # inverter card
    parts += [
        '<rect x="64" y="236" width="300" height="312" rx="20" fill="#FFFFFF" opacity="0.10"/>',
        '<rect x="139" y="300" width="150" height="120" rx="12" fill="none" stroke="#FFFFFF" stroke-width="4"/>',
        '<path d="M190 330 L178 362 H196 L186 392 L214 354 H196 L206 330 Z" fill="#FFB300"/>',
        '<rect x="155" y="404" width="118" height="5" rx="2.5" fill="#FFFFFF" opacity="0.5"/>',
        text(214, 470, str(t["inverter"]), 30, weight=700, anchor="middle"),
        text(214, 504, str(t["inverter_sub"]), 22, anchor="middle", opacity=0.8),
    ]

    # connection
    link = list(t["link"])  # type: ignore[call-overload]
    parts += [
        '<line x1="384" y1="400" x2="500" y2="400" stroke="#FFFFFF" stroke-width="4" marker-end="url(#arrow)"/>',
        '<line x1="500" y1="424" x2="384" y2="424" stroke="#FFFFFF" stroke-width="4" opacity="0.6"'
        ' marker-end="url(#arrow)"/>',
        text(442, 352, link[0], 19, weight=600, anchor="middle"),
        text(442, 376, link[1], 19, weight=600, anchor="middle"),
        text(442, 462, str(t["link_sub"]), 17, anchor="middle", opacity=0.75),
    ]

    # features card
    parts += [
        '<rect x="520" y="236" width="696" height="312" rx="20" fill="#FFFFFF" opacity="0.10"/>',
        text(552, 280, "Home Assistant", 22, weight=700),
    ]
    for index, (title, sub) in enumerate(t["features"]):  # type: ignore[arg-type]
        y = 302 + index * 60
        parts += [
            f'<g transform="translate(552 {y + 6}) scale(0.85)">{ICONS[index]}</g>',
            text(596, y + 20, title, 20, weight=600),
            text(596, y + 44, sub, 16, opacity=0.75),
        ]

    parts.append(screenshot(lang))
    parts += [text(WIDTH / 2, 598, str(t["footer"]), 18, anchor="middle", opacity=0.65), "</svg>"]
    return "\n".join(parts) + "\n"


def screenshot(lang: str) -> str:
    """Return the cropped device screenshot as an embedded image."""
    from PIL import Image

    image = Image.open(OUT / "screenshots" / lang / "device.png").convert("RGB").crop(SCREENSHOT_CROP)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    data = base64.b64encode(buffer.getvalue()).decode()
    x, y, w, h = SCREENSHOT_BOX
    return (
        f'<clipPath id="shot"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12"/></clipPath>'
        f'<image x="{x}" y="{y}" width="{w}" height="{h}" preserveAspectRatio="xMidYMin slice"'
        f' clip-path="url(#shot)" href="data:image/png;base64,{data}"/>'
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="none" stroke="#FFFFFF"'
        ' stroke-width="2" opacity="0.5"/>'
    )


async def render(svg: str, png: Path, chromium: str | None) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=chromium)
        page = await browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=1)
        await page.set_content(f'<html><body style="margin:0">{svg}</body></html>')
        await page.screenshot(path=str(png), clip={"x": 0, "y": 0, "width": WIDTH, "height": HEIGHT})
        await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chromium", help="path to a Chromium binary (default: Playwright's own)")
    args = parser.parse_args()
    for lang in TEXT:
        png = OUT / f"banner.{lang}.png"
        asyncio.run(render(banner(lang), png, args.chromium))
        print(f"wrote {png}")


if __name__ == "__main__":
    main()
