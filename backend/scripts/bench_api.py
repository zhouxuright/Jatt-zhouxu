"""HTTP 接口压测脚本，产出 P50/P95/P99 与 RPS 基线。

用法（在 backend 目录下，服务已启动）::

    python scripts/bench_api.py --base-url http://127.0.0.1:8000 \
        --username admin --password 'xxx' --concurrency 10 --duration 20

    # 只测读接口（无需登录也能跑的 /health 之外都要 token，脚本会自动登录）
    python scripts/bench_api.py --scenario law_categories --scenario health

    # 把结果写入 JSON，便于和上一次基线对比
    python scripts/bench_api.py --json bench_baseline.json

生产环境可以直接对公网地址压测::

    python scripts/bench_api.py --base-url http://152.136.44.32/api
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

try:
    import httpx
except ImportError:  # pragma: no cover
    raise SystemExit("需要 httpx：pip install httpx")


SCENARIOS: dict[str, dict] = {
    "health": {"method": "GET", "path": "/health", "auth": False},
    "auth_me": {"method": "GET", "path": "/api/v1/auth/me", "auth": True},
    "law_categories": {"method": "GET", "path": "/api/v1/law/categories", "auth": True},
    "corpus_registry": {"method": "GET", "path": "/api/v1/admin/corpus-registry", "auth": True},
    "admin_users": {"method": "GET", "path": "/api/v1/admin/users", "auth": True},
    "law_search": {
        "method": "POST",
        "path": "/api/v1/law/search",
        "auth": True,
        "body": {"query": "劳动合同到期不续签需要支付经济补偿吗", "top_k": 5},
    },
}


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((pct / 100) * (len(ordered) - 1)))))
    return ordered[index]


async def login(client: httpx.AsyncClient, username: str, password: str) -> str:
    resp = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    if resp.status_code != 200:
        raise SystemExit(f"登录失败 {resp.status_code}: {resp.text[:300]}")
    return resp.json()["token"]["access_token"]


async def worker(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    names: list[str],
    deadline: float,
    latencies: dict[str, list[float]],
    errors: dict[str, int],
    counter: list[int],
    lock: asyncio.Lock,
) -> None:
    index = 0
    while time.perf_counter() < deadline:
        name = names[index % len(names)]
        index += 1
        spec = SCENARIOS[name]
        started = time.perf_counter()
        try:
            if spec["method"] == "GET":
                resp = await client.get(spec["path"], headers=headers if spec["auth"] else None)
            else:
                resp = await client.post(
                    spec["path"], json=spec.get("body"), headers=headers if spec["auth"] else None
                )
            elapsed = (time.perf_counter() - started) * 1000
            async with lock:
                counter[0] += 1
                if resp.status_code >= 400:
                    errors[name] = errors.get(name, 0) + 1
                else:
                    latencies.setdefault(name, []).append(elapsed)
        except Exception:  # noqa: BLE001 - 压测中任何异常都算失败
            async with lock:
                counter[0] += 1
                errors[name] = errors.get(name, 0) + 1


async def run(args: argparse.Namespace) -> dict:
    base_url = args.base_url.rstrip("/")
    timeout = httpx.Timeout(args.timeout)
    limits = httpx.Limits(max_connections=args.concurrency * 2, max_keepalive_connections=args.concurrency)

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, limits=limits) as client:
        token = args.token
        if not token and any(SCENARIOS[name]["auth"] for name in args.scenario):
            if not args.username:
                raise SystemExit("需要登录的接口必须提供 --username/--password 或 --token")
            token = await login(client, args.username, args.password)
            print(f"登录成功，token 长度 {len(token)}")

        headers = {"Authorization": f"Bearer {token}"} if token else {}
        latencies: dict[str, list[float]] = {}
        errors: dict[str, int] = {}
        counter = [0]
        lock = asyncio.Lock()

        print(f"压测开始：{base_url} | 并发 {args.concurrency} | 持续 {args.duration}s | 场景 {', '.join(args.scenario)}")
        deadline = time.perf_counter() + args.duration
        started = time.perf_counter()
        await asyncio.gather(*[
            worker(client, headers, args.scenario, deadline, latencies, errors, counter, lock)
            for _ in range(args.concurrency)
        ])
        wall = time.perf_counter() - started

    all_latencies = [value for values in latencies.values() for value in values]
    report = {
        "base_url": base_url,
        "concurrency": args.concurrency,
        "duration_s": round(wall, 2),
        "requests": counter[0],
        "rps": round(counter[0] / wall, 2) if wall else 0.0,
        "error_rate": round(sum(errors.values()) / max(counter[0], 1), 4),
        "latency_ms": {
            "p50": round(percentile(all_latencies, 50), 2),
            "p95": round(percentile(all_latencies, 95), 2),
            "p99": round(percentile(all_latencies, 99), 2),
            "avg": round(statistics.fmean(all_latencies), 2) if all_latencies else 0.0,
            "max": round(max(all_latencies), 2) if all_latencies else 0.0,
        },
        "scenarios": {
            name: {
                "requests_ok": len(values),
                "errors": errors.get(name, 0),
                "p50": round(percentile(values, 50), 2),
                "p95": round(percentile(values, 95), 2),
                "p99": round(percentile(values, 99), 2),
            }
            for name, values in latencies.items()
        },
        "errors_by_scenario": errors,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="HTTP 接口压测（P50/P95/P99 + RPS）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--token", default=None)
    parser.add_argument("--scenario", action="append", choices=sorted(SCENARIOS), default=None)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--duration", type=float, default=15.0, help="每个并发 worker 的持续秒数")
    parser.add_argument("--timeout", type=float, default=30.0, help="单请求超时秒数")
    parser.add_argument("--json", default=None, help="写入 JSON 报告路径")
    args = parser.parse_args()
    args.scenario = args.scenario or ["health", "law_categories", "corpus_registry", "law_search"]

    report = asyncio.run(run(args))

    print("\n" + "=" * 78)
    print(f"总请求 {report['requests']}  RPS {report['rps']}  错误率 {report['error_rate']:.2%}")
    print(f"P50 {report['latency_ms']['p50']}ms  P95 {report['latency_ms']['p95']}ms  "
          f"P99 {report['latency_ms']['p99']}ms  avg {report['latency_ms']['avg']}ms  max {report['latency_ms']['max']}ms")
    print("-" * 78)
    for name, stats in report["scenarios"].items():
        print(f"{name:<18} ok={stats['requests_ok']:<6} err={stats['errors']:<4} "
              f"P50={stats['p50']}ms P95={stats['p95']}ms P99={stats['p99']}ms")
    print("=" * 78)

    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告已写入 {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
