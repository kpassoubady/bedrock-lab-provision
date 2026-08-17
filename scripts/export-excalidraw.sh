#!/usr/bin/env bash
# Export all .excalidraw files to SVG (primary) and PNG in book/diagrams/
#
# Uses excalidraw-render (pure Python, zero-Node dependency):
#   pip install excalidraw-render
#
# Usage:
#   bash scripts/export-excalidraw.sh              # Export both SVG + PNG
#   bash scripts/export-excalidraw.sh --svg-only   # Export SVG only
#   bash scripts/export-excalidraw.sh --png-only   # Export PNG only
#
# Note: excalidraw-render produces clean vector lines rather than
#       the hand-drawn squiggly look. For hand-drawn fidelity,
#       use excalirender (https://github.com/JonRC/excalirender).

set -euo pipefail

DIAGRAMS_DIR="$(cd "$(dirname "$0")/../book/diagrams" && pwd)"
SVG_COUNT=0
PNG_COUNT=0
FAILED=0

# Parse flags
EXPORT_SVG=true
EXPORT_PNG=true
if [[ "${1:-}" == "--svg-only" ]]; then
    EXPORT_PNG=false
elif [[ "${1:-}" == "--png-only" ]]; then
    EXPORT_SVG=false
fi

# Check dependency
if ! command -v excalidraw-render &> /dev/null; then
    echo "Error: excalidraw-render not found."
    echo "Install it with:  pip install excalidraw-render"
    exit 1
fi

echo "Exporting Excalidraw diagrams..."
echo "Directory: $DIAGRAMS_DIR"
echo "Formats:   $( $EXPORT_SVG && echo -n 'SVG ' )$( $EXPORT_PNG && echo -n 'PNG' )"
echo ""

for excalidraw_file in "$DIAGRAMS_DIR"/*.excalidraw; do
    [ -f "$excalidraw_file" ] || continue
    base=$(basename "$excalidraw_file")

    # Export SVG (primary format for the book)
    if $EXPORT_SVG; then
        svg_file="${excalidraw_file}.svg"
        if excalidraw-render "$excalidraw_file" -f svg 2>/dev/null; then
            echo "  ✓ $base → ${base}.svg"
            SVG_COUNT=$((SVG_COUNT + 1))
        else
            echo "  ✗ $base (SVG export failed)"
            FAILED=$((FAILED + 1))
        fi
    fi

    # Export PNG
    if $EXPORT_PNG; then
        png_file="${excalidraw_file}.png"
        if excalidraw-render "$excalidraw_file" 2>/dev/null; then
            echo "  ✓ $base → ${base}.png"
            PNG_COUNT=$((PNG_COUNT + 1))
        else
            echo "  ✗ $base (PNG export failed)"
            FAILED=$((FAILED + 1))
        fi
    fi
done

echo ""
echo "Done. SVG: $SVG_COUNT, PNG: $PNG_COUNT, Failed: $FAILED"
