"""Entry point for the `vera` command (see ./vera and ./vera.cmd)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vera.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
