#!/usr/bin/env python3
"""
Extract glyph metadata from codepoint-map.json and SVG source files.
Enriches with Dublin Core metadata from upstream SVGs.
Outputs glyphs.json for the website.
"""
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
VENDOR_DIR = PROJECT_DIR / "vendor" / "WorldWeatherSymbols"
DATA_DIR = PROJECT_DIR / "data"

# Directory name -> human-readable category label
CATEGORY_LABELS = {
    'CH_CloudHigh': 'Cloud High (CH)',
    'CL_CloudLow': 'Cloud Low (CL)',
    'CM_CloudMedium': 'Cloud Medium (CM)',
    'C_CloudGenus': 'Cloud Genus (C)',
    'N_TotalCloudCover': 'Total Cloud Cover (N)',
    'ww_PresentWeather': 'Present Weather (ww)',
    'wawa_PresentWeatherAutomaticStation': 'Present Weather \u2014 Auto Station (wawa)',
    'w1w1_PresentWeatherAdditional': 'Present Weather \u2014 Additional (w1w1)',
    'W1W2_PastWeather': 'Past Weather (W1W2)',
    'Wa1Wa2_PastWeatherAutomaticStation': 'Past Weather \u2014 Auto Station (Wa1Wa2)',
    'E_StateOfGround': 'State of Ground (E)',
    'Eprime_StateOfGround': "State of Ground (E\u2032)",
    'a_PressureTendencyCharacteristic': 'Pressure Tendency (a)',
    'PressureCentres': 'Pressure Centres',
    'ICAO_SigWx': 'Significant Weather \u2014 ICAO',
    'Ft_Fronts': 'Fronts (Ft)',
    'ddff_WindArrows': 'Wind Arrows (dd, ff)',
    'dw1dw1_SwellDirection': 'Swell Direction (dw, dw)',
    'Ds_ShipDirection': 'Ship Direction (Ds)',
}

# Category directory -> plane slug
CATEGORY_TO_PLANE = {
    'CH_CloudHigh': 'sky-cloud',
    'CL_CloudLow': 'sky-cloud',
    'CM_CloudMedium': 'sky-cloud',
    'C_CloudGenus': 'sky-cloud',
    'N_TotalCloudCover': 'sky-cloud',
    'ww_PresentWeather': 'present-weather',
    'wawa_PresentWeatherAutomaticStation': 'present-weather',
    'w1w1_PresentWeatherAdditional': 'present-weather',
    'W1W2_PastWeather': 'past-weather',
    'Wa1Wa2_PastWeatherAutomaticStation': 'past-weather',
    'E_StateOfGround': 'state-of-ground',
    'Eprime_StateOfGround': 'state-of-ground',
    'a_PressureTendencyCharacteristic': 'pressure',
    'PressureCentres': 'pressure',
    'ddff_WindArrows': 'wind-ocean',
    'dw1dw1_SwellDirection': 'wind-ocean',
    'Ds_ShipDirection': 'wind-ocean',
    'ICAO_SigWx': 'significant-weather',
    'Ft_Fronts': 'significant-weather',
}


def load_codepoint_map():
    """Load codepoint-map.json: symbols/relative/path -> codepoint."""
    with open(DATA_DIR / "codepoint-map.json") as f:
        return json.load(f)


def extract_title_and_desc(svg_path):
    """Extract title and desc from SVG file."""
    title = None
    desc = None
    try:
        tree = ET.parse(str(svg_path))
        root = tree.getroot()
        for elem in root.iter():
            tag = elem.tag
            if tag.endswith('}title') or tag == 'title':
                if elem.text and not title:
                    title = elem.text.strip()
            elif tag.endswith('}desc') or tag == 'desc':
                if elem.text and not desc:
                    desc = elem.text.strip()
    except Exception:
        pass
    return title, desc


