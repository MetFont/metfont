#!/usr/bin/env python3
"""
Normalize WMO weather symbol SVGs for font compilation.

Reads SVGs from src/ (cleaned sources), uses picosvg to convert strokes to fills,
normalizes viewBox to 0 0 72 72, and writes to color/svg/ with PUA hexcode filenames.

Usage:
    python3 helpers/normalize-svg.py \
        --input-dir src/ \
        --mapping data/source-map.json \
        --output-dir color/svg/
"""

import argparse
import json
import math
import os
import re
import sys
from io import BytesIO
from pathlib import Path

from lxml import etree
from picosvg.svg import SVG, parse_view_box, Affine2D

# Fix scientific notation in SVG path data (fontTools doesn't handle 1e-7)
_SCI_NOTATION_RE = re.compile(
    r'([-+]?(?:[0-9]*\.?[0-9]+)?)(e[-+]?[0-9]+)', re.IGNORECASE
)
_NUM_RE = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)')

NS = 'http://www.w3.org/2000/svg'

# SVG path command regex: matches command letters and number sequences
_CMD_RE = re.compile(r'[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)')


def fix_scientific_notation(s):
    """Convert scientific notation in SVG path data to fixed-point."""
    def replacer(m):
        full = m.group(1) + m.group(2)
        if m.group(1) in ('', '+', '-'):
            return full
        try:
            return f"{float(full):.6f}"
        except ValueError:
            return full
    return _SCI_NOTATION_RE.sub(replacer, s)


def parse_path_coords(d):
    """Extract all x,y coordinate pairs from SVG path data, including bezier control points.

    For bezier curves (C, S, Q), includes control point coordinates since they define
    the spatial extent of the curve. For arcs (A), computes the actual bounding box
    of the elliptical arc segment.

    Returns list of (x, y) tuples.
    """
    tokens = _CMD_RE.findall(d)
    coords = []
    i = 0
    current_cmd = None
    param_count = 0
    params = []
    cur_x, cur_y = 0.0, 0.0  # current point

    while i < len(tokens):
        token = tokens[i]
        if token.isalpha():
            cmd_char = token
            current_cmd = cmd_char.upper()
            param_count = 0
            params = []
            i += 1
            continue

        params.append(float(token))
        param_count += 1

        if current_cmd == 'A':
            if param_count == 7:
                rx, ry = abs(params[0]), abs(params[1])
                x2, y2 = params[5], params[6]
                # Arc endpoint
                coords.append((x2, y2))
                # Compute arc bounding box for elliptical arc from (cur_x,cur_y) to (x2,y2)
                # Convert to center parameterization
                if rx > 0 and ry > 0:
                    phi = params[2]
                    fA = params[3]
                    fs = params[4]
                    cos_phi = math.cos(math.radians(phi))
                    sin_phi = math.sin(math.radians(phi))

                    # Step 1: compute (x1', y1')
                    dx = (cur_x - x2) / 2.0
                    dy = (cur_y - y2) / 2.0
                    x1p = cos_phi * dx + sin_phi * dy
                    y1p = -sin_phi * dx + cos_phi * dy

                    # Step 2: compute (cx', cy')
                    rx_sq, ry_sq = rx * rx, ry * ry
                    x1p_sq, y1p_sq = x1p * x1p, y1p * y1p

                    # Check for valid ellipse
                    lam = x1p_sq / rx_sq + y1p_sq / ry_sq
                    if lam > 1.0:
                        rx *= math.sqrt(lam)
                        ry *= math.sqrt(lam)
                        rx_sq, ry_sq = rx * rx, ry * ry

                    num = max(0, rx_sq * ry_sq - rx_sq * y1p_sq - ry_sq * x1p_sq)
                    den = rx_sq * y1p_sq + ry_sq * x1p_sq
                    if den > 0:
                        sq = math.sqrt(num / den)
                        if fA == fs:
                            sq = -sq
                        cxp = sq * rx * y1p / ry
                        cyp = -sq * ry * x1p / rx
                    else:
                        cxp = cyp = 0.0

                    # Step 3: compute (cx, cy)
                    cx = cos_phi * cxp - sin_phi * cyp + (cur_x + x2) / 2.0
                    cy = sin_phi * cxp + cos_phi * cyp + (cur_y + y2) / 2.0

                    # Step 4: compute theta1, dtheta
                    def angle(ux, uy, vx, vy):
                        n = math.sqrt(ux * ux + uy * uy) * math.sqrt(vx * vx + vy * vy)
                        if n < 1e-10:
                            return 0.0
                        c = max(-1.0, min(1.0, (ux * vx + uy * vy) / n))
                        a = math.acos(c)
                        if ux * vy - uy * vx < 0:
                            a = -a
                        return a

                    theta1 = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
                    dtheta = angle((x1p - cxp) / rx, (y1p - cyp) / ry,
                                   (-x1p - cxp) / rx, (-y1p - cyp) / ry)
                    if not fs and dtheta > 0:
                        dtheta -= 2 * math.pi
                    elif fs and dtheta < 0:
                        dtheta += 2 * math.pi

                    # Add extremes
                    n_ext = 0
                    if math.sin(phi) != 0:
                        n_ext = 4
                    if math.cos(phi) != 0:
                        n_ext += 4

                    for k in range(n_ext):
                        angle_k = theta1 + k * math.pi / 2.0
                        # Check if this angle is within the arc sweep
                        if not _angle_in_arc(angle_k, theta1, dtheta):
                            continue
                        xe = rx * math.cos(angle_k)
                        ye = ry * math.sin(angle_k)
                        # Rotate back
                        fx = cos_phi * xe - sin_phi * ye + cx
                        fy = sin_phi * xe + cos_phi * ye + cy
                        coords.append((fx, fy))

                cur_x, cur_y = x2, y2
                param_count = 0
                params = []

        elif current_cmd in ('M', 'L', 'T'):
            if param_count == 2:
                coords.append((params[0], params[1]))
                if current_cmd == 'M':
                    current_cmd = 'L'
                cur_x, cur_y = params[0], params[1]
                param_count = 0
                params = []

        elif current_cmd in ('H', 'V'):
            if param_count == 1:
                if current_cmd == 'H':
                    coords.append((params[0], cur_y))
                    cur_x = params[0]
                else:
                    coords.append((cur_x, params[0]))
                    cur_y = params[0]
                param_count = 0
                params = []

        elif current_cmd in ('C', 'S', 'Q'):
            expected = 6 if current_cmd == 'C' else 4
            if param_count == expected:
                # Include ALL control points, not just endpoint
                for j in range(0, expected, 2):
                    coords.append((params[j], params[j + 1]))
                if current_cmd == 'C':
                    cur_x, cur_y = params[4], params[5]
                elif current_cmd == 'S':
                    cur_x, cur_y = params[2], params[3]
                else:  # Q
                    cur_x, cur_y = params[2], params[3]
                param_count = 0
                params = []

        i += 1

    return coords


