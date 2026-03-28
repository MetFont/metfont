# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build Commands

```bash
# Full pipeline (from upstream submodule to font)
git submodule update --init          # clone vendor/WorldWeatherSymbols/
npm run prepare-sources              # assign codepoints + clean upstream SVGs -> src/ (541 files)
npm run prepare-fonts                # normalize src/ -> color/svg/ (viewBox 0 0 72 72)
npm run generate-fonts               # build 3 font formats via nanoemoji

# Individual steps
python3 helpers/assign-codepoints.py    # build data/codepoint-map.json, data/icons.json, data/plane-info.json
python3 helpers/clean-sources.py        # clean vendor/ symbols/ -> src/
python3 helpers/normalize-svg.py --input-dir src/ --mapping data/codepoint-map.json --output-dir color/svg/

# Website
npm run generate-glyphs   # regenerate site/public/glyphs.json from codepoint-map.json
npm run dev               # VitePress dev server in site/
npm run build:site        # production build in site/
```

For local font dev, `color/svg/` is already populated — just run `npm run generate-fonts`.

**Dependencies**: `pip install nanoemoji picosvg fonttools lxml`, `npm ci` in `site/`. CI uses Docker for font builds.

## Architecture

### PUA Block Allocation

7 semantic blocks in BMP PUA (U+E000–U+E7FF), 541 glyphs, 1507 free codepoints:

| Block | Range | Count | Categories |
|-------|-------|-------|------------|
| Sky & Cloud | U+E000–U+E0FF | 50 | CloudHigh, CloudLow, CloudMedium, CloudGenus, TotalCloudCover |
| Present Weather | U+E100–U+E2FF | 270 | PresentWeather, PresentWeatherAuto, PresentWeatherAdditional |
| Past Weather | U+E300–U+E3FF | 20 | PastWeather, PastWeatherAuto |
| State of Ground | U+E400–U+E4FF | 20 | StateOfGround, StateOfGroundEprime |
| Pressure | U+E500–U+E5FF | 14 | PressureTendency, PressureCentres |
| Wind & Ocean | U+E600–U+E6FF | 107 | WindArrows, SwellDirection, ShipDirection |
| Significant Weather | U+E700–U+E7FF | 60 | SigWx, Fronts |

### Source → Font Pipeline

```
vendor/WorldWeatherSymbols/symbols/  (git submodule, upstream WMO SVGs with metadata)
  → helpers/assign-codepoints.py     (builds data/codepoint-map.json, icons.json, plane-info.json)
  → helpers/clean-sources.py         (fixes SVG errors, writes cleaned files to src/)
  → helpers/normalize-svg.py         (picosvg stroke-to-fill, viewBox → 0 0 72 72)
  → color/svg/                       (541 SVGs with hexcode filenames, e.g. E000.svg)
    → nanoemoji (3 formats) → ttx name injection → woff2_compress → font/
```

### Source Cleaning (`helpers/clean-sources.py`)

The upstream WMO SVGs have several categories of errors that this script fixes:
1. **Stray elements**: Path coordinates far outside viewBox (e.g., x=-115 in a 0-55 viewBox). Causes symbols to render tiny. Found in 5 SVGs (TropicalCyclone variants).
2. **`<text>` elements**: Digit glyphs in w1w1 symbols use `<text>` instead of `<path>`. Converted to `<path>` using fontTools RecordingPen. Found in 24 SVGs.
3. **`<marker>`/`<defs>`**: Not supported by picosvg. Stripped. Found in 25 SVGs.
4. **CSS units**: `1pt`, `1px` values in attributes. Converted to bare numbers. Found in 61 SVGs.
5. **Dublin Core metadata**: `dc:`, `cc:`, `rdf:`, `xml:` namespace elements stripped.
6. **Empty `<g>` elements**: Removed.
7. **Namespace normalization**: Some SVGs use `svg:` prefix but child elements have no namespace — must normalize all into SVG namespace.

### Key Data Files

- **`data/codepoint-map.json`** — Auto-generated: `symbols/{Category}/{Filename}.svg` → PUA codepoint. Bridge between upstream symbol filenames and font codepoints.
- **`data/icons.json`** — Font config: 541 glyphs, PUA `0xE000`–`0xE73B`, paths to `color/svg/`, font metadata (UPM=1024).
- **`data/plane-info.json`** — Per-plane metadata (WMO source, code table URLs, creator, license, sub-categories). Merged with `category-descriptions.json` by `assign-codepoints.py`.
- **`data/category-descriptions.json`** — Extensible per-plane descriptions, WMO document references, usage examples (SYNOP/METAR/TAF/SIGWX), code table details, and per-sub-category descriptions. **Open/closed**: add new keys at any level and the site renderer will pass them through.
- **`data/upstream-icons.json`** — Original upstream reference.
- **`data/MetFont.ttx`** — Name table overlay injected post-build.

### Font Formats

| Format | Description | Size |
|--------|-------------|------|
| `glyf` | Monochrome TrueType | 42 KB |
| `glyf_colr_1` | COLRv1 + CPAL color | 53 KB |
| `picosvgz` | SVG table | 71 KB |

Font metrics: UPM=1024, ascender=1045, descender=-275.

### Website (`site/`)

VitePress SPA with Tailwind CSS. Hash-based routing:
- `#glyph/U+XXXX` — per-glyph detail page with codepoint info, text preview, related symbols
- `#plane/{slug}` — per-category detail page with WMO references, code tables, usage examples, and glyph grid
- `#about` — about page with links to category planes
- `#download` — download page

Font served as `MetFont` from `/MetFont-glyf.woff2`.

## Key Pitfalls

- **nanoemoji TOML glob bug**: Use CLI positional args, not TOML `srcs` glob.
- **picosvg viewBox cancellation**: Call `topicosvg()` FIRST, THEN inject `<g>` transform wrapper.
- **SVG path arc commands**: Arc (A/a) has 7 params (rx, ry, rotation, large-arc, sweep, x, y) — only last 2 are coordinates. Naive `findall(r'\d+')` gives wrong bounds.
- **`<use>` elements**: WWS SVGs have `<use>` without `href`. Must remove before picosvg.
- **`src/` is gitignored**: Generated from `vendor/WorldWeatherSymbols/symbols/` via `clean-sources.py`. Not committed.
- **Tropical cyclone scaling**: Symbols in `TROPICAL_CYCLONE_CODEPOINTS` are scaled 0.5x centered in `normalize-svg.py`.
- **Python imports**: `helpers/__init__.py` exists but blocks package imports — use `sys.path.insert(0, 'helpers/')` and `from module import ...`.
