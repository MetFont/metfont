#!/usr/bin/env python3
"""
Generate package README.adoc with version from font/VERSION.

Usage:
    python3 helpers/generate-package-readme.py > font/README.adoc
"""
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
VERSION_FILE = PROJECT_DIR / "font" / "VERSION"
TEMPLATE_FILE = PROJECT_DIR / "data" / "package-README.adoc"

version = "0.0.0"
try:
    version = VERSION_FILE.read_text().strip()
except FileNotFoundError:
    pass

template = TEMPLATE_FILE.read_text()
print(template.replace("{VERSION}", version))
