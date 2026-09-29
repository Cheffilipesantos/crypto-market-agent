"""Historical candle collector + runner for structural replay.

This is an offline research command. It never imports agent.py, never sends orders,
and never writes PAPER positions. Data comes from Hyperliquid public candleSnapshot.
"""

import argparse
import json
import time
from pathlib import Path

import requests

import structural_replay


HL_URL = "https://api.hyperliquid.xyz/info"
TIMEFRAMES = ("4h", "2h", "1h", "15m", "5m", "1m")
TF_MS = structural_replay.TF_MS


def fetch_candles(asset, timeframe, start_ms, end_ms, timeout=20):
    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": asset,
            "interval": timeframe,
            "startTime": int(start_ms),
            "endTime": int(end_ms),
        },
    }
    response = requests.post(HL_URL, json=payload, timeout=timeout)
    response.raise_for_status()
    raw = response.json()
    return [
        {
            "time": int(c["t"]),
            "open": float(c["o"]),
            "high": float(c["h"]),
            "low": float(c["l"]),
            "close": float(c["c"]),
            "volume": float(c["v"]),
        }
        for c in raw
    ]


def collect(asset, start_ms, end_ms, warmup_bars=220):
    data = {}
    for timeframe in TIMEFRAMES:
        warmup_start = int(start_ms) - TF_MS[timeframe] * int(warmup_bars)
        data[timeframe] = fetch_candles(
            asset,
            timeframe,
            warmup_start,
            end_ms,
        )
    return data


def decision_points(candles_1m, start_ms, end_ms, step_minutes=5):
    step_ms = int(step_minutes) * 60_000
    first = ((int(start_ms) + step_ms - 1) // step_ms) * step_ms
    available = {
        int(c["time"]) + TF_MS["1m"]
        for c in candles_1m
        if int(start_ms) <= int(c["time"]) + TF_MS["1m"] <= int(end_ms)
    }
    return [t for t in range(first, int(end_ms) + 1, step_ms) if t in available]


def run(asset, start_ms, end_ms, step_minutes=5):
    candles = collect(asset, start_ms, end_ms)
    points = decision_points(candles["1m"], start_ms, end_ms, step_minutes)
    results = [
        structural_replay.replay_decision(candles, point)
        for point in points
    ]
    return {
        "asset": asset,
        "start_ms": int(start_ms),
        "end_ms": int(end_ms),
        "step_minutes": int(step_minutes),
        "summary": structural_replay.summarize(results),
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", required=True)
    parser.add_argument("--start-ms", required=True, type=int)
    parser.add_argument("--end-ms", type=int, default=None)
    parser.add_argument("--step-minutes", type=int, default=5)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    end_ms = args.end_ms or int(time.time() * 1000)
    report = run(
        args.asset.upper(),
        args.start_ms,
        end_ms,
        args.step_minutes,
    )

    output = args.output or (
        "replay_%s_%s_%s.json"
        % (args.asset.lower(), args.start_ms, end_ms)
    )
    Path(output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print("REPORT:", output)


if __name__ == "__main__":
    main()
