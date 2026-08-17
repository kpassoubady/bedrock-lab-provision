#!/bin/bash
# Build Marp slide decks to PDF
# Usage: ./scripts/build-slides.sh [-p <path>]
#   -p <path>   Build all slide decks found recursively under <path>
#               (defaults to <repo>/slides)

set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
THEME="$REPO_ROOT/assets/theme.css"
SLIDE_DIR="$REPO_ROOT/slides"

while getopts "p:" opt; do
  case "$opt" in
    p) SLIDE_DIR="$OPTARG" ;;
    *) echo "Usage: $0 [-p <path>]" >&2; exit 1 ;;
  esac
done

if [ ! -d "$SLIDE_DIR" ]; then
  echo "No slides directory found at $SLIDE_DIR"
  exit 1
fi

SLIDE_DIR="$(cd "$SLIDE_DIR" && pwd)"
PDF_DIR="$SLIDE_DIR/pdf"

md_count=$(find "$SLIDE_DIR" -name '*.md' | wc -l | tr -d ' ')
if [ "$md_count" -eq 0 ]; then
  echo "No .md files found under $SLIDE_DIR"
  exit 1
fi

mkdir -p "$PDF_DIR"
echo "Building slides → $PDF_DIR/"

find "$SLIDE_DIR" -name '*.md' -print0 | while IFS= read -r -d '' md_file; do
  rel_path="${md_file#"$SLIDE_DIR"/}"
  rel_dir=$(dirname "$rel_path")
  filename=$(basename "$md_file" .md)

  if [ "$rel_dir" = "." ]; then
    out_dir="$PDF_DIR"
    out_label="$filename.pdf"
  else
    out_dir="$PDF_DIR/$rel_dir"
    out_label="$rel_dir/$filename.pdf"
  fi
  mkdir -p "$out_dir"

  echo "  $rel_path → $out_label"
  marp --pdf --theme "$THEME" --allow-local-files "$md_file" -o "$out_dir/$filename.pdf" < /dev/null
done

echo "Done."
