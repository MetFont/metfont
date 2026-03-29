#!/usr/bin/env python3
"""
Clean upstream WMO SVGs from vendor/WorldWeatherSymbols/symbols/ and write
to src/ for font compilation.

Fixes applied:
1. Strip Dublin Core / RDF / CC metadata namespaces
2. Remove stray elements (coordinates far outside viewBox)
3. Convert <text> elements to <path> using fontTools
4. Remove <marker> and <defs> elements (Phase 1: strip only)
5. Fix CSS unit values (pt, px → bare numbers)
6. Remove empty <g> elements
7. Remove id attributes

Usage:
    python3 helpers/clean-sources.py
"""

import argparse
import json
import re
import sys
from io import BytesIO
from pathlib import Path

from lxml import etree

NS = "http://www.w3.org/2000/svg"
NS_XLINK = "http://www.w3.org/1999/xlink"

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
VENDOR_DIR = PROJECT_DIR / "vendor" / "WorldWeatherSymbols"
SOURCE_MAP_PATH = PROJECT_DIR / "data" / "codepoint-map.json"
DEFAULT_INPUT = VENDOR_DIR / "symbols"
DEFAULT_OUTPUT = PROJECT_DIR / "src"

_NUM_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)")
_UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)(px|pt)", re.IGNORECASE)
_font_cache = None


def load_source_map(path):
    """Load source-map.json: symbols/relative/path → codepoint."""
    with open(path) as f:
        return json.load(f)


def strip_metadata(root):
    """Remove Dublin Core, RDF, CC, and other non-SVG namespace elements."""
    ns_to_remove = []
    for prefix, uri in root.nsmap.items():
        if uri == NS:
            continue
        if prefix is not None:
            ns_to_remove.append((prefix, uri))

    for elem in list(root.iter()):
        tag = elem.tag
        if not isinstance(tag, str):
            continue
        for _prefix, uri in ns_to_remove:
            if tag.startswith(f"{{{uri}}}"):
                parent = elem.getparent()
                if parent is not None:
                    parent.remove(elem)
                break

    # Remove namespaced attributes
    for elem in root.iter():
        attrs_to_remove = [k for k in elem.attrib if "}" in k]
        for attr in attrs_to_remove:
            del elem.attrib[attr]


def fix_css_units(root):
    """Fix CSS unit values (pt, px) in element attributes."""
    for elem in root.iter():
        for attr_name in list(elem.attrib):
            val = elem.attrib[attr_name]
            if isinstance(val, str):
                fixed = _UNIT_RE.sub(r"\1", val)
                if fixed != val:
                    elem.attrib[attr_name] = fixed


def remove_markers_and_defs(root):
    """Remove <marker> and <defs> elements (Phase 1: strip only)."""
    for elem in list(root.iter()):
        if elem.tag in ("marker", "defs"):
            parent = elem.getparent()
            if parent is not None:
                parent.remove(elem)


def remove_stray_elements(root):
    """Remove path elements with coordinates far outside the viewBox.

    Stray elements are paths whose bounding box extends far beyond the SVG
    viewBox, making the symbol appear tiny when normalized. We remove the
    stray subpaths from multi-path elements.
    """
    vb = root.get("viewBox")
    if not vb:
        return
    parts = vb.strip().replace(",", " ").split()
    if len(parts) != 4:
        return
    vx, vy, vw, vh = float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])

    # Tolerance: any coordinate more than 2x the viewBox width/height away
    # from the viewBox boundary is considered stray
    margin_x = vw * 2.0
    margin_y = vh * 2.0

    for path_elem in list(root.iter(f"{{{NS}}}path")):
        d = path_elem.get("d", "")
        if not d:
            continue

        # Check if any coordinate is far outside viewBox
        nums = [float(n) for n in _NUM_RE.findall(d)]
        if not nums:
            continue
        xs = nums[0::2]
        ys = nums[1::2]

        is_stray = (
            min(xs) < vx - margin_x
            or max(xs) > vx + vw + margin_x
            or min(ys) < vy - margin_y
            or max(ys) > vy + vh + margin_y
        )

        if is_stray:
            parent = path_elem.getparent()
            if parent is not None:
                parent.remove(path_elem)


