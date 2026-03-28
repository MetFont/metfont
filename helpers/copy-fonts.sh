#!/bin/bash
# Copy built font files from font/ to site/public/
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." || exit 1

FONT_DIR="font"
PUBLIC_DIR="site/public"

if [ ! -d "$FONT_DIR" ]; then
  echo "ERROR: font/ directory not found. Run 'npm run generate-fonts' first."
  exit 1
fi

mkdir -p "$PUBLIC_DIR"

cp -f "$FONT_DIR/MetFont-glyf/MetFont-glyf.woff2" "$PUBLIC_DIR/"
cp -f "$FONT_DIR/MetFont-glyf/MetFont-glyf.ttf" "$PUBLIC_DIR/"
cp -f "$FONT_DIR/MetFont-glyf_colr_1/MetFont-glyf_colr_1.ttf" "$PUBLIC_DIR/"
cp -f "$FONT_DIR/MetFont-picosvgz/MetFont-picosvgz.ttf" "$PUBLIC_DIR/"

echo "Copied 4 font files to $PUBLIC_DIR/"
