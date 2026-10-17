"""Starts and stays alive without serving anything."""

from __future__ import annotations

import time


def main() -> None:
    print("silent-app started", flush=True)
    time.sleep(120)


if __name__ == "__main__":
    main()