def convert_text_to_path(root):
    """Convert <text> elements to <path> using fontTools.

    Uses fontTools RecordingPen to capture glyph outlines from system fonts,
    then converts to SVG path data.
    """
    try:
        from fontTools.ttLib import TTFont
        from fontTools.pens.recordingPen import RecordingPen
        from fontTools.pens.transformPen import TransformPen
    except ImportError:
        print(
            "  WARNING: fontTools not available, skipping text-to-path conversion",
            file=sys.stderr,
        )
        return

    global _font_cache
    if _font_cache is None:
        _font_cache = _load_system_font()
    font = _font_cache

    for text_elem in list(root.iter(f"{{{NS}}}text")):
        # Get text content
        text_content = ""
        tspan = text_elem.find(f"{{{NS}}}tspan")
        if tspan is not None and tspan.text:
            text_content = tspan.text
        elif text_elem.text:
            text_content = text_elem.text

        if not text_content.strip():
            parent = text_elem.getparent()
            if parent is not None:
                parent.remove(text_elem)
            continue

        # Get position
        try:
            x = float(text_elem.get("x", 0))
            y = float(text_elem.get("y", 0))
        except ValueError:
            parent = text_elem.getparent()
            if parent is not None:
                parent.remove(text_elem)
            continue

        # Get font size for scaling (check attribute first, then style)
        font_size = 1.0
        try:
            fs = text_elem.get("font-size") or (
                tspan.get("font-size") if tspan is not None else None
            )
            if not fs:
                # Parse from style attribute (e.g. "font-size:20px;...")
                style = text_elem.get("style", "") or (
                    tspan.get("style", "") if tspan is not None else ""
                )
                m = re.search(r"font-size\s*:\s*([\d.]+)", style)
                if m:
                    fs = m.group(1)
            if fs:
                font_size = float(fs)
        except (ValueError, TypeError):
            pass

        # Get glyph outlines
        pen = RecordingPen()
        try:
            glyph_set = font.getGlyphSet()
            scale_factor = font_size / font["head"].unitsPerEm

            total_width = 0
            for char in text_content:
                if char == " ":
                    total_width += font_size * 0.3
                    continue
                glyph_name = font["cmap"].getBestCmap().get(ord(char))
                if glyph_name is None:
                    continue
                tpen = TransformPen(
                    pen,
                    (scale_factor, 0, 0, -scale_factor, x + total_width, y),
                )
                glyph_set[glyph_name].draw(tpen)
                width = font["hmtx"][glyph_name][0]
                total_width += width * scale_factor

            if not pen.value:
                parent = text_elem.getparent()
                if parent is not None:
                    parent.remove(text_elem)
                continue

            # Convert recording to SVG path data
            path_data = _recording_to_svg_path(pen.value)

            # Replace <text> with <path>
            path_elem = etree.Element(f"{{{NS}}}path")
            path_elem.set("d", path_data)
            # Inherit fill from parent or default
            path_elem.set("fill", text_elem.get("fill", "#000000"))

            parent = text_elem.getparent()
            if parent is not None:
                parent.replace(text_elem, path_elem)

        except Exception as e:
            print(
                f"  WARNING: text-to-path failed for '{text_content}': {e}",
                file=sys.stderr,
            )


