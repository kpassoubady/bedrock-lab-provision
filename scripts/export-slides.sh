#!/bin/bash
# Export all Marp slide decks to PDF
# Usage: ./scripts/export-slides.sh [course-name]
#   course-name: 1-day, 2-day-am-pm, mobile, or all (default: all)

set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SLIDES_DIR="$REPO_ROOT/slides"
EXPORTS_DIR="$REPO_ROOT/exports"
THEME="$REPO_ROOT/assets/theme.css"

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

# Function to check if marp is installed
check_marp() {
    if ! command -v marp &> /dev/null; then
        print_error "Marp CLI is not installed"
        echo ""
        echo "Install Marp CLI:"
        echo "  npm install -g @marp-team/marp-cli"
        echo ""
        echo "Or with Homebrew:"
        echo "  brew install marp-cli"
        exit 1
    fi
    print_success "Marp CLI found: $(marp --version)"
}

# Function to combine multiple markdown files into a single file for export.
# Keeps frontmatter from the first file and strips frontmatter from subsequent files.
combine_markdown_files() {
    local output_file=$1
    shift
    local first=true

    > "$output_file"

    for md_file in "$@"; do
        if [ "$first" = true ]; then
            cat "$md_file" >> "$output_file"
            first=false
        else
            # Strip leading frontmatter block (between first two `---` lines) if present
            awk '
                BEGIN { in_front = 0; found_first = 0 }
                /^---$/ {
                    if (found_first == 0) { found_first = 1; in_front = 1; next }
                    else if (in_front == 1) { in_front = 0; next }
                }
                found_first == 1 && in_front == 0 { print }
            ' "$md_file" >> "$output_file"
        fi

        # Ensure a slide separator between files
        echo "" >> "$output_file"
        echo "---" >> "$output_file"
        echo "" >> "$output_file"
    done
}

# Function to export a single course
export_course() {
    local course_name=$1
    local course_dir="$SLIDES_DIR/slides-$course_name"
    local output_file="$EXPORTS_DIR/slides-$course_name.pdf"

    if [ ! -d "$course_dir" ]; then
        print_warning "Course directory not found: $course_dir"
        return 1
    fi

    # Collect markdown files (excluding READMEs and hidden files), sorted
    local temp_list=$(mktemp)
    find "$course_dir" -name '*.md' -not -name 'README.md' -not -path '*/\.*' | sort > "$temp_list"
    local md_count=$(wc -l < "$temp_list" | tr -d ' ')

    if [ "$md_count" -eq 0 ]; then
        print_warning "No slide files found in $course_dir"
        rm "$temp_list"
        return 1
    fi

    print_info "Exporting $course_name ($md_count files)..."

    # Marp cannot write a single PDF from multiple input files with -o,
    # so combine them into one temporary markdown file inside the course
    # directory so that relative image/asset paths resolve correctly.
    local temp_combined="$course_dir/.export-combined-$$.md"
    cleanup_combined() {
        rm -f "$temp_combined"
    }
    trap cleanup_combined EXIT

    combine_markdown_files "$temp_combined" $(cat "$temp_list")
    rm "$temp_list"

    if [ -f "$THEME" ]; then
        marp --pdf --theme "$THEME" --allow-local-files "$temp_combined" -o "$output_file"
    else
        print_warning "Theme not found at $THEME, using default theme"
        marp --pdf --allow-local-files "$temp_combined" -o "$output_file"
    fi

    rm -f "$temp_combined"
    trap - EXIT

    if [ -f "$output_file" ]; then
        local file_size=$(du -h "$output_file" | cut -f1)
        print_success "Exported: $output_file ($file_size)"
        return 0
    else
        print_error "Failed to export: $course_name"
        return 1
    fi
}

# Function to show usage
show_usage() {
    echo "Usage: $0 [course-name]"
    echo ""
    echo "Export Marp slide decks to PDF"
    echo ""
    echo "Arguments:"
    echo "  course-name    Course to export (default: all)"
    echo "                 Options: 1-day, 2-day-am-pm, mobile, all"
    echo ""
    echo "Examples:"
    echo "  $0              # Export all courses"
    echo "  $0 1-day        # Export only 1-day course"
    echo "  $0 2-day-am-pm  # Export only 2-day AM-PM course"
    echo "  $0 mobile       # Export only mobile course"
}

# Main script
main() {
    local course=${1:-all}
    
    # Show help if requested
    if [ "$course" = "-h" ] || [ "$course" = "--help" ]; then
        show_usage
        exit 0
    fi
    
    echo ""
    print_info "GitHub Copilot Slides Export"
    echo ""
    
    # Check prerequisites
    check_marp
    
    # Create exports directory
    mkdir -p "$EXPORTS_DIR"
    print_success "Exports directory: $EXPORTS_DIR"
    echo ""
    
    # Export courses
    local success_count=0
    local fail_count=0
    
    case "$course" in
        1-day)
            if export_course "1-day"; then
                ((success_count++))
            else
                ((fail_count++))
            fi
            ;;
        2-day-am-pm)
            if export_course "2-day-am-pm"; then
                ((success_count++))
            else
                ((fail_count++))
            fi
            ;;
        mobile)
            if export_course "mobile"; then
                ((success_count++))
            else
                ((fail_count++))
            fi
            ;;
        all)
            print_info "Exporting all courses..."
            echo ""
            
            if export_course "1-day"; then
                ((success_count++))
            else
                ((fail_count++))
            fi
            echo ""
            
            if export_course "2-day-am-pm"; then
                ((success_count++))
            else
                ((fail_count++))
            fi
            echo ""
            
            if export_course "mobile"; then
                ((success_count++))
            else
                ((fail_count++))
            fi
            ;;
        *)
            print_error "Unknown course: $course"
            echo ""
            show_usage
            exit 1
            ;;
    esac
    
    # Summary
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    if [ $success_count -gt 0 ]; then
        print_success "Successfully exported: $success_count course(s)"
    fi
    if [ $fail_count -gt 0 ]; then
        print_warning "Failed to export: $fail_count course(s)"
    fi
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    
    if [ $fail_count -gt 0 ]; then
        exit 1
    fi
}

# Run main function
main "$@"
