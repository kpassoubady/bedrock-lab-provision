#!/usr/bin/env python3
"""Add a white casing (halo) under Mermaid connector lines and arrowheads in
pdf2svg-generated SVGs so edges stay visible on both light (day) and dark
(night) backgrounds.

Mermaid's default connector color is rgb(20%,20%,20%) (#333). pdf2svg emits:
  - line edges    : <path fill="none" ... stroke="rgb(19.999695%, ...)" .../>
  - arrowheads    : <path ... fill="rgb(19.999695%, ...)" .../>  (no stroke)

For each connector element we insert a wider white-stroked duplicate *beneath*
it (painted first), so the black core shows on white pages and the white halo
shows on dark pages.

Usage: add-edge-casing.py FILE.svg [FILE2.svg ...]
       add-edge-casing.py --line-width N --arrow-width N FILE.svg
Edits files in place. Idempotent (skips files already cased).
"""
import re
import sys

CONNECTOR = "rgb(19.999695%, 19.999695%, 19.999695%)"
WHITE = "rgb(100%, 100%, 100%)"
MARKER = "<!--edge-casing-->"

LINE_WIDTH = 4.0   # white stroke width for line edges (orig is 1)
ARROW_WIDTH = 2.0  # white stroke width added around arrowhead triangles


def case_line(tag: str) -> str:
    """White, wider duplicate of a fill=none connector line."""
    t = tag.replace(f'stroke="{CONNECTOR}"', f'stroke="{WHITE}"')
    t = re.sub(r'stroke-width="[^"]*"', f'stroke-width="{LINE_WIDTH}"', t)
    if "stroke-linecap" not in t:
        t = t.replace("<path", '<path stroke-linecap="round"', 1)
    else:
        t = re.sub(r'stroke-linecap="[^"]*"', 'stroke-linecap="round"', t)
    t = re.sub(r'stroke-linejoin="[^"]*"', 'stroke-linejoin="round"', t)
    return t


def case_arrow(tag: str) -> str:
    """White, fattened duplicate of a filled arrowhead triangle."""
    t = tag.replace(f'fill="{CONNECTOR}"', f'fill="{WHITE}"')
    # add a white stroke so the triangle grows outward into a halo
    inject = (f' stroke="{WHITE}" stroke-width="{ARROW_WIDTH}" '
              'stroke-linejoin="round"')
    t = t.replace("<path", "<path" + inject, 1)
    return t


def process(text: str) -> str:
    if MARKER in text:
        return text  # already cased
    casings = []
    first_pos = None
    for m in re.finditer(r"<path\b[^>]*/>", text):
        tag = m.group(0)
        is_line = 'fill="none"' in tag and f'stroke="{CONNECTOR}"' in tag
        is_arrow = f'fill="{CONNECTOR}"' in tag and 'fill="none"' not in tag
        if not (is_line or is_arrow):
            continue
        if first_pos is None:
            first_pos = m.start()
        casings.append(case_line(tag) if is_line else case_arrow(tag))
    if not casings:
        return text
    block = MARKER + "\n" + "\n".join(casings) + "\n"
    return text[:first_pos] + block + text[first_pos:]


def main(argv):
    args = argv[1:]
    files = []
    i = 0
    global LINE_WIDTH, ARROW_WIDTH
    while i < len(args):
        a = args[i]
        if a == "--line-width":
            LINE_WIDTH = float(args[i + 1]); i += 2
        elif a == "--arrow-width":
            ARROW_WIDTH = float(args[i + 1]); i += 2
        else:
            files.append(a); i += 1
    if not files:
        print("usage: add-edge-casing.py [--line-width N] [--arrow-width N] FILE.svg ...")
        return 1
    for f in files:
        with open(f, encoding="utf-8") as fh:
            text = fh.read()
        out = process(text)
        if out == text:
            print(f"  skip (cased or no edges): {f}")
            continue
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(out)
        print(f"  cased: {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
