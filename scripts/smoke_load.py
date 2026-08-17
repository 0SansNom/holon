#!/usr/bin/env python3
"""Minimal concurrent smoke against Holon /live and /ready probes.

Not a capacity or soak suite — just a cheap gate that the stack answers
under light concurrency. Exit non-zero if any request fails or latency
p95 exceeds --max-p95-ms.

Usage (stack already up):
  python3 scripts/smoke_load.py
  python3 scripts/smoke_load.py --concurrency 20 --requests 200
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

DEFAULT_TARGETS = (
    "http://localhost:8001/live",
    "http://localhost:8001/ready",
    "http://localhost:8002/live",
    "http://localhost:8003/live",
    "http://localhost:8004/live",
    "http://localhost:8005/live",
    "http://localhost:8006/live",
)


def _one(url: str, timeout: float) -> tuple[str, float, int | None, str | None]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            _ = resp.read(64)
            return url, (time.perf_counter() - started) * 1000.0, int(status), None
    except urllib.error.HTTPError as exc:
        return url, (time.perf_counter() - started) * 1000.0, int(exc.code), str(exc)
    except Exception as exc:  # noqa: BLE001 — report any probe failure
        return url, (time.perf_counter() - started) * 1000.0, None, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        action="append",
        dest="urls",
        default=None,
        help="Probe URL (repeatable). Default: Identity/Connectivity/… /live(+Identity /ready)",
    )
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--requests", type=int, default=80, help="Total probe calls across all URLs")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--max-p95-ms", type=float, default=2000.0)
    args = parser.parse_args()

    urls = args.urls or list(DEFAULT_TARGETS)
    if args.requests < 1 or args.concurrency < 1:
        print("requests and concurrency must be >= 1", file=sys.stderr)
        return 2

    plan = [urls[i % len(urls)] for i in range(args.requests)]
    results: list[tuple[str, float, int | None, str | None]] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(_one, url, args.timeout) for url in plan]
        for fut in as_completed(futures):
            results.append(fut.result())

    latencies = [ms for _, ms, _, _ in results]
    failures = [(u, st, err) for u, _, st, err in results if err is not None or st is None or st >= 400]
    p95 = sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)] if latencies else 0.0
    mean = statistics.fmean(latencies) if latencies else 0.0

    print(
        f"smoke_load: n={len(results)} concurrency={args.concurrency} "
        f"mean_ms={mean:.1f} p95_ms={p95:.1f} failures={len(failures)}"
    )
    for url, status, err in failures[:20]:
        print(f"  FAIL {url} status={status} err={err}", file=sys.stderr)

    if failures:
        return 1
    if p95 > args.max_p95_ms:
        print(f"p95 {p95:.1f}ms exceeds --max-p95-ms {args.max_p95_ms}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
