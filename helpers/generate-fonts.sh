#!/bin/bash
# Font generation entry point
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." || exit 1
python3 helpers/generate-fonts.py "${1:-dev}"
