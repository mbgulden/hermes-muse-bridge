"""Run the stocking monitor as ``python3 -m fleetops.stocking``."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