def extract_dublin_core(svg_path):
    """Extract ALL Dublin Core metadata from an upstream SVG into a dict."""
    dc = {}
    try:
        tree = ET.parse(str(svg_path))
        root = tree.getroot()
        NS_DC = 'http://purl.org/dc/elements/1.1/'
        NS_CC = 'http://creativecommons.org/ns#'
        NS_RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'

        ns = {
            'dc': NS_DC,
            'cc': NS_CC,
            'rdf': NS_RDF,
        }

        rdf = root.find('.//rdf:RDF', ns)
        if rdf is None:
            for elem in root.iter():
                if elem.tag.endswith('}RDF') or elem.tag == 'RDF':
                    rdf = elem
                    break

        if rdf is None:
            return dc

        work = rdf.find('.//cc:Work', ns)
        if work is None:
            for elem in rdf.iter():
                if elem.tag.endswith('}Work') or elem.tag == 'Work':
                    work = elem
                    break

        if work is None:
            return dc

        def find_text(tag):
            el = work.find(f'{{{NS_DC}}}{tag}')
            if el is not None and el.text:
                return el.text.strip()
            return None

        def find_agent_title(tag):
            dc_el = work.find(f'{{{NS_DC}}}{tag}')
            if dc_el is None:
                return None
            agent = dc_el.find(f'{{{NS_CC}}}Agent')
            if agent is not None:
                t = agent.find(f'{{{NS_DC}}}title')
                if t is not None and t.text:
                    return t.text.strip()
            return None

        # All text fields
        for tag in ('title', 'description', 'identifier', 'source', 'date', 'language', 'coverage'):
            val = find_text(tag)
            if val:
                dc[tag] = val

        # Agent fields (creator, publisher, rights, contributor)
        for tag in ('creator', 'publisher', 'rights', 'contributor'):
            val = find_agent_title(tag)
            if val:
                dc[tag] = val

        # Subject keywords (rdf:Bag > rdf:li)
        bag = work.find(f'.//{{{NS_RDF}}}Bag')
        if bag is not None:
            dc['subject'] = [
                li.text.strip()
                for li in bag.findall(f'{{{NS_RDF}}}li')
                if li.text and li.text.strip()
            ]

        # License URL
        license_el = work.find(f'{{{NS_CC}}}license')
        if license_el is not None:
            dc['license'] = license_el.get(
                f'{{{NS_RDF}}}resource',
                license_el.get('resource', ''),
            )
        if not dc.get('license'):
            for elem in work.iter():
                if elem.tag.endswith('}license') or elem.tag == 'license':
                    dc['license'] = elem.get('resource', '')
                    break

        # rdf:about URL (canonical upstream URL)
        about = work.get(f'{{{NS_RDF}}}about', '')
        if about:
            dc['about'] = about

        # Parse version and status from dc:title suffix
        dc_title = dc.get('title', '')
        version_match = re.search(r'Version\s*([\d.]+)', dc_title, re.IGNORECASE)
        status_match = re.search(r'status\s*(\w+)', dc_title, re.IGNORECASE)
        if version_match:
            dc['version'] = version_match.group(1)
        if status_match:
            dc['status'] = status_match.group(1).capitalize()

    except Exception:
        pass

    return dc


def clean_display_name(name):
    """Strip common WMO prefixes from display names."""
    if not name:
        return name
    name = re.sub(r'^WMO international weather symbol:\s*', '', name, flags=re.IGNORECASE)
    name = re.sub(r'^Symbols for significant weather:\s*', '', name, flags=re.IGNORECASE)
    name = re.sub(
        r'^Characteristic of pressure tendency during the three hours preceding the time of observation:\s*',
        '', name, flags=re.IGNORECASE
    )
    return name


