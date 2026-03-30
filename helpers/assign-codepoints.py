#!/usr/bin/env python3
"""
Assign PUA codepoints to all WMO weather symbols, organized by category blocks.

Walks vendor/WorldWeatherSymbols/symbols/ to discover all SVGs, assigns codepoints
per the category block table, and outputs:
  - data/codepoint-map.json: symbol path -> PUA codepoint
  - data/icons.json: font config with codepoint -> color/svg path
  - data/plane-info.json: per-plane metadata from Dublin Core

Usage:
    python3 helpers/assign-codepoints.py
"""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
VENDOR_DIR = PROJECT_DIR / "vendor" / "WorldWeatherSymbols"
SYMBOLS_DIR = VENDOR_DIR / "symbols"
DATA_DIR = PROJECT_DIR / "data"
VERSION_FILE = PROJECT_DIR / "font" / "VERSION"

NS_DC = "http://purl.org/dc/elements/1.1/"
NS_CC = "http://creativecommons.org/ns#"
NS_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"


def read_font_version():
    """Read version from font/VERSION, return in nanoemoji format (e.g. '001.000')."""
    try:
        version = VERSION_FILE.read_text().strip()
    except FileNotFoundError:
        version = "0.0.0"
    parts = version.split(".")
    return ".".join(p.zfill(3) for p in parts)


# ---------------------------------------------------------------------------
# Block allocation: each (block_name, slug, start, size, categories)
# ---------------------------------------------------------------------------

BLOCKS = [
    ("Sky & Cloud", "sky-cloud", 0xE000, 256, [
        "CH_CloudHigh",
        "CL_CloudLow",
        "CM_CloudMedium",
        "C_CloudGenus",
        "N_TotalCloudCover",
    ]),
    ("Present Weather", "present-weather", 0xE100, 512, [
        "ww_PresentWeather",
        "wawa_PresentWeatherAutomaticStation",
        "w1w1_PresentWeatherAdditional",
    ]),
    ("Past Weather", "past-weather", 0xE300, 256, [
        "W1W2_PastWeather",
        "Wa1Wa2_PastWeatherAutomaticStation",
    ]),
    ("State of Ground", "state-of-ground", 0xE400, 256, [
        "E_StateOfGround",
        "Eprime_StateOfGround",
    ]),
    ("Pressure", "pressure", 0xE500, 256, [
        "a_PressureTendencyCharacteristic",
        "PressureCentres",
    ]),
    ("Wind & Ocean", "wind-ocean", 0xE600, 256, [
        "ddff_WindArrows",
        "dw1dw1_SwellDirection",
        "Ds_ShipDirection",
    ]),
    ("Significant Weather", "significant-weather", 0xE700, 256, [
        "ICAO_SigWx",
        "Ft_Fronts",
    ]),
]

# Build lookup: category_dir -> (block_slug, block_start)
CATEGORY_TO_BLOCK = {}
for _name, slug, start, _size, cats in BLOCKS:
    for cat in cats:
        CATEGORY_TO_BLOCK[cat] = (slug, start)

# Category display labels (for plane-info.json)
CATEGORY_LABELS = {
    "CH_CloudHigh": "Cloud High (CH)",
    "CL_CloudLow": "Cloud Low (CL)",
    "CM_CloudMedium": "Cloud Medium (CM)",
    "C_CloudGenus": "Cloud Genus (C)",
    "N_TotalCloudCover": "Total Cloud Cover (N)",
    "ww_PresentWeather": "Present Weather (ww)",
    "wawa_PresentWeatherAutomaticStation": "Present Weather — Auto Station (wawa)",
    "w1w1_PresentWeatherAdditional": "Present Weather — Additional (w1w1)",
    "W1W2_PastWeather": "Past Weather (W1W2)",
    "Wa1Wa2_PastWeatherAutomaticStation": "Past Weather — Auto Station (Wa1Wa2)",
    "E_StateOfGround": "State of Ground (E)",
    "Eprime_StateOfGround": "State of Ground (E')",
    "a_PressureTendencyCharacteristic": "Pressure Tendency (a)",
    "PressureCentres": "Pressure Centres",
    "ddff_WindArrows": "Wind Arrows (dd, ff)",
    "dw1dw1_SwellDirection": "Swell Direction (dw, dw)",
    "Ds_ShipDirection": "Ship Direction (Ds)",
    "ICAO_SigWx": "Significant Weather — ICAO",
    "Ft_Fronts": "Fronts (Ft)",
}


def extract_dublin_core(svg_path):
    """Extract Dublin Core metadata from an upstream SVG."""
    dc = {}
    try:
        tree = ET.parse(str(svg_path))
        root = tree.getroot()
        ns = {"dc": NS_DC, "cc": NS_CC, "rdf": NS_RDF}

        # Find cc:Work element
        work = root.find(".//{%s}Work" % NS_CC)
        if work is None:
            return dc

        def find_text(parent, tag):
            el = parent.find(f"{{{NS_DC}}}{tag}")
            if el is not None and el.text:
                return el.text.strip()
            el = parent.find(tag)
            if el is not None and el.text:
                return el.text.strip()
            return None

        def find_agent_title(dc_tag):
            """Find text inside dc:{tag} > cc:Agent > dc:title."""
            dc_el = work.find(f"{{{NS_DC}}}{dc_tag}")
            if dc_el is None:
                return None
            agent = dc_el.find(f"{{{NS_CC}}}Agent")
            if agent is not None:
                return find_text(agent, "title")
            return None

        dc["identifier"] = find_text(work, "identifier")
        dc["source"] = find_text(work, "source")
        dc["date"] = find_text(work, "date")
        dc["creator"] = find_agent_title("creator")
        dc["publisher"] = find_agent_title("publisher")
        dc["rights"] = find_agent_title("rights")
        dc["contributor"] = find_agent_title("contributor")

        # License URL from cc:license attribute
        license_el = work.find(f"{{{NS_CC}}}license")
        if license_el is not None:
            dc["license"] = license_el.get(
                f"{{{NS_RDF}}}resource",
                license_el.get("resource", ""),
            )

    except Exception:
        pass
    return dc