def _angle_in_arc(angle, theta1, dtheta):
    """Check if angle is within the arc from theta1 spanning dtheta."""
    # Normalize angle relative to theta1
    a = angle - theta1
    # Normalize to [-pi, pi]
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    # Check against dtheta (which can be > 2pi or < -2pi)
    if dtheta >= 0:
        return 0 <= a <= dtheta or a + 2 * math.pi <= dtheta
    else:
        return dtheta <= a <= 0 or a - 2 * math.pi >= dtheta

def clean_svg(svg_bytes):
    """Clean SVG for picosvg compatibility.

    1. Remove Dublin Core metadata (dc:, cc:, rdf:, xml:)
    2. Convert svg: namespace prefix to default namespace
    3. Remove <svg:title> and <svg:desc> elements
    4. Remove id attributes
    """
    text = svg_bytes.decode('utf-8', errors='replace')
    text = fix_scientific_notation(text)

    tree = etree.fromstring(text.encode())

    # Collect namespaces to remove (everything except svg)
    ns_to_remove = []
    for prefix, uri in tree.nsmap.items():
        if uri == NS:
            continue
        if prefix is not None:
            ns_to_remove.append((prefix, uri))

    # Remove namespace prefixes from all elements
    # First, convert svg: prefixed elements to default namespace
    for elem in tree.iter():
        tag = elem.tag
        if isinstance(tag, str) and tag.startswith(f'{{{NS}}}'):
            # Already in default namespace
            elem.tag = tag.replace(f'{{{NS}}}', '')
        elif isinstance(tag, str) and ':svg:' in f'{{{tag}}}':
            # Has svg: prefix in namespace
            pass

        # Remove Dublin Core / RDF / other namespace elements
        if isinstance(tag, str):
            for prefix, uri in ns_to_remove:
                if tag.startswith(f'{{{uri}}}'):
                    elem.getparent().remove(elem)
                    break

    # Also handle the svg: prefix specifically
    # The SVGs use <svg:svg>, <svg:path>, etc.
    for elem in tree.iter():
        tag = elem.tag
        if isinstance(tag, str) and tag.startswith('{http://www.w3.org/2000/svg}'):
            elem.tag = tag.replace('{http://www.w3.org/2000/svg}', '')

    # Remove remaining namespaced attributes
    for elem in tree.iter():
        attrs_to_remove = [k for k in elem.attrib if ('}' in k or ':' in k)]
        for attr in attrs_to_remove:
            del elem.attrib[attr]

    # Remove id attributes (not needed for font)
    for elem in tree.iter():
        elem.attrib.pop('id', None)

    # Remove <use> elements without valid #fragment href (picosvg can't handle them)
    for elem in list(tree.iter()):
        if elem.tag == 'use':
            href = elem.get('href') or elem.get('{http://www.w3.org/1999/xlink}href')
            if not href or not href.startswith('#'):
                elem.getparent().remove(elem)
                continue

    # Remove <title>, <desc>, <metadata>, <marker>, <defs> elements
    # Note: <text> elements should already be converted to paths by clean-sources.py
    for elem in list(tree.iter()):
        if elem.tag in ('title', 'desc', 'metadata', 'marker', 'defs'):
            parent = elem.getparent()
            if parent is not None:
                parent.remove(elem)

    # Fix CSS unit values (1pt -> 1, 1px -> 1) in both attributes and style strings
    _UNIT_RE = re.compile(r'(\d+(?:\.\d+)?)(px|pt|em|rem|mm|cm|%)', re.IGNORECASE)
    for elem in tree.iter():
        for attr_name in list(elem.attrib):
            val = elem.attrib[attr_name]
            if isinstance(val, str):
                val_fixed = _UNIT_RE.sub(r'\1', val)
                if val_fixed != val:
                    elem.attrib[attr_name] = val_fixed

    # Set default namespace
    etree.cleanup_namespaces(tree)

    # picosvg requires default namespace to be set
    if tree.nsmap.get(None) != NS:
        result = etree.tostring(tree, xml_declaration=False, encoding='unicode')
        if 'xmlns=' not in result and 'xmlns="' not in result:
            result = result.replace('<svg ', f'<svg xmlns="{NS}" ', 1)
        return result.encode('utf-8')

    return etree.tostring(tree, xml_declaration=False, encoding='utf-8')


