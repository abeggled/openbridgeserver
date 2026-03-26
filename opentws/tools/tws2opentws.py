"""
TWS → OpenTWS Migration CLI Tool — Phase 6 (stub)

Usage:
  python -m opentws.tools.tws2opentws tws_export.xml -o opentws_config.json

Then import via: POST /api/v1/config/import
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert a TWS XML export to OpenTWS JSON config"
    )
    parser.add_argument("input", help="Path to tws_export.xml")
    parser.add_argument("-o", "--output", default="opentws_config.json")
    args = parser.parse_args(argv)

    # TODO Phase 6
    print(f"[tws2opentws] Input:  {args.input}")
    print(f"[tws2opentws] Output: {args.output}")
    print("[tws2opentws] Not yet implemented — Phase 6")
    return 1


if __name__ == "__main__":
    sys.exit(main())
