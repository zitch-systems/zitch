#!/usr/bin/env python3
"""Embed the Zitch mark into the published WhatsApp Flow JSON.

WhatsApp Flows cannot fetch a remote image: the `Image` component takes base64
bytes inlined in the document, so the logo has to live inside `pin_flow.json`
itself — the file you paste into WhatsApp Manager. That makes the JSON the
artifact, and this script the record of how the bytes in it were produced.

Rendered at 3x the display height so it stays crisp on dense screens, then
written into every screen's layout as the first child.

    python scripts/build-flow-logo.py          # rewrite the JSON in place
    python scripts/build-flow-logo.py --check   # verify it is up to date (CI)
"""
import argparse
import base64
import io
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "images" / "zitch-mark.png"
TARGET = ROOT / "backend" / "whatsapp" / "flow_assets" / "pin_flow.json"

DISPLAY_HEIGHT = 48        # dp, as rendered inside the Flow
RENDER_SCALE = 3           # source pixels per dp, for 3x screens


def logo_base64() -> str:
    from PIL import Image

    image = Image.open(SOURCE).convert("RGBA")
    height = DISPLAY_HEIGHT * RENDER_SCALE
    width = round(image.width * height / image.height)
    buffer = io.BytesIO()
    image.resize((width, height), Image.LANCZOS).save(buffer, "PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode()


def logo_component(src: str) -> dict:
    return {
        "type": "Image",
        "src": src,
        "height": DISPLAY_HEIGHT,
        "scale-type": "contain",
        "alt-text": "Zitch",
    }


def with_logo(document: dict, src: str) -> dict:
    """Put the mark at the top of every screen, replacing any earlier one."""
    for screen in document["screens"]:
        children = screen["layout"]["children"]
        if children and children[0].get("type") == "Image":
            children[0] = logo_component(src)
        else:
            children.insert(0, logo_component(src))
    return document


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if the JSON is not up to date")
    args = parser.parse_args()

    current = TARGET.read_text()
    updated = json.dumps(with_logo(json.loads(current), logo_base64()),
                         indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if current != updated:
            print(f"{TARGET.relative_to(ROOT)} is stale — run scripts/build-flow-logo.py")
            return 1
        print("flow JSON logo is up to date")
        return 0
    TARGET.write_text(updated)
    print(f"wrote {TARGET.relative_to(ROOT)} "
          f"({len(updated) / 1024:.1f} KB, logo at {DISPLAY_HEIGHT}dp)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
