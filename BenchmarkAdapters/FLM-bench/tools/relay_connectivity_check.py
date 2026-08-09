#!/usr/bin/env python3
"""Check basic FLM relay HTTP connectivity without using proxy variables."""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any


PROXY_ENV_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


def _clear_proxy_env() -> None:
    for name in PROXY_ENV_VARS:
        os.environ.pop(name, None)


def _check(url: str, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return {
                "ok": 200 <= int(response.status) < 500,
                "status": int(response.status),
                "duration_seconds": round(time.monotonic() - started, 3),
                "error": None,
                "body_tail": body[-1000:],
            }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": True,
            "status": int(exc.code),
            "duration_seconds": round(time.monotonic() - started, 3),
            "error": f"HTTPError: {exc.reason}",
            "body_tail": body[-1000:],
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "duration_seconds": round(time.monotonic() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
            "body_tail": "",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Relay URL to check.")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--allow-proxy",
        action="store_true",
        help="Keep proxy variables from the parent environment.",
    )
    args = parser.parse_args()

    if not args.allow_proxy:
        _clear_proxy_env()

    result = _check(args.url, args.timeout)
    payload = {
        "status": "passed" if result["ok"] else "failed",
        "url": args.url,
        "timeout_seconds": args.timeout,
        "proxy_env_used": bool(args.allow_proxy),
        "result": result,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