def build_glyph_data():
    """Build complete glyph data from codepoint-map.json and SVG metadata."""
    cp_map = load_codepoint_map()

    glyph_data = []
    for sym_path, codepoint in cp_map.items():
        uni = int(codepoint) if isinstance(codepoint, str) else codepoint
        hex_code = f'U+{uni:04X}'

        # Extract category from sym_path
        # sym_path is like "symbols/CH_CloudHigh/WeatherSymbol_WMO_CloudHigh_CH_1.svg"
        rel = sym_path
        if rel.startswith("symbols/"):
            rel = rel[len("symbols/"):]
        parts = rel.split("/")
        category_dir = parts[0] if len(parts) >= 2 else ""
        filename = parts[-1] if parts else ""

        # Category label
        category_label = CATEGORY_LABELS.get(category_dir, category_dir.replace('_', ' ') if category_dir else 'Unknown')

        # Source SVG path (upstream)
        source_svg = VENDOR_DIR / sym_path

        # Extract title and desc
        title = None
        description = None
        if source_svg.exists():
            title, description = extract_title_and_desc(source_svg)

        # Extract Dublin Core metadata
        dc = {}
        if source_svg.exists():
            dc = extract_dublin_core(source_svg)

        # Pretty name from filename
        name = os.path.splitext(filename)[0]
        name = re.sub(r'^WeatherSymbol_WMO_', '', name)
        name = re.sub(r'^WeatherSymbol_ICAO_', '', name)
        name = name.replace('_', ' ')

        display_name = clean_display_name(title) if title else name

        if description:
            description = clean_display_name(description)

        entry = {
            'codepoint': f'0x{uni:04X}',
            'unicode': hex_code,
            'dec': uni,
            'name': display_name or name,
            'category': category_dir,
            'categoryLabel': category_label,
            'plane': CATEGORY_TO_PLANE.get(category_dir, ''),
            'sourcePath': sym_path,
        }

        # Store ALL Dublin Core metadata as a single object
        if dc:
            entry['metadata'] = dc

        glyph_data.append(entry)

    # Sort by codepoint
    glyph_data.sort(key=lambda x: x['dec'])

    return glyph_data


if __name__ == '__main__':
    import sys

    mode = sys.argv[1] if len(sys.argv) > 1 else 'full'
    data = build_glyph_data()

    if mode == 'index':
        # Lightweight index: unicode, category, categoryLabel, plane, name, dec
        index = [
            {
                'codepoint': g['codepoint'],
                'unicode': g['unicode'],
                'dec': g['dec'],
                'name': g['name'],
                'category': g['category'],
                'categoryLabel': g['categoryLabel'],
                'plane': g['plane'],
                'sourcePath': g.get('sourcePath', ''),
                'version': g.get('metadata', {}).get('version', ''),
                'status': g.get('metadata', {}).get('status', ''),
            }
            for g in data
        ]
        print(json.dumps(index))
    elif mode == 'metadata-only':
        # Full metadata keyed by unicode for lazy loading
        meta = {g['unicode']: g.get('metadata', {}) for g in data}
        print(json.dumps(meta))
    elif mode == 'metadata':
        # Consolidated metadata for font package: glyphs + metadata + planes
        # This is the single JSON file the website downloads and uses.
        with open(DATA_DIR / "plane-info.json") as f:
            planes = json.load(f)

        glyphs_index = [
            {
                'codepoint': g['codepoint'],
                'unicode': g['unicode'],
                'dec': g['dec'],
                'name': g['name'],
                'category': g['category'],
                'categoryLabel': g['categoryLabel'],
                'plane': g['plane'],
                'sourcePath': g.get('sourcePath', ''),
                'version': g.get('metadata', {}).get('version', ''),
                'status': g.get('metadata', {}).get('status', ''),
            }
            for g in data
        ]
        glyph_metadata = {g['unicode']: g.get('metadata', {}) for g in data}

        output = {
            'glyphs': glyphs_index,
            'metadata': glyph_metadata,
            'planes': planes,
        }
        print(json.dumps(output))
    else:
        print(json.dumps(data, indent=2))
