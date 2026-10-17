"""Fixture that requires DATABASE_URL and exits when it is missing."""

from __future__ import annotations

import os
import sys


def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print(
            "missing required environment variable DATABASE_URL",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(1)
    print("config ok", flush=True)


if __name__ == "__main__":
    main()
