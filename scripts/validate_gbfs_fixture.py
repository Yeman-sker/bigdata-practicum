#!/usr/bin/env python3
"""Validate normalized station_status_event_v1 JSON fixtures."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gbfs_collector import validate_event


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.fixture.read_text())
    events = payload if isinstance(payload, list) else [payload]
    errors = {}
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            errors[str(index)] = ["event must be a JSON object"]
            continue
        event_errors = validate_event(event)
        if event_errors:
            errors[str(index)] = event_errors
    if errors:
        print(json.dumps(errors, indent=2), file=sys.stderr)
        return 1
    print(f"valid: {len(events)} station_status_event_v1 event(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
