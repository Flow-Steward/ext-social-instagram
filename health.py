#!/usr/bin/env python3
"""Check package availability only; credentials are not available during install."""

import json


def main() -> int:
    import main as entrypoint

    available = callable(entrypoint.main)
    print(json.dumps({"status": "healthy" if available else "failed"}))
    return 0 if available else 1


if __name__ == "__main__":
    raise SystemExit(main())
