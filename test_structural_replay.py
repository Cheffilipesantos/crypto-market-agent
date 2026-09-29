from structural_replay import visible_candles, resolve_plan, summarize


def candle(t, o, h, l, c):
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": 1}


# A 5m candle opened at t=0 is not visible until its close at t=300000.
five = [
    candle(0, 100, 101, 99, 100),
    candle(300_000, 100, 150, 50, 120),  # future candle must not leak
]
assert visible_candles(five, "5m", 299_999) == []
assert len(visible_candles(five, "5m", 300_000)) == 1
assert len(visible_candles(five, "5m", 599_999)) == 1
assert len(visible_candles(five, "5m", 600_000)) == 2


short_plan = {
    "decision": "SHORT",
    "side": "SHORT",
    "entry": 100.0,
    "stop": 102.0,
    "t1": 98.0,
    "t2": 96.0,
    "t3": 94.0,
}

# Normal short progression reaches T3 for +3R.
future = [
    candle(60_000, 100, 101, 97.5, 98.5),
    candle(120_000, 98.5, 99, 95.5, 96.5),
    candle(180_000, 96.5, 97, 93.5, 94.0),
]
out = resolve_plan(short_plan, future)
assert out["exit_reason"] == "T3"
assert out["result_r"] == 3.0
assert out["hit_t1"] is True
assert out["hit_t2"] is True


# Stop before target is -1R.
stopped = [candle(60_000, 100, 102.1, 99.5, 101.5)]
out = resolve_plan(short_plan, stopped)
assert out["exit_reason"] == "STOP"
assert out["result_r"] == -1.0


# If the same 1m candle contains stop and any newly reached target,
# ordering is unknowable from OHLC alone.
ambiguous_t1 = [candle(60_000, 100, 102.1, 97.9, 100)]
out = resolve_plan(short_plan, ambiguous_t1)
assert out["status"] == "AMBIGUOUS"
assert out["result_r"] == 0.0


# If the same 1m candle contains stop and T3, ordering is unknowable.
ambiguous = [candle(60_000, 100, 102.1, 93.9, 100)]
out = resolve_plan(short_plan, ambiguous)
assert out["status"] == "AMBIGUOUS"
assert out["result_r"] == 0.0


summary = summarize([
    {"plan": short_plan, "outcome": {"status": "CLOSED", "result_r": 3.0}},
    {"plan": short_plan, "outcome": {"status": "CLOSED", "result_r": -1.0}},
    {"plan": short_plan, "outcome": {"status": "AMBIGUOUS", "result_r": 0.0}},
    {
        "plan": {
            "decision": "NO_TRADE",
            "setup_type": "STRUCTURAL_ROOM_REJECTED",
        },
        "outcome": None,
    },
])
assert summary["shadow_trades"] == 3
assert summary["closed_trades"] == 2
assert summary["ambiguous_excluded"] == 1
assert summary["room_rejected"] == 1
assert summary["wins"] == 1
assert summary["losses"] == 1
assert summary["total_r"] == 2.0
assert summary["avg_r"] == 1.0

print("STRUCTURAL REPLAY TESTS: OK")