def _load_system_font():
    """Try to load a system font for text-to-path conversion."""
    from fontTools.ttLib import TTFont
    import os

    # Common font paths (prefer .ttf/.otf over .ttc to avoid warnings)
    font_paths = [
        # macOS - single fonts first
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial.ttf",
        # macOS - collection fonts (may produce warnings)
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFCompact.ttf",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]

    for fp in font_paths:
        if os.path.exists(fp):
            try:
                return TTFont(fp, fontNumber=0)
            except Exception:
                try:
                    return TTFont(fp)
                except Exception:
                    continue

    # Try to find any TTF/OTF font
    for search_dir in [
        "/System/Library/Fonts",
        "/Library/Fonts",
        "/usr/share/fonts/truetype/dejavu",
    ]:
        if not os.path.isdir(search_dir):
            continue
        for fn in os.listdir(search_dir):
            if fn.endswith((".ttf", ".otf")):
                try:
                    return TTFont(os.path.join(search_dir, fn))
                except Exception:
                    continue

    raise RuntimeError("No system font found for text-to-path conversion")


def _recording_to_svg_path(ops):
    """Convert fontTools RecordingPen operations to SVG path data.

    RecordingPen operations use point tuples as args:
      moveTo((x, y))
      lineTo((x, y))
      qCurveTo((cx, cy), ..., (x, y))  -- last point is on-curve
      curveTo((c1x, c1y), (c2x, c2y), (x, y))
      closePath()
      endPath()
    """
    parts = []
    current_x, current_y = 0, 0

    for op, args in ops:
        if op == "moveTo":
            x, y = args[0]
            parts.append(f"M{x:.4f},{y:.4f}")
            current_x, current_y = x, y
        elif op == "lineTo":
            x, y = args[0]
            parts.append(f"L{x:.4f},{y:.4f}")
            current_x, current_y = x, y
        elif op == "qCurveTo":
            # args is a list of (x, y) tuples; last is on-curve, rest are off-curve
            points = args
            if len(points) == 2:
                # Single quadratic: off-curve + on-curve
                cx, cy = points[0]
                x, y = points[1]
                # Convert quadratic to cubic (Q → C)
                mx = 2 * cx - current_x
                my = 2 * cy - current_y
                parts.append(
                    f"C{mx:.4f},{my:.4f} {cx:.4f},{cy:.4f} {x:.4f},{y:.4f}"
                )
            elif len(points) > 2:
                # Multiple off-curve points → chain of quadratics
                # Use implied on-curve points between consecutive off-curve points
                all_points = [(current_x, current_y)] + list(points)
                i = 1
                while i < len(all_points) - 1:
                    cx, cy = all_points[i]
                    # Implied on-curve point is midpoint between consecutive off-curve
                    if i + 1 < len(all_points) - 1:
                        nx, ny = all_points[i + 1]
                        x, y = (cx + nx) / 2, (cy + ny) / 2
                    else:
                        x, y = all_points[i + 1]
                    mx = 2 * cx - current_x
                    my = 2 * cy - current_y
                    parts.append(
                        f"C{mx:.4f},{my:.4f} {cx:.4f},{cy:.4f} {x:.4f},{y:.4f}"
                    )
                    current_x, current_y = x, y
                    i += 1
            current_x, current_y = points[-1]
        elif op == "curveTo":
            c1x, c1y = args[0]
            c2x, c2y = args[1]
            x, y = args[2]
            parts.append(
                f"C{c1x:.4f},{c1y:.4f} {c2x:.4f},{c2y:.4f} {x:.4f},{y:.4f}"
            )
            current_x, current_y = x, y
        elif op == "closePath":
            parts.append("Z")
        elif op == "endPath":
            pass

    return " ".join(parts)


def resolve_use_elements(root):
    """Resolve <use> elements by inlining the referenced element.

    Must be called BEFORE remove_ids() since <use href="#id"> needs ids.
    Handles transforms by merging parent <use> transform with referenced element.
    """
    NS = "http://www.w3.org/2000/svg"
    xlink_href = f"{{{NS_XLINK}}}href"

    for use_elem in list(root.iter(f"{{{NS}}}use")):
        href = use_elem.get("href") or use_elem.get(xlink_href, "")
        if not href or not href.startswith("#"):
            # No valid href — remove (picosvg can't handle these)
            parent = use_elem.getparent()
            if parent is not None:
                parent.remove(use_elem)
            continue

        # Find referenced element by id
        target_id = href[1:]
        target = root.find(f".//*[@id='{target_id}']")
        if target is None:
            parent = use_elem.getparent()
            if parent is not None:
                parent.remove(use_elem)
            continue

        # Clone the referenced element(s)
        parent = use_elem.getparent()
        if parent is None:
            continue

        # Get <use> transform and merge with target's own transform
        use_transform = use_elem.get("transform", "")

        # Deep clone the referenced element(s)
        index = list(parent).index(use_elem)
        parent.remove(use_elem)

        # If target has children, clone each child (container <g>).
        # If target is a leaf (e.g. <path>), clone it directly.
        elements_to_clone = list(target) if len(target) > 0 else [target]

        for elem in elements_to_clone:
            clone = etree.Element(elem.tag, attrib=dict(elem.attrib))
            # Merge transforms: use_transform * elem_transform
            elem_transform = clone.get("transform", "")
            if use_transform and elem_transform:
                clone.set("transform", f"{use_transform} {elem_transform}")
            elif use_transform:
                clone.set("transform", use_transform)
            # Insert at the position where <use> was
            parent.insert(index, clone)
            index += 1


def remove_empty_groups(root):
    """Remove <g> elements that have no child elements."""
    changed = True
    while changed:
        changed = False
        for elem in list(root.iter()):
            if elem.tag == f"{{{NS}}}g" and len(list(elem)) == 0:
                parent = elem.getparent()
                if parent is not None:
                    parent.remove(elem)
                    changed = True


def remove_ids(root):
    """Remove id attributes (not needed for font)."""
    for elem in root.iter():
        elem.attrib.pop("id", None)


_WRONG_TC_ARC_RE = re.compile(
    r'^M\s*32,\s*-5\s+V\s*51\s+A\s*28,\s*28\s+0\s+0\s+0\s+32,\s*-5\s+z$',
    re.IGNORECASE
)


def fix_total_cloud_cover_arcs(root, filename=""):
    """
    Fix arc geometry in N_TotalCloudCover symbols where the filled arc is
    misaligned with its companion circle.

    Problem: N_5.svg (and N_3, N_6, N_7, N_8, N_9) have a filled arc whose chord
    endpoints are at y=-5 and y=51 (arc center at (32, 23)), but the companion
    circle is centered at (32, 32) with radius 28.  The arc's chord should be
    at y=32 (the circle's center), connecting (4, 32) to (60, 32).

    The incorrect arc:  M 32,-5 V 51 A 28,28 0 0 0 32,-5 z
    The correct arc:    M 4,32 A 28,28 0 0 0 60,32 Z
                        (counterclockwise from (60,32) to (4,32), filling the
                         bottom semicircle of the circle centered at (32,32))
    """
    if "TotalCloudCover" not in filename:
        return

    # Find all path elements with fill (not stroke-only)
    for path in root.iter(f"{{{NS}}}path"):
        d = path.get("d", "")
        if not d:
            continue
        fill = path.get("fill", "").lower()
        style = path.get("style", "").lower()
        # Only fix filled paths (not strokes)
        if fill in ("none", "") and "fill:none" in style:
            continue

        # Detect the wrong arc pattern: M 32,-5 V 51 A 28,28 0 0 0 32,-5 z
        # (arc centered at (32,23) instead of (32,32))
        if not _WRONG_TC_ARC_RE.match(d.strip()):
            continue

        # Replace with the correct arc for a bottom semicircle of the circle
        # centered at (32, 32) with radius 28:
        # chord from (4, 32) to (60, 32), arc sweeping counterclockwise
        # through the bottom (fs=0, large-arc=0)
        path.set("d", "M 4,32 A 28,28 0 0 0 60,32 Z")


def _translate_path_y_coords(d, dy):
    """Translate all Y coordinates in an SVG path data string by dy units."""
    tokens = _NUM_RE.findall(d)
    # Determine Y indices for each command type
    # M L T: index 1; H: none; V: 0; C: 1,3,5; S: 1,3; Q: 1,3; A: 5
    # After M/m, subsequent pairs are implicit l/l
    CMD_RE = re.compile(r'[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)')
    parts = CMD_RE.split(d)
    # parts: ['', 'M', ' 0,0 ', 'H', ' -40 ', ...]
    # Actually just use the tokens approach
    tokens2 = _NUM_RE.findall(d)
    # Track implicit command state
    implicit_l = False
    result_nums = []
    for tok in tokens2:
        result_nums.append(tok)
    # Simpler: just translate Y in the path data by finding all Y coords
    # Use a state machine approach
    tokens3 = CMD_RE.findall(d)
    result = []
    i = 0
    cmd = None
    N = {'M': 2, 'L': 2, 'H': 1, 'V': 1, 'C': 6, 'S': 4, 'Q': 4, 'T': 2,
         'A': 7, 'Z': 0, 'm': 2, 'l': 2, 'h': 1, 'v': 1, 'c': 6, 's': 4,
         'q': 4, 't': 2, 'a': 7, 'z': 0}
    Y = {'M': [1], 'L': [1], 'H': [], 'V': [0],
         'C': [1, 3, 5], 'S': [1, 3], 'Q': [1, 3], 'T': [1], 'A': [5], 'Z': [],
         'm': [1], 'l': [1], 'h': [], 'v': [0],
         'c': [1, 3, 5], 's': [1, 3], 'q': [1, 3], 't': [1], 'a': [5], 'z': []}

    toks = CMD_RE.findall(d)
    i = 0
    while i < len(toks):
        tok = toks[i]
        if tok.isalpha():
            result.append(tok)
            cmd = tok
            i += 1
            continue
        # Number: collect N[cmd] params
        n = N.get(cmd, 2)
        y_idx = set(Y.get(cmd, []))
        for _ in range(n):
            if i >= len(toks) or toks[i].isalpha():
                break
            p = float(toks[i])
            if (len(result) - 1) % n in y_idx if len(result) > 0 else False:
                p = p + dy
            # Format: keep original precision
            if p == int(p):
                result.append(str(int(p)))
            else:
                result.append(f"{p:.4f}".rstrip('0').rstrip('.'))
            i += 1
        # After M/m, subsequent pairs are implicit l/l
        if cmd in ('M', 'm') and len(result) >= 1:
            lc = 'l' if cmd.islower() else 'L'
            y_idx_l = set(Y.get(lc, []))
            while i < len(toks) and not toks[i].isalpha():
                # X
                if i < len(toks) and not toks[i].isalpha():
                    result.append(toks[i]); i += 1
                # Y (shifted)
                if i < len(toks) and not toks[i].isalpha():
                    p = float(toks[i])
                    if 1 in y_idx_l:
                        p = p + dy
                    if p == int(p):
                        result.append(str(int(p)))
                    else:
                        result.append(f"{p:.4f}".rstrip('0').rstrip('.'))
                    i += 1
    return ''.join(result)


def clean_svg(svg_path):
    """Apply all cleaning steps to an SVG and return cleaned XML bytes."""
    tree = etree.parse(str(svg_path))
    root = tree.getroot()

    # Normalize namespace: ensure all SVG elements use the SVG namespace.
    # Some upstream SVGs use <svg:svg> with svg: prefix bound to the SVG
    # namespace, while child elements like <rect>, <text> have no namespace.
    # This causes issues with namespace-aware processing (picosvg, fontTools).
    svg_prefix = None
    for prefix, uri in root.nsmap.items():
        if uri == NS and prefix is not None:
            svg_prefix = prefix
            break

    if svg_prefix:
        # Elements with no namespace should be in the SVG namespace
        for elem in root.iter():
            tag = elem.tag
            if isinstance(tag, str) and not tag.startswith("{"):
                elem.tag = f"{{{NS}}}{tag}"

    resolve_use_elements(root)
    strip_metadata(root)
    fix_css_units(root)
    remove_markers_and_defs(root)
    remove_stray_elements(root)
    convert_text_to_path(root)
    remove_empty_groups(root)
    remove_ids(root)
    fix_total_cloud_cover_arcs(root, filename=svg_path.name)

    # Clean up namespaces
    etree.cleanup_namespaces(root)

    # Ensure xmlns is set
    result = etree.tostring(root, xml_declaration=False, encoding="unicode")
    if 'xmlns="' not in result and "xmlns='" not in result:
        result = result.replace("<svg ", f'<svg xmlns="{NS}" ', 1)

    return result


def main():
    parser = argparse.ArgumentParser(description="Clean upstream WMO SVGs")
    parser.add_argument(
        "--input-dir",
        default=str(DEFAULT_INPUT),
        help="Input directory (default: vendor/WorldWeatherSymbols/symbols)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT),
        help="Output directory (default: src)",
    )
    parser.add_argument(
        "--source-map",
        default=str(SOURCE_MAP_PATH),
        help="Path to source-map.json",
    )
    args = parser.parse_args()

    source_map = load_source_map(args.source_map)
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)

    success = 0
    failed = 0
    cleaned = 0

    for sym_path, codepoint in sorted(source_map.items(), key=lambda x: x[1]):
        input_file = input_dir.parent / sym_path
        if not input_file.exists():
            print(f"  SKIP: {sym_path} not found", file=sys.stderr)
            continue

        # Determine output path: preserve category structure
        # sym_path is like "symbols/ICAO_SigWx/WeatherSymbol_ICAO_TropicalCyclone.svg"
        # output should be "src/ICAO_SigWx/WeatherSymbol_ICAO_TropicalCyclone.svg"
        rel_path = Path(sym_path).relative_to("symbols")
        output_file = output_dir / rel_path
        output_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            result = clean_svg(input_file)
            output_file.write_text(result, encoding="utf-8")
            success += 1
        except Exception as e:
            print(
                f"  FAIL: {sym_path} -> {output_file}: {e}",
                file=sys.stderr,
            )
            failed += 1

    print(f"\nCleaned: {success} OK, {failed} failed")


if __name__ == "__main__":
    main()
