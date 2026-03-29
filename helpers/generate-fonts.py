#!/usr/bin/env python3
"""
Entry point for font generation.
Usage: python3 helpers/generate-fonts.py [version]
"""
import subprocess
import sys
import os

script_dir = os.path.dirname(os.path.abspath(__file__))
runner = os.path.join(script_dir, "generate-fonts-runner.py")
version = sys.argv[1] if len(sys.argv) > 1 else "dev"
build_dir = os.path.join(os.path.dirname(script_dir), "build")

# Safety net: ensure overlapping paths are combined before font build.
# This is normally done by prepare-fonts, but running generate-fonts
# directly without prepare-fonts would otherwise produce artifacts.
combine_script = os.path.join(script_dir, "combine-svg-paths.py")
result = subprocess.run(
    [sys.executable, combine_script, "--input-dir",
     os.path.join(os.path.dirname(script_dir), "src")],
    capture_output=True, text=True,
)
if result.returncode != 0:
    print(f"WARNING: combine-svg-paths.py failed:\n{result.stderr}", file=sys.stderr)
    # Continue anyway — single-path SVGs will be fine.

sys.exit(subprocess.call([sys.executable, runner, build_dir, version]))
