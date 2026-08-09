#!/usr/bin/env python3
"""Send two small requests to the FLM relay and report timing/status."""
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


def _request_once(
    *,
    url: str,
    api_key: str,
    model: str,
    timeout: float,
    index: int,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "input": f"Request {index}: reply exactly API_READY_{index}",
        "max_output_tokens": 16,
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {
            "request": index,
            "ok": False,
            "status": int(exc.code),
            "duration_seconds": round(time.monotonic() - started, 3),
            "error": f"HTTPError: {exc.reason}",
            "body_tail": body[-1000:],
        }
    except Exception as exc:
        return {
            "request": index,
            "ok": False,
            "status": None,
            "duration_seconds": round(time.monotonic() - started, 3),
            "error": f"{type(exc).__name__}: {exc}",
            "body_tail": "",
        }
    marker = f"API_READY_{index}"
    return {
        "request": index,
        "ok": status == 200 and marker in body,
        "status": status,
        "duration_seconds": round(time.monotonic() - started, 3),
        "error": None,
        "body_tail": body[-1000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Responses endpoint URL.")
    parser.add_argument("--model", required=True)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--count", type=int, default=2)
    parser.add_argument(
        "--allow-proxy",
        action="store_true",
        help="Keep proxy variables from the parent environment.",
    )
    args = parser.parse_args()

    if not args.allow_proxy:
        _clear_proxy_env()

    api_key = os.environ.get("UPSTREAM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": "set UPSTREAM_API_KEY or OPENAI_API_KEY",
                },
                indent=2,
            )
        )
        return 2

    started = time.monotonic()
    results = [
        _request_once(
            url=args.url,
            api_key=api_key,
            model=args.model,
            timeout=args.timeout,
            index=index,
        )
        for index in range(1, args.count + 1)
    ]
    passed = all(item["ok"] for item in results)
    print(
        json.dumps(
            {
                "status": "passed" if passed else "failed",
                "url": args.url,
                "model": args.model,
                "count": args.count,
                "timeout_seconds": args.timeout,
                "proxy_env_used": bool(args.allow_proxy),
                "total_duration_seconds": round(time.monotonic() - started, 3),
                "results": results,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
