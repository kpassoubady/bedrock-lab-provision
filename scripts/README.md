# Scripts

This folder contains build and export helpers for course slide decks, diagrams, and book assets.

## Overview

| Script | Purpose | Input | Output |
|--------|---------|-------|--------|
| [`build-diagrams.sh`](#build-diagramssh) | Convert `.mmd` Mermaid diagrams to SVG/PNG | `slides/slides-2-day-am-pm/day*/diagrams/*.mmd` | `.svg` and/or `.png` files next to each `.mmd` |
| [`build-slides.sh`](#build-slidessh) | Convert Marp markdown slide decks to PDF | `slides/**/*.md` | `slides/pdf/**/*.pdf` |
| [`export-slides.sh`](#export-slidessh) | Combine and export full course slide decks to PDF | `slides/slides-{1-day,2-day-am-pm,mobile}/` | `exports/slides-*.pdf` |
| [`export-excalidraw.sh`](#export-excalidrawsh) | Export `.excalidraw` files to SVG/PNG | `book/diagrams/*.excalidraw` | `.excalidraw.svg`, `.excalidraw.png` |
| [`generate-excalidraw-diagrams.py`](#generate-excalidraw-diagramspy) | Generate `.excalidraw` source files programmatically | Hard-coded diagram definitions | `slides/slides-2-day-am-pm/day2-session2/diagrams/*.excalidraw` |
| [`add-edge-casing.py`](#add-edge-casingpy) | Add a white halo/casing to Mermaid SVG connectors | `*.svg` (in place) | Same file, modified |
| [`edge-casing.css`](#edge-casingcss) | CSS filter for PNG edge casing | Used by `mmdc` with `-C` | N/A |
| [`print-color-adjust.css`](#print-color-adjustcss) | Force exact color rendering during PDF-to-SVG conversion | Used by `build-diagrams.sh` for the SVG PDF step (`-C` flag) | N/A |

## build-diagrams.sh

Renders Mermaid (`.mmd`) diagrams for course slides using the Mermaid CLI (`mmdc`).

### Dependencies

- [Mermaid CLI](https://github.com/mermaid-js/mermaid-cli): `npm install -g @mermaid-js/mermaid-cli`

### Usage

```bash
./scripts/build-diagrams.sh [day-folder] [--svg|--png|--both]
```

- `day-folder`: `day1-session1`, `day2-session2`, or `all` (default: `all`)
- `--svg`: generate SVG only (default)
- `--png`: generate PNG only
- `--both`: generate both SVG and PNG

Examples:

```bash
./scripts/build-diagrams.sh
./scripts/build-diagrams.sh day1-session1 --png
./scripts/build-diagrams.sh day2-session2 --both
```

### Notes

- Diagrams are read from `slides/slides-2-day-am-pm/<day-folder>/diagrams/`.
- The script uses a transparent background and `neutral` theme by default.
- SVG output may use `print-color-adjust.css` to preserve exact label colors during the PDF-to-SVG step.
- SVG output receives an additional edge casing via `add-edge-casing.py` if needed.

## build-slides.sh

Builds Marp markdown slide decks to PDF, preserving folder structure under `slides/pdf/`.

### Dependencies

- [Marp CLI](https://marp.app/)
- Theme file at `assets/theme.css`

### Usage

```bash
./scripts/build-slides.sh [-p <path>]
```

- `-p <path>`: build all `.md` files found recursively under `<path>` (default: `slides/`)

Example:

```bash
./scripts/build-slides.sh
./scripts/build-slides.sh -p slides/slides-2-day-am-pm
```

## export-slides.sh

Combines multiple Marp slide markdown files into a single course deck and exports a PDF to the `exports/` folder.

### Dependencies

- [Marp CLI](https://marp.app/)
- Theme file at `assets/theme.css`

### Usage

```bash
./scripts/export-slides.sh [course-name]
```

- `course-name`: `1-day`, `2-day-am-pm`, `mobile`, or `all` (default: `all`)

Examples:

```bash
./scripts/export-slides.sh
./scripts/export-slides.sh 2-day-am-pm
```

### Notes

- Markdown files are sorted alphabetically before combining.
- Frontmatter from the first file is kept; subsequent frontmatter blocks are stripped.
- Output is written to `exports/slides-<course-name>.pdf`.

## export-excalidraw.sh

Exports Excalidraw diagrams to SVG and PNG using `excalidraw-render`.

### Dependencies

- [excalidraw-render](https://github.com/excalidraw/excalidraw): `pip install excalidraw-render`

### Usage

```bash
bash scripts/export-excalidraw.sh [--svg-only|--png-only]
```

Examples:

```bash
bash scripts/export-excalidraw.sh
bash scripts/export-excalidraw.sh --svg-only
bash scripts/export-excalidraw.sh --png-only
```

### Notes

- Operates on files in `book/diagrams/`.
- SVG is the primary format for the book.
- `excalidraw-render` produces clean vector lines rather than hand-drawn squiggles.

## generate-excalidraw-diagrams.py

Programmatically creates `.excalidraw` JSON source files for course diagrams through a small Python scene builder.

### Dependencies

- Python 3

### Usage

```bash
python scripts/generate-excalidraw-diagrams.py
```

### Notes

- The output directory is currently hard-coded to a course-specific path. Review `DIAGRAMS_DIR` at the top of the file before running.
- The script defines diagrams per chapter (e.g., `ch01_token_pipeline`, `ch02_model_selection`) and writes them to `.excalidraw` files.

## add-edge-casing.py

Post-processes Mermaid-generated SVGs to add a white halo/casing under connector lines and arrowheads so edges stay readable on both light and dark backgrounds.

### Dependencies

- Python 3

### Usage

```bash
python scripts/add-edge-casing.py [--line-width N] [--arrow-width N] FILE.svg [FILE2.svg ...]
```

Examples:

```bash
python scripts/add-edge-casing.py slides/slides-2-day-am-pm/day1-session1/diagrams/*.svg
python scripts/add-edge-casing.py --line-width 4 --arrow-width 2 diagram.svg
```

### Notes

- Edits files in place.
- Idempotent: skips files that already contain the `<!--edge-casing-->` marker.
- Targets Mermaid's default connector color (`rgb(19.999695%, ...)`).

## edge-casing.css

A CSS filter file used by the Mermaid CLI for PNG output to create a white halo around Mermaid connector lines and arrowheads.

Usage with `mmdc`:

```bash
mmdc -i diagram.mmd -o diagram.png -C scripts/edge-casing.css
```

This provides an equivalent raster halo for PNGs, while `add-edge-casing.py` performs a similar casing for SVGs.

## print-color-adjust.css

A CSS file that forces exact color rendering during Chromium PDF export so explicit white text (e.g. `color:#fff` node labels) is not dimmed to light gray.

`build-diagrams.sh` renders SVGs via a PDF intermediate (`mmdc` -> PDF -> `pdf2svg`). Without exact color adjustment, the final SVG can darken white labels even though the direct PNG render looks correct. This CSS is passed to `mmdc` with the `-C` flag during the SVG PDF step.

Usage with `mmdc`:

```bash
mmdc -i diagram.mmd -o diagram.svg -C scripts/print-color-adjust.css
```

Use this together with `edge-casing.css` when both accurate colors and visible connectors matter.
