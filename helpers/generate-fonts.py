#!/usr/bin/env python3
"""
Entry point for font generation.
Usage: python3 helpers/generate-fonts.py [version]

Note: SVG preparation (combine-svg-paths + normalize-svg) is done by
the prepare-fonts step before this script is called.
"""
import subprocess
import sys
import os

script_dir = os.path.dirname(os.path.abspath(__file__))
runner = os.path.join(script_dir, "generate-fonts-runner.py")
version = sys.argv[1] if len(sys.argv) > 1 else "dev"
build_dir = os.path.join(os.path.dirname(script_dir), "build")

sys.exit(subprocess.call([sys.executable, runner, build_dir, version]))
