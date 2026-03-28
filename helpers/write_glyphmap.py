#!/usr/bin/env python3
"""
Custom glyphmap generator for nanoemoji.
Maps SVG filenames (e.g., E900.svg) to PUA codepoints (e.g., 0xE900).

Usage: Called by nanoemoji with list of SVG filenames as arguments.
Output: CSV rows of filename,codepoint,glyph_name to stdout.
"""
import csv
import os
import sys


def main():
    writer = csv.writer(sys.stdout)
    for filepath in sys.argv[1:]:
        basename = os.path.basename(filepath)
        hexcode = os.path.splitext(basename)[0]
        try:
            codepoint = int(hexcode, 16)
        except ValueError:
            continue
        glyph_name = f"u{hexcode.upper()}"
        writer.writerow([filepath, f"0x{codepoint:04X}", glyph_name])


if __name__ == "__main__":
    main()
