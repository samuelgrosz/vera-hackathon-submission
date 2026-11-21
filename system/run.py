#!/usr/bin/env python3
"""Graded entry point (called by system/run): <data folder> <output file> [--format csv|jsonl]."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # works under python -I

from engine.s5_batch import main  # noqa: E402

raise SystemExit(main())