def load_mapping(mapping_path):
    """Load icons.json to build category/filename -> codepoint mapping."""
    with open(mapping_path) as f:
        config = json.load(f)

    mapping = {}
    for codepoint_hex, glyph_info in config["glyphs"].items():
        src = glyph_info["src"]
        # src is like "icons-stripped/CH_CloudHigh/CloudHigh_CH_1.svg"
        parts = src.split('/')
        if len(parts) >= 3:
            relative = '/'.join(parts[1:])
        else:
            relative = src
        mapping[relative] = int(codepoint_hex, 16)

    return mapping


def load_source_mapping(mapping_path):
    """Load codepoint-map.json: symbols/relative/path -> codepoint.

    codepoint-map.json format: {"symbols/Category/Filename.svg": 57344, ...}
    The keys include the "symbols/" prefix which must be stripped when matching
    against files in the input directory.
    """
    with open(mapping_path) as f:
        raw = json.load(f)

    mapping = {}
    for sym_path, codepoint in raw.items():
        # sym_path is like "symbols/ICAO_SigWx/WeatherSymbol_ICAO_TropicalCyclone.svg"
        # Strip the "symbols/" prefix since input-dir points to the symbols/ contents
        if sym_path.startswith("symbols/"):
            relative = sym_path[len("symbols/"):]
        else:
            relative = sym_path
        mapping[relative] = int(codepoint) if isinstance(codepoint, str) else codepoint

    return mapping


