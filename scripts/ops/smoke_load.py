from __future__ import annotations

import argparse
import asyncio
import statistics
import time

import httpx


async def run(base_url: str, requests: int, concurrency: int) -> int:
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    failures = 0

    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        async def one() -> None:
            nonlocal failures
            async with semaphore:
                started = time.perf_counter()
                try:
                    response = await client.get('/hackathons', params={'limit': 20})
                    if response.status_code >= 400:
                        failures += 1
                except httpx.HTTPError:
                    failures += 1
                finally:
                    latencies.append((time.perf_counter() - started) * 1000)

        await asyncio.gather(*(one() for _ in range(requests)))

    ordered = sorted(latencies)
    p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)] if ordered else float('inf')
    print({'requests': requests, 'failures': failures, 'median_ms': round(statistics.median(latencies), 2), 'p95_ms': round(p95, 2)})
    return 0 if failures == 0 and p95 < 500 else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://localhost:8000')
    parser.add_argument('--requests', type=int, default=200)
    parser.add_argument('--concurrency', type=int, default=20)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.base_url, args.requests, args.concurrency)))
