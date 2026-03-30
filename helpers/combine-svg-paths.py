#!/usr/bin/env python3
"""
Combine overlapping SVG paths using Inkscape's path-combining operations.

Run BEFORE normalize-svg.py so that Inkscape can convert strokes to filled
outlines on the source SVGs (which still have stroke attributes). This script
iterates over SVG files in src/, uses Inkscape headless to:
  1. object-stroke-to-path — convert strokes to filled outlines
  2. object-to-path        — convert <circle>, <line>, <rect> primitives to <path>
  3. path-union            — boolean union of overlapping shapes into one path
  4. path-combine          — merge all remaining paths into a single compound path

Then strips Inkscape's verbose metadata.

Inkscape path-union + path-combine handles three cases that fill-rule alone cannot:
  1. Overlapping shapes with opposite winding directions (fill-rule="evenodd"
     does NOT fix this — the overlap still becomes a hole in some renderers)
  2. Multiple path elements that nanoemoji might treat as separate contours
  3. Self-intersecting compound paths with inconsistent orientation

Usage:
    python3 helpers/combine-svg-paths.py [--input-dir src/]

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


def combine_paths_inkscape(svg_bytes: bytes, max_iterations: int = 10) -> bytes:
    """
    Run Inkscape headless to convert all shapes to paths and combine them.

    Iterative per-group approach:
      Phase 1: Convert primitives and strokes, flatten groups iteratively
        - object-to-path          : convert <circle>, <line>, <rect>, <text> to <path>
        - object-stroke-to-path   : convert stroke attributes to filled outlines (creates sub-groups)
        - SelectionUnGroup:repeat : flatten those sub-groups back to parent level
        Repeat Phase 1 until no new sub-groups are created (everything at root level)

      Phase 2: Union all root-level filled paths
        - SelectionUnion          : merge all paths into one compound path

      Phase 3: Cleanup and export
        - CleanEdges              : remove jagged edges from union
        - FitCanvasToSelection   : resize canvas to content
        - transform-scale:1       : normalize any scale transforms
        - export-*               : export as plain SVG at 96dpi

    Returns the cleaned SVG bytes (Inkscape metadata removed).
    Raises subprocess.CalledProcessError if Inkscape fails.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        inp = Path(tmpdir) / "in.svg"
        out = Path(tmpdir) / "out.svg"

        inp.write_bytes(svg_bytes)

        # Phase 1: iterative flatten + convert
        # Keep running until no new groups are created (everything at root level)
        for _ in range(max_iterations):
            result = subprocess.run(
                [
                    "inkscape",
                    str(inp),
                    "--actions=select-all:all;object-to-path;"
                    "select-all:all;object-stroke-to-path;"
                    "select-all:all;SelectionUnGroup:repeat",
                    "--batch-process",
                    "-o",
                    str(inp),  # overwrite in-place
                ],
                capture_output=True,
                text=True,
                env={**subprocess.os.environ, "DISPLAY": ""},
            )
            if result.returncode != 0:
                raise subprocess.CalledProcessError(
                    result.returncode, result.args, result.stdout, result.stderr
                )

            # Check if inp still has groups (if no groups remain, phase 1 is done)
            tree = etree.parse(str(inp))
            groups = list(tree.iter("{http://www.w3.org/2000/svg}g"))
            if not groups:
                break

        # Phase 2: union all root-level paths
        result = subprocess.run(
            [
                "inkscape",
                str(inp),
                "--actions=select-all:all;SelectionUnion",
                "--batch-process",
                "-o",
                str(inp),
            ],
            capture_output=True,
            text=True,
            env={**subprocess.os.environ, "DISPLAY": ""},
        )
        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, result.args, result.stdout, result.stderr
            )

        # Phase 3: cleanup and export
        result = subprocess.run(
            [
                "inkscape",
                str(inp),
                "--actions=select-all:all;org.inkscape.effect.filter.CleanEdges;"
                "FitCanvasToSelection;select-all:all;transform-scale:1;"
                "export-plain-svg;export-dpi:96;export-area-drawing;export-area;"
                "export-filename:" + str(out) + ";export-overwrite;export-do",
                "--batch-process",
            ],
            capture_output=True,
            text=True,
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

    Returns True if the file was modified, False if skipped (single drawable).
    Raises exception on error.
    """
    content = svg_path.read_text(encoding="utf-8")

    # Count drawable elements (paths, circles, lines, ellipses, rects, polylines)
    # regardless of namespace prefix (svg: or bare).
    drawables = 0
    for tag in ("path", "circle", "line", "ellipse", "rect", "polyline", "polygon"):
        drawables += content.count(f"<{tag} ")
        drawables += content.count(f"<svg:{tag} ")
    if drawables < 2:
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
        default="src",
        help="Directory containing source SVG files with strokes (default: src/)",
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

    svg_files = sorted(svg_dir.rglob("*.svg"))
    if not svg_files:
        print(f"No SVG files found in {svg_dir}")
        sys.exit(0)

    processed = 0
    skipped = 0
    failed = 0

    for svg_path in svg_files:
        # Quick check: only process files with multiple drawable elements.
        content = svg_path.read_text(encoding="utf-8")
        drawables = 0
        for tag in ("path", "circle", "line", "ellipse", "rect", "polyline", "polygon"):
            drawables += content.count(f"<{tag} ")
            drawables += content.count(f"<svg:{tag} ")
        if drawables < 2:
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
