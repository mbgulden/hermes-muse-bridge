"""``python -m fleetops.regression`` entry point."""
import sys

from .run_regression import main

if __name__ == "__main__":
    sys.exit(main())
