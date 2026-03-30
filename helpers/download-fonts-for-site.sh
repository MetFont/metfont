#!/bin/bash
# Download font artifacts from the latest GitHub release for PR builds.
# Falls back to local build if no release is available.
set -euo pipefail

OWNER="MetFont"
REPO="metfont"
ASSETS_URL="https://api.github.com/repos/${OWNER}/${REPO}/releases/latest"

ZIP_URL=$(curl -sS "$ASSETS_URL" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for asset in data.get('assets', []):
    if asset['name'].endswith('.zip'):
        print(asset['browser_download_url'])
        break
" 2>&1) || {
  echo "No release found — will build fonts locally"
  exit 1
}

if [ -z "$ZIP_URL" ]; then
  echo "No release found — will build fonts locally"
  exit 1
fi

echo "Downloading fonts from release: $ZIP_URL"
curl -sSL "$ZIP_URL" -o /tmp/metfont-fonts.zip
mkdir -p font
unzip -o /tmp/metfont-fonts.zip -d font
npm run copy-fonts
echo "Fonts downloaded from release"