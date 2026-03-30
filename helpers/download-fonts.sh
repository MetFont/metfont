#!/bin/bash
# Download font artifacts from a GitHub release and extract to font/
# Usage: ./helpers/download-fonts.sh [version]
#   version: specific tag (e.g., v1.2.0). If omitted, uses the latest release.
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." || exit 1

OWNER="MetFont"
REPO="metfont"
TAG="${1:-}"

FONT_DIR="font"
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

echo "Downloading MetFont fonts from GitHub releases..."

if [ -n "$TAG" ]; then
  ASSETS_URL="https://api.github.com/repos/${OWNER}/${REPO}/releases/tags/${TAG}"
else
  ASSETS_URL="https://api.github.com/repos/${OWNER}/${REPO}/releases/latest"
fi

# Get the browser_download_url for the font zip asset
ZIP_URL=$(curl -sS "$ASSETS_URL" | \
  python3 -c "
import json, sys
data = json.load(sys.stdin)
# Find the zip asset
for asset in data.get('assets', []):
    if asset['name'].endswith('.zip'):
        print(asset['browser_download_url'])
        break
else:
    # No zip found, fall back to individual ttf/woff2 files
    print('ASSETS_URL=' + data.get('upload_url', '').split('{')[0])
    sys.exit(1)
" 2>&1) || {
  echo "ERROR: Could not find font release assets"
  echo "Make sure a release has been published at https://github.com/${OWNER}/${REPO}/releases"
  echo "ZIP_URL result: $ZIP_URL"
  exit 1
}

if [[ "$ZIP_URL" == ASSETS_URL=* ]]; then
  echo "ERROR: No zip asset found in release"
  exit 1
fi

echo "Downloading $ZIP_URL"
cd "$TMPDIR"
curl -sSL -O "$ZIP_URL"

ZIP_FILE=$(ls MetFont-*.zip 2>/dev/null | head -1)
if [ -z "$ZIP_FILE" ]; then
  echo "ERROR: Downloaded file not found"
  exit 1
fi

echo "Extracting to $FONT_DIR"
mkdir -p "$FONT_DIR"
unzip -o "$ZIP_FILE" -d "$FONT_DIR"

echo "Done. Font files installed from $ZIP_FILE"
ls "$FONT_DIR"/*/*.ttf "$FONT_DIR"/*/*.woff2 2>/dev/null | head -5
