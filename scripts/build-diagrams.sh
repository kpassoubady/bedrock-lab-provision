#!/bin/bash
# Build Mermaid diagrams to SVG and/or PNG for course slides
# Usage: ./scripts/build-diagrams.sh [day-folder] [--svg|--png|--both]
#   day-folder: day1-session1, day2-session2, or all (default: all)
#   --svg       Generate SVG only (default)
#   --png       Generate PNG only
#   --both      Generate both SVG and PNG

set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SLIDES_DIR="$REPO_ROOT/slides/slides-2-day-am-pm"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

# Function to check if mmdc is installed
check_mmdc() {
    if ! command -v mmdc &> /dev/null; then
        print_error "Mermaid CLI (mmdc) is not installed"
        echo ""
        echo "Install Mermaid CLI:"
        echo "  npm install -g @mermaid-js/mermaid-cli"
        echo ""
        exit 1
    fi
    print_success "Mermaid CLI found: $(mmdc --version | head -1)"
}

# Function to build diagrams for a day
build_day() {
    local day_folder=$1
    local format=$2
    local diagrams_dir="$SLIDES_DIR/$day_folder/diagrams"
    
    if [ ! -d "$diagrams_dir" ]; then
        print_warning "Diagrams directory not found: $diagrams_dir"
        return 1
    fi
    
    # Count .mmd files
    local mmd_count=$(find "$diagrams_dir" -name '*.mmd' | wc -l | tr -d ' ')
    
    if [ "$mmd_count" -eq 0 ]; then
        print_warning "No .mmd files found in $diagrams_dir"
        return 1
    fi
    
    print_info "Building $mmd_count diagram(s) for $day_folder ($format)..."
    
    local success_count=0
    local fail_count=0
    
    # Build each diagram
    for mmd_file in "$diagrams_dir"/*.mmd; do
        local filename=$(basename "$mmd_file" .mmd)
        local outputs=""
        local ok=true

        if [ "$format" = "svg" ] || [ "$format" = "both" ]; then
            local svg_file="$diagrams_dir/$filename.svg"
            if mmdc -i "$mmd_file" -o "$svg_file" -t neutral -b transparent 2>/dev/null; then
                outputs="$filename.svg"
            else
                print_error "  Failed (SVG): $filename.mmd"
                ok=false
            fi
        fi

        if [ "$format" = "png" ] || [ "$format" = "both" ]; then
            local png_file="$diagrams_dir/$filename.png"
            if mmdc -i "$mmd_file" -o "$png_file" -t neutral -b transparent --scale 3 2>/dev/null; then
                [ -n "$outputs" ] && outputs="$outputs + $filename.png" || outputs="$filename.png"
            else
                print_error "  Failed (PNG): $filename.mmd"
                ok=false
            fi
        fi

        if $ok && [ -n "$outputs" ]; then
            print_success "  $filename.mmd → $outputs"
            ((success_count++))
        elif ! $ok; then
            ((fail_count++))
        fi
    done
    
    echo ""
    if [ $success_count -gt 0 ]; then
        print_success "$day_folder: $success_count diagram(s) built"
    fi
    if [ $fail_count -gt 0 ]; then
        print_warning "$day_folder: $fail_count diagram(s) failed"
    fi
    
    return 0
}

# Function to show usage
show_usage() {
    echo "Usage: $0 [day-folder] [--svg|--png|--both]"
    echo ""
    echo "Build Mermaid diagrams to SVG and/or PNG for course slides"
    echo ""
    echo "Arguments:"
    echo "  day-folder    Day folder to build (default: all)"
    echo "                Options: day1-session1, day2-session2, all"
    echo "  --svg         Generate SVG only (default)"
    echo "  --png         Generate PNG only"
    echo "  --both        Generate both SVG and PNG"
    echo ""
    echo "Examples:"
    echo "  $0                         # Build all diagrams as SVG"
    echo "  $0 --both                  # Build all diagrams as SVG and PNG"
    echo "  $0 day1-session1           # Build only day1-session1 as SVG"
    echo "  $0 day1-session1 --png     # Build only day1-session1 as PNG"
    echo "  $0 day2-session2 --both    # Build day2-session2 as SVG and PNG"
}

# Main script
main() {
    local day="all"
    local format="svg"

    # Parse arguments
    for arg in "$@"; do
        case "$arg" in
            --svg)  format="svg" ;;
            --png)  format="png" ;;
            --both) format="both" ;;
            -h|--help)
                show_usage
                exit 0
                ;;
            *) day="$arg" ;;
        esac
    done

    echo ""
    print_info "GitHub Copilot Diagrams Build"
    echo ""
    
    # Check prerequisites
    check_mmdc
    echo ""
    
    # Build diagrams
    case "$day" in
        day1-session1)
            build_day "day1-session1" "$format"
            ;;
        day2-session2)
            build_day "day2-session2" "$format"
            ;;
        all)
            print_info "Building all diagrams..."
            echo ""
            build_day "day1-session1" "$format"
            echo ""
            build_day "day2-session2" "$format"
            ;;
        *)
            print_error "Unknown day folder: $day"
            echo ""
            show_usage
            exit 1
            ;;
    esac
    
    echo ""
    print_success "Diagram build complete!"
    echo ""
}

# Run main function
main "$@"