def natural_sort_key(filename):
    """Sort SVG filenames naturally (numeric parts ordered numerically)."""
    # Strip common prefix
    name = filename
    for prefix in ("WeatherSymbol_WMO_", "WeatherSymbol_ICAO_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    name = name.replace(".svg", "")
    return name


def main():
    # Discover all SVGs grouped by category
    categories = {}
    for svg_path in sorted(SYMBOLS_DIR.rglob("*.svg")):
        rel = svg_path.relative_to(SYMBOLS_DIR)
        parts = rel.parts
        cat = parts[0]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(svg_path)

    # Assign codepoints
    codepoint_map = {}  # "symbols/Cat/File.svg" -> int
    icons_glyphs = {}   # "0xHEX" -> {"src": "color/svg/E000.svg", "width": 1024}

    # Track per-plane metadata
    plane_info = {}

    for _name, slug, block_start, _block_size, block_cats in BLOCKS:
        addr = block_start
        sub_cats = []

        for cat in block_cats:
            if cat not in categories:
                continue

            svgs = sorted(categories[cat], key=lambda p: natural_sort_key(p.name))
            sub_cat_count = 0

            for svg_path in svgs:
                rel = f"symbols/{svg_path.relative_to(SYMBOLS_DIR)}"
                codepoint_map[rel] = addr
                hexcode = f"{addr:04X}"
                icons_glyphs[f"0x{addr:04X}"] = {
                    "src": f"color/svg/{hexcode}.svg",
                    "width": 1024,
                }
                addr += 1
                sub_cat_count += 1

            # Extract per-sub-category metadata from first SVG
            first_in_cat = svgs[0]
            cat_dc = extract_dublin_core(first_in_cat) if first_in_cat else {}

            sub_cats.append({
                "id": cat,
                "label": CATEGORY_LABELS.get(cat, cat),
                "count": sub_cat_count,
                "source": cat_dc.get("source", ""),
                "creator": cat_dc.get("creator", ""),
                "publisher": cat_dc.get("publisher", ""),
                "date": cat_dc.get("date", ""),
                "identifier": cat_dc.get("identifier", ""),
            })

        # Extract plane metadata from first SVG in the block
        first_svg = None
        for cat in block_cats:
            if cat in categories and categories[cat]:
                first_svg = sorted(categories[cat], key=lambda p: natural_sort_key(p.name))[0]
                break

        dc = extract_dublin_core(first_svg) if first_svg else {}

        plane_end = addr - 1
        total_count = sum(sc["count"] for sc in sub_cats)

        plane_info[slug] = {
            "slug": slug,
            "name": _name,
            "blockStart": block_start,
            "blockEnd": plane_end,
            "range": [f"U+{block_start:04X}", f"U+{plane_end:04X}"],
            "count": total_count,
            "subCategories": sub_cats,
            "wmoSource": dc.get("source", ""),
            "wmoCodeTable": dc.get("identifier", ""),
            "creator": dc.get("creator", ""),
            "publisher": dc.get("publisher", ""),
            "date": dc.get("date", ""),
            "contributor": dc.get("contributor", ""),
            "license": dc.get("license", ""),
        }

    # Write outputs
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Merge category-descriptions.json into plane-info
    cat_desc_path = DATA_DIR / "category-descriptions.json"
    if cat_desc_path.exists():
        with open(cat_desc_path) as f:
            cat_descriptions = json.load(f)
        # Remove schema key if present
        cat_descriptions.pop("_schema", None)
        cat_descriptions.pop("_description", None)
        for slug, desc in cat_descriptions.items():
            if slug in plane_info:
                # Merge description, overview, usageInPractice, wmoDocuments, codeTables
                for key in ("description", "overview", "usageInPractice", "wmoDocuments", "codeTables"):
                    if key in desc:
                        plane_info[slug][key] = desc[key]
                # Merge sub-category descriptions
                if "subCategories" in desc and isinstance(desc["subCategories"], dict):
                    for sub in plane_info[slug]["subCategories"]:
                        sub_desc = desc["subCategories"].get(sub["id"], {})
                        for key in ("description", "codeTable"):
                            if key in sub_desc:
                                sub[key] = sub_desc[key]

    with open(DATA_DIR / "codepoint-map.json", "w") as f:
        json.dump(codepoint_map, f, indent=2, sort_keys=True)

    icons_config = {
        "props": {
            "ascent": 1045,
            "descent": -275,
            "em": 1024,
            "encoding": "UnicodeFull",
            "family": "MetFont",
            "fontname": "MetFont-Regular",
            "fullname": "MetFont Regular",
            "style": "Regular",
            "version": read_font_version(),
        },
        "glyphs": icons_glyphs,
    }
    with open(DATA_DIR / "icons.json", "w") as f:
        json.dump(icons_config, f, indent=2)

    with open(DATA_DIR / "plane-info.json", "w") as f:
        json.dump(plane_info, f, indent=2)

    total = len(codepoint_map)
    print(f"Assigned {total} codepoints across {len(plane_info)} planes")
    for slug, info in plane_info.items():
        print(
            f"  {info['name']:24s} U+{info['blockStart']:04X}–U+{info['blockEnd']:04X} "
            f"({info['count']:3d} glyphs)"
        )


if __name__ == "__main__":
    main()
