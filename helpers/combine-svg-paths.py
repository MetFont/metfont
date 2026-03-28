#!/usr/bin/env python3
"""
Combine overlapping SVG paths using Inkscape's path-union operation.

Run after normalize-svg.py but before generate-fonts.py. This iterates over
all SVG files in color/svg/, uses Inkscape's headless path-union to merge
any overlapping <path> elements into a single geometrically-correct path,
and strips Inkscape's verbose metadata.

Inkscape path-union handles three cases that fill-rule alone cannot:
  1. Overlapping shapes with opposite winding directions (fill-rule="evenodd"
     does NOT fix this — the overlap still becomes a hole in some renderers)
  2. Multiple path elements that nanoemoji might treat as separate contours
  3. Self-intersecting compound paths with inconsistent orientation

Usage:
    python3 helpers/combine-svg-paths.py [--input-dir color/svg/]

Dependencies: Inkscape >= 1.4 (installed system-wide)
"""
import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from lxml import etree

NS = "http://www.w3.org/2000/svg"

# Inkscape adds these namespaces and elements — strip them all from output.
INKSCAPE_NAMESPACES = {
    "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd": "sodipodi",
    "http://inkscape.org/namespaces/inkscape": "inkscape",
}
# Attributes on any element that come from Inkscape/sodipodi.
INKSCAPE_ATTR_RE = re.compile(
    r"^(sodipodi:|inkscape:|inkscape:.*|sodipodi:.*)$"
)
# Elements to remove entirely.
INKSCAPE_ELEM_RE = re.compile(
    r"^(sodipodi:|inkscape:)"
)


def inkscape_available():
    """Return True if inkscape is on PATH and can be invoked."""
    return subprocess.call(
        ["inkscape", "--version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) == 0


def combine_paths_inkscape(svg_bytes: bytes) -> bytes:
    """
    Run Inkscape headless to apply path-union on a single SVG.

    Returns the cleaned SVG bytes (Inkscape metadata removed).
    Raises subprocess.CalledProcessError if Inkscape fails.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        inp = Path(tmpdir) / "in.svg"
        out = Path(tmpdir) / "out.svg"

        inp.write_bytes(svg_bytes)

        result = subprocess.run(
            [
                "inkscape",
                str(inp),
                "--actions=select-all;path-combine",
                "--batch-process",
                "-o",
                str(out),
            ],
            capture_output=True,
            text=True,
            # Inkscape needs a display for some operations even in batch mode;
            # fall back to a dummy display if none is set.
            env={**subprocess.os.environ, "DISPLAY": ""},
        )
        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, result.args, result.stdout, result.stderr
            )

        return out.read_bytes()


def strip_inkscape_metadata(svg_bytes: bytes) -> bytes:
    """
    Remove Inkscape-specific namespaces, elements, and attributes from SVG bytes.
    Keeps standard SVG 1.1 elements intact. Re-applies fill-rule="evenodd" to
    the root SVG element so that combined compound paths render correctly.
    """
    tree = etree.fromstring(svg_bytes)

    # Remove Inkscape namespace declarations from the root element.
    for prefix, uri in list(tree.nsmap.items()):
        if uri in INKSCAPE_NAMESPACES:
            del tree.nsmap[prefix]
            # Also remove the namespace declaration attribute.
            ns_attr = f"{{{uri}}}"
            for attr in list(tree.attrib):
                if attr.startswith(ns_attr):
                    del tree.attrib[attr]

    # Remove sodipodi:namedview (the page border/grid element).
    for elem in list(tree.iter()):
        tag = elem.tag if isinstance(elem.tag, str) else ""
        if INKSCAPE_ELEM_RE.match(tag):
            parent = elem.getparent()
            if parent is not None:
                parent.remove(elem)
            continue
        # Strip Inkscape/sodipodi attributes from all elements.
        for attr in list(elem.attrib):
            if INKSCAPE_ATTR_RE.match(attr):
                del elem.attrib[attr]

    # Re-apply fill-rule="evenodd" to the root <svg> so compound paths
    # (multiple subpaths from path-combine) render as a single filled union.
    tree.set("fill-rule", "evenodd")

    return etree.tostring(
        tree, xml_declaration=True, encoding="UTF-8", pretty_print=False
    )


def process_svg(svg_path: Path) -> bool:
    """
    Process a single SVG file: combine paths via Inkscape, strip metadata.

    Returns True if the file was modified, False if skipped (single path).
    Raises exception on error.
    """
    content = svg_path.read_text(encoding="utf-8")

    # Quick check: only process files with multiple <path> elements.
    if content.count("<path ") < 2:
        return False

    svg_bytes = content.encode("utf-8")
    combined = combine_paths_inkscape(svg_bytes)
    cleaned = strip_inkscape_metadata(combined)
    svg_path.write_bytes(cleaned)
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Combine overlapping SVG paths via Inkscape path-union."
    )
    parser.add_argument(
        "--input-dir",
        default="color/svg",
        help="Directory containing normalized SVG files (default: color/svg/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show which files would be processed without modifying them.",
    )
    args = parser.parse_args()

    if not inkscape_available():
        print("ERROR: inkscape not found on PATH. Install Inkscape >= 1.4:", file=sys.stderr)
        print("  macOS: brew install inkscape", file=sys.stderr)
        print("  Ubuntu: sudo apt install inkscape", file=sys.stderr)
        sys.exit(1)

    svg_dir = Path(args.input_dir)
    if not svg_dir.is_dir():
        print(f"ERROR: {svg_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    svg_files = sorted(svg_dir.glob("*.svg"))
    if not svg_files:
        print(f"No SVG files found in {svg_dir}")
        sys.exit(0)

    processed = 0
    skipped = 0
    failed = 0

    for svg_path in svg_files:
        # Quick check: single-path SVGs don't need combining.
        if svg_path.read_text(encoding="utf-8").count("<path ") < 2:
            skipped += 1
            continue

        if args.dry_run:
            print(f"  [dry-run] would process: {svg_path.name}")
            processed += 1
            continue

        try:
            process_svg(svg_path)
            processed += 1
            print(f"  combined: {svg_path.name}")
        except subprocess.CalledProcessError as e:
            failed += 1
            print(
                f"  FAIL: {svg_path.name}: inkscape error:\n{e.stderr.strip()}",
                file=sys.stderr,
            )
        except Exception as e:
            failed += 1
            print(f"  FAIL: {svg_path.name}: {e}", file=sys.stderr)

    print(
        f"\nDone: {processed} combined, {skipped} single-path skipped, {failed} failed"
    )
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