def normalize_svg(svg_bytes, scale_factor=1.0):
    """
    Normalize an SVG:
    1. Clean namespaces and metadata
    2. Use picosvg to convert strokes to fills and bake transforms
    3. Scale content to fit 0 0 72 72 viewBox

    Falls back to simple namespace cleaning + viewBox transform if picosvg fails.

    Args:
        svg_bytes: Raw SVG data
        scale_factor: Additional scale applied to the content (0.5 = half size, centered)
    """
    cleaned = clean_svg(svg_bytes)

    # Try picosvg for stroke-to-fill conversion and transform baking
    root = None
    used_picosvg = False
    try:
        svg = SVG.parse(BytesIO(cleaned))
        psvg = svg.topicosvg()
        root = psvg.toetree()
        used_picosvg = True
        # picosvg may introduce <text> or <rect> elements — strip them
        for elem in list(root.iter()):
            if elem.tag in ('text', 'rect'):
                parent = elem.getparent()
                if parent is not None:
                    parent.remove(elem)
    except Exception:
        # Fallback: use cleaned SVG
        try:
            root = etree.fromstring(cleaned)
        except Exception:
            root = etree.fromstring(cleaned)

    # Compute bounding box using parse_path_coords which handles arcs and control points
    all_coords = []
    for path in root.iter(f'{{{NS}}}path'):
        d = path.get('d', '')
        if d:
            all_coords.extend(parse_path_coords(d))

    if all_coords:
        xs = [c[0] for c in all_coords]
        ys = [c[1] for c in all_coords]
        content_xmin, content_xmax = min(xs), max(xs)
        content_ymin, content_ymax = min(ys), max(ys)
        # Use the actual content bounds as the effective viewBox with padding
        pad = 1.0
        vx = content_xmin - pad
        vy = content_ymin - pad
        vw = (content_xmax - content_xmin) + 2 * pad
        vh = (content_ymax - content_ymin) + 2 * pad
    else:
        vx, vy, vw, vh = -27.5, -27.5, 55.0, 55.0

    # Remove width/height that could conflict
    for attr in ('width', 'height'):
        root.attrib.pop(attr, None)
        root.attrib.pop(f'{{{NS}}}{attr}', None)

    # Wrap content in <g> with transform to map content → centered in viewBox.
    # Both horizontal and vertical: centered in 0-72.
    VB_WIDTH = 72.0
    VB_HEIGHT = 72.0

    sx = VB_WIDTH / vw if vw > 0 else 1.0
    sy = VB_HEIGHT / vh if vh > 0 else 1.0
    s = min(sx, sy)

    tx = (VB_WIDTH - vw * s) / 2 - vx * s
    ty = (VB_HEIGHT - vh * s) / 2 - vy * s  # center vertically

    # Apply additional scale factor (e.g., 0.5 to make symbol half size, centered)
    if scale_factor != 1.0:
        # Compute center of the content in the output (72x72) space
        center_vx = vx + vw / 2
        center_vy = vy + vh / 2
        center_out_x = s * center_vx + tx
        center_out_y = s * center_vy + ty
        # Scale down, keep center fixed
        s *= scale_factor
        tx = center_out_x - s * center_vx
        ty = center_out_y - s * center_vy

    g = etree.Element(f'{{{NS}}}g')
    g.set('transform', f'matrix({s},0,0,{s},{tx},{ty})')
    children = list(root)
    root.append(g)
    for child in children:
        g.append(child)

    # Set viewBox to 0 0 72 72
    root.set('viewBox', '0 0 72 72')

    result = etree.tostring(root, xml_declaration=False, encoding='unicode')
    if 'xmlns=' not in result and 'xmlns="' not in result:
        result = result.replace('<svg ', f'<svg xmlns="{NS}" ', 1)

    return result


def main():
    parser = argparse.ArgumentParser(description='Normalize WMO SVGs for font compilation')
    parser.add_argument('--input-dir', required=True, help='Directory containing icon SVGs')
    parser.add_argument('--mapping', required=True,
                        help='Path to codepoint-map.json, source-map.json, or icons.json')
    parser.add_argument('--output-dir', required=True, help='Output directory for normalized SVGs')
    args = parser.parse_args()

    # Choose mapping loader based on file content
    mapping_path = Path(args.mapping)
    if mapping_path.name in ('source-map.json', 'codepoint-map.json'):
        mapping = load_source_mapping(args.mapping)
    else:
        mapping = load_mapping(args.mapping)

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    success = 0
    skipped = 0
    failed = 0

    for svg_path in sorted(input_dir.rglob('*.svg')):
        relative = str(svg_path.relative_to(input_dir))

        if relative not in mapping:
            skipped += 1
            continue

        codepoint = mapping[relative]
        hexcode = f"{codepoint:04X}"
        output_path = output_dir / f"{hexcode}.svg"

        svg_bytes = svg_path.read_bytes()

        try:
            result = normalize_svg(svg_bytes)
            output_path.write_text(result, encoding='utf-8')
            success += 1
        except Exception as e:
            print(f"  FAIL: {relative} -> {hexcode}: {e}", file=sys.stderr)
            failed += 1

    print(f"\nNormalized: {success} OK, {skipped} skipped, {failed} failed")


if __name__ == '__main__':
    main()
