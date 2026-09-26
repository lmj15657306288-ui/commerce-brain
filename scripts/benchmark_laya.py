#!/usr/bin/env python3
"""Measure local Laya transport/schema reliability, not business accuracy."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

from decision_provider import LayaProvider
from laya_client import LayaClient


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "laya"
DEFAULT_KEY_FILE = Path.home() / ".local/share/commerce-laya/config/api-key"
ACTION_LABELS = {
    "OBSERVE": "Continue monitoring.",
    "PROMPT_HOST": "Host messaging needs operator review.",
    "CHECK_PRODUCT": "Product/card performance needs operator review.",
    "CHECK_CAMPAIGN": "Paid traffic needs operator review.",
    "ESCALATE_SLOW_BRAIN": "Ambiguous or complex; request slow analysis.",
}


def questions() -> dict[str, dict[str, Any]]:
    return {
        "next_action": {
            "type": "choice",
            "instructions": "Choose the safest next operational action. Do not execute anything.",
            "criteria": ACTION_LABELS,
        },
        "actionable": {
            "type": "noul",
            "instructions": "Is an operator review needed now?",
        },
        "priority": {
            "type": "score",
            "instructions": "How urgent is the safest operator review?",
            "criteria": [
                "no immediate operational concern",
                "low priority review",
                "operator review recommended",
                "high priority operator review",
            ],
        },
    }


def load_fixtures() -> list[dict[str, Any]]:
    fixtures = []
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("state"), dict):
            raise ValueError(f"invalid fixture: {path.name}")
        fixtures.append(value)
    if len(fixtures) < 9:
        raise ValueError("expected at least nine Laya fixtures")
    return fixtures


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return round(ordered[index], 2)


def resident_memory_kb() -> int | None:
    try:
        rows = subprocess.check_output(
            ["ps", "-axo", "pid=,rss=,command="],
            text=True,
            stderr=subprocess.DEVNULL,
        ).splitlines()
    except (OSError, subprocess.SubprocessError):
        return None
    rss = []
    for row in rows:
        fields = row.strip().split(None, 2)
        if len(fields) == 3 and ("laya.serve" in fields[2] or "laya-serve" in fields[2]):
            try:
                rss.append(int(fields[1]))
            except ValueError:
                continue
    return max(rss) if rss else None


class CountingClient:
    def __init__(self, client: LayaClient):
        self.client = client
        self.errors = 0

    @property
    def last_latency_ms(self) -> int:
        return self.client.last_latency_ms

    @property
    def timeout_seconds(self) -> float:
        return self.client.timeout_seconds

    @timeout_seconds.setter
    def timeout_seconds(self, value: float) -> None:
        self.client.timeout_seconds = value

    def systemone(self, **kwargs: Any) -> dict[str, Any]:
        try:
            return self.client.systemone(**kwargs)
        except Exception:
            self.errors += 1
            raise


def measure_cold_start(args: argparse.Namespace, key: str) -> float | None:
    if args.skip_cold_start:
        return None
    port = 18766
    env = {
        **os.environ,
        "LAYA_HOST": "127.0.0.1",
        "LAYA_PORT": str(port),
        "LAYA_PRELOAD": "1",
        "LAYA_MODELS": "typed-decisions",
        "LAYA_DEVICE": args.device,
        "LAYA_THREADS": "4",
        "LAYA_API_KEY": key,
    }
    command = [args.laya_python, "-m", "laya.serve"]
    started = time.perf_counter()
    process = subprocess.Popen(
        command,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    client = LayaClient(
        base_url=f"http://127.0.0.1:{port}",
        api_key=key,
        timeout_seconds=1.0,
    )
    try:
        deadline = time.monotonic() + args.cold_timeout_seconds
        while time.monotonic() < deadline:
            try:
                health = client.health()
                if health.status == "ok" and "typed-decisions" in health.loaded:
                    return round((time.perf_counter() - started) * 1000, 2)
            except Exception:
                time.sleep(0.1)
        return None
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def run(args: argparse.Namespace) -> dict[str, Any]:
    key = Path(args.api_key_file).expanduser().read_text(encoding="utf-8").strip()
    fixtures = load_fixtures()
    client = CountingClient(
        LayaClient(
            base_url=args.base_url,
            api_key=key,
            timeout_seconds=args.timeout_seconds,
        )
    )
    provider = LayaProvider(
        client=client,
        timeout_ms=round(args.timeout_seconds * 1000),
        min_confidence=args.min_confidence,
    )
    latencies: list[float] = []
    schema_valid = 0
    service_success = 0
    provider_fallback = 0
    choices: dict[str, int] = {}
    started = time.perf_counter()
    for index in range(args.count):
        fixture = fixtures[index % len(fixtures)]
        before = client.errors
        request_started = time.perf_counter()
        result = provider.decide({"metrics": fixture["state"]})
        latencies.append((time.perf_counter() - request_started) * 1000)
        if client.errors == before:
            service_success += 1
        if result.get("provider") == "laya":
            schema_valid += 1
        else:
            provider_fallback += 1
        choices[result["choice"]] = choices.get(result["choice"], 0) + 1
    elapsed = time.perf_counter() - started
    cold_start_ms = measure_cold_start(args, key)
    return {
        "benchmark_version": 1,
        "count": args.count,
        "fixture_count": len(fixtures),
        "base_url": args.base_url,
        "service_success_rate": round(service_success / args.count, 4),
        "schema_valid_rate": round(schema_valid / args.count, 4),
        "fallback_rate": round(provider_fallback / args.count, 4),
        "client_error_count": client.errors,
        "p50_ms": percentile(latencies, 0.50),
        "p95_ms": percentile(latencies, 0.95),
        "p99_ms": percentile(latencies, 0.99),
        "warm_first_request_ms": round(latencies[0], 2) if latencies else None,
        "cold_start_ms": cold_start_ms,
        "ram_rss_mb": round((resident_memory_kb() or 0) / 1024, 2) if resident_memory_kb() else None,
        "elapsed_seconds": round(elapsed, 2),
        "throughput_requests_per_second": round(args.count / elapsed, 2) if elapsed else None,
        "choice_counts": choices,
        "accuracy_claim": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--api-key-file", default=str(DEFAULT_KEY_FILE))
    parser.add_argument("--laya-python", default=str(Path.home() / ".local/share/commerce-laya/.venv/bin/python"))
    parser.add_argument("--device", default="mps")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--min-confidence", type=float, default=0.2)
    parser.add_argument("--skip-cold-start", action="store_true")
    parser.add_argument("--cold-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--output")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    report = run(args)
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).expanduser().write_text(encoded, encoding="utf-8")
    print(encoded, end="")
