"""Entry point for `python -m empire`. Dispatches to `empire.cli`."""

from empire.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
