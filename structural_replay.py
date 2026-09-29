"""Deterministic, no-lookahead replay for the structural Shadow.

Input candles must already be historical OHLCV. This module never downloads data.
At each decision timestamp it exposes only candles whose close time is known, builds
the same multi-timeframe structure used live, asks Shadow for a plan, then resolves
that virtual trade using strictly later 1m candles.
"""

import market_structure
import shadow_engine


TF_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "2h": 2 * 60 * 60_000,
    "4h": 4 * 60 * 60_000,
}

REQUIRED_TFS = ("4h", "2h", "1h", "15m", "5m", "1m")


def candle_close_ms(candle, timeframe):
    """Hyperliquid candle time is treated as candle-open time."""
    return int(candle["time"]) + TF_MS[timeframe]


def visible_candles(candles, timeframe, decision_ms, max_bars=220):
    """Only candles fully closed by decision_ms are visible to the strategy."""
    visible = [
        candle for candle in candles
        if candle_close_ms(candle, timeframe) <= int(decision_ms)
    ]
    return visible[-max_bars:]


def build_context_at(candles_by_tf, decision_ms, max_bars=220):
    sliced = {
        tf: visible_candles(candles_by_tf.get(tf, []), tf, decision_ms, max_bars)
        for tf in REQUIRED_TFS
    }
    mtf = market_structure.analyze_multitimeframe(sliced)
    return {
        "mtf": mtf,
        "interpretation": market_structure.interpret_multitimeframe(mtf),
    }, sliced


def resolve_plan(plan, future_1m):
    """Resolve one Shadow plan with the same conservative ambiguity rule as PAPER."""
    if plan.get("decision") not in ("LONG", "SHORT"):
        return None

    side = plan["side"]
    stop = float(plan["stop"])
    t1 = float(plan["t1"])
    t2 = float(plan["t2"])
    t3 = float(plan["t3"])
    hit_t1 = False
    hit_t2 = False
    mfe_r = 0.0
    mae_r = 0.0
    risk = abs(float(plan["entry"]) - stop)
    if risk <= 0:
        return None

    for candle in future_1m:
        high = float(candle["high"])
        low = float(candle["low"])

        if side == "LONG":
            stop_hit = low <= stop
            t1_hit = high >= t1
            t2_hit = high >= t2
            t3_hit = high >= t3
            favorable = high - float(plan["entry"])
            adverse = float(plan["entry"]) - low
        else:
            stop_hit = high >= stop
            t1_hit = low <= t1
            t2_hit = low <= t2
            t3_hit = low <= t3
            favorable = float(plan["entry"]) - low
            adverse = high - float(plan["entry"])

        mfe_r = max(mfe_r, favorable / risk)
        mae_r = max(mae_r, adverse / risk)

        # Same candle contains both terminal outcomes: ordering is unknowable.
        if stop_hit and t3_hit:
            return {
                "status": "AMBIGUOUS",
                "exit_reason": "AMBIGUOUS",
                "result_r": 0.0,
                "mfe_r": mfe_r,
                "mae_r": mae_r,
            }

        if stop_hit:
            return {
                "status": "CLOSED",
                "exit_reason": "STOP",
                "result_r": -1.0,
                "mfe_r": mfe_r,
                "mae_r": mae_r,
                "hit_t1": hit_t1 or t1_hit,
                "hit_t2": hit_t2 or t2_hit,
            }

        hit_t1 = hit_t1 or t1_hit
        hit_t2 = hit_t2 or t2_hit

        if t3_hit:
            return {
                "status": "CLOSED",
                "exit_reason": "T3",
                "result_r": 3.0,
                "mfe_r": mfe_r,
                "mae_r": mae_r,
                "hit_t1": True,
                "hit_t2": True,
            }

    return {
        "status": "OPEN",
        "exit_reason": None,
        "result_r": None,
        "mfe_r": mfe_r,
        "mae_r": mae_r,
        "hit_t1": hit_t1,
        "hit_t2": hit_t2,
    }


def replay_decision(candles_by_tf, decision_ms, max_bars=220):
    """Replay exactly one historical decision point without future leakage."""
    context, sliced = build_context_at(candles_by_tf, decision_ms, max_bars)
    one_minute = sliced["1m"]
    if not one_minute:
        return {
            "decision_ms": int(decision_ms),
            "plan": {
                "decision": "WAIT",
                "side": "NONE",
                "setup_type": "REPLAY_NO_1M_DATA",
                "reason": "Sem candle 1m fechado no instante da decisão.",
                "evidence": {},
            },
            "outcome": None,
        }

    entry = float(one_minute[-1]["close"])
    plan = shadow_engine.build_structural_plan(
        {"price": entry},
        context,
    )

    future = [
        candle for candle in candles_by_tf.get("1m", [])
        if int(candle["time"]) >= int(decision_ms)
    ]

    return {
        "decision_ms": int(decision_ms),
        "entry": entry,
        "plan": plan,
        "outcome": resolve_plan(plan, future),
        "context": context,
    }


def summarize(results):
    plans = [r for r in results if r.get("plan")]
    trades = [
        r for r in plans
        if r["plan"].get("decision") in ("LONG", "SHORT")
    ]
    closed = [
        r for r in trades
        if (r.get("outcome") or {}).get("status") == "CLOSED"
    ]
    ambiguous = [
        r for r in trades
        if (r.get("outcome") or {}).get("status") == "AMBIGUOUS"
    ]
    room_rejected = [
        r for r in plans
        if r["plan"].get("setup_type") == "STRUCTURAL_ROOM_REJECTED"
    ]
    total_r = sum(float(r["outcome"]["result_r"]) for r in closed)

    return {
        "decision_points": len(results),
        "shadow_trades": len(trades),
        "closed_trades": len(closed),
        "ambiguous_excluded": len(ambiguous),
        "room_rejected": len(room_rejected),
        "wins": sum(1 for r in closed if r["outcome"]["result_r"] > 0),
        "losses": sum(1 for r in closed if r["outcome"]["result_r"] < 0),
        "total_r": total_r,
        "avg_r": total_r / len(closed) if closed else 0.0,
    }
