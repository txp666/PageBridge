#!/usr/bin/env python3
"""Prepare PageBridge's optional local MinerU runtime, outside the app bundle."""
import argparse
import asyncio
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--uv", type=Path)
    args = parser.parse_args()
    if args.runtime:
        os.environ["PAGEBRIDGE_MINERU_RUNTIME"] = str(args.runtime.expanduser().resolve())
    if args.uv:
        os.environ["PAGEBRIDGE_UV"] = str(args.uv.expanduser().resolve())
    import structured_reading
    class Events:
        def emit(self, event_type, **fields):
            print(json.dumps(dict(type=event_type, **fields), ensure_ascii=False), flush=True)
    try:
        asyncio.run(structured_reading.prepare(Events()))
    except structured_reading.ReadingError as error:
        print(json.dumps(dict(type="error", message=str(error)), ensure_ascii=False), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
