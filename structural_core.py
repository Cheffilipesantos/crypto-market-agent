"""Canonical structural market-reading primitives.

Experimental only. This module never opens/closes positions and never changes
Official leverage rules. It turns closed OHLCV candles into volatility-aware
price-action evidence for Shadow/Candidate strategies.
"""

from statistics import median


def _f(candle, key):
    return float(candle[key])


def true_range(candle, previous_close=None):
    high = _f(candle, "high")
    low = _f(candle, "low")
    if previous_close is None:
        return max(0.0, high - low)
    previous_close = float(previous_close)
    return max(
        high - low,
        abs(high - previous_close),
        abs(low - previous_close),
    )


def atr(candles, period=14):
    if not candles:
        return 0.0
    sample = candles[-(period + 1):]
    values = []
    previous_close = None
    for candle in sample:
        values.append(true_range(candle, previous_close))
        previous_close = _f(candle, "close")
    values = values[-period:]
    return sum(values) / len(values) if values else 0.0


def adaptive_tolerance_pct(
    candles,
    atr_period=14,
    atr_fraction=0.35,
    min_pct=0.0005,
    max_pct=0.01,
):
    """Price-zone tolerance scaled by recent volatility, with safety bounds."""
    if not candles:
        return min_pct
    price = _f(candles[-1], "close")
    if price <= 0:
        return min_pct
    raw = (atr(candles, atr_period) / price) * atr_fraction
    return max(min_pct, min(max_pct, raw))


def candle_anatomy(candle, atr_value=None):
    o = _f(candle, "open")
    h = _f(candle, "high")
    l = _f(candle, "low")
    c = _f(candle, "close")
    rng = max(0.0, h - l)
    body = abs(c - o)
    upper = max(0.0, h - max(o, c))
    lower = max(0.0, min(o, c) - l)
    close_position = (c - l) / rng if rng else 0.5
    body_ratio = body / rng if rng else 0.0
    return {
        "time": candle.get("time"),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": float(candle.get("volume", 0.0)),
        "direction": "BULLISH" if c > o else "BEARISH" if c < o else "DOJI",
        "range": rng,
        "body": body,
        "body_ratio": body_ratio,
        "upper_wick_ratio": upper / rng if rng else 0.0,
        "lower_wick_ratio": lower / rng if rng else 0.0,
        "close_position": close_position,
        "range_atr": (rng / atr_value) if atr_value else None,
    }


def detect_raw_pivots(candles, left=2, right=2):
    pivots = []
    if len(candles) < left + right + 1:
        return pivots
    for i in range(left, len(candles) - right):
        cur = candles[i]
        left_part = candles[i-left:i]
        right_part = candles[i+1:i+1+right]
        if all(_f(cur, "high") > _f(x, "high") for x in left_part) and all(
            _f(cur, "high") >= _f(x, "high") for x in right_part
        ):
            pivots.append({
                "index": i, "time": cur.get("time"),
                "type": "HIGH", "price": _f(cur, "high"),
            })
        if all(_f(cur, "low") < _f(x, "low") for x in left_part) and all(
            _f(cur, "low") <= _f(x, "low") for x in right_part
        ):
            pivots.append({
                "index": i, "time": cur.get("time"),
                "type": "LOW", "price": _f(cur, "low"),
            })
    return sorted(pivots, key=lambda x: (x["index"], 0 if x["type"] == "LOW" else 1))


def normalize_pivots(pivots):
    """Force alternating HIGH/LOW pivots, retaining the more extreme duplicate."""
    result = []
    for pivot in pivots:
        item = dict(pivot)
        if not result or result[-1]["type"] != item["type"]:
            result.append(item)
            continue
        previous = result[-1]
        replace = (
            item["type"] == "HIGH" and item["price"] >= previous["price"]
        ) or (
            item["type"] == "LOW" and item["price"] <= previous["price"]
        )
        if replace:
            result[-1] = item
    return result


def classify_pivots(pivots, tolerance_pct):
    result = []
    previous = {"HIGH": None, "LOW": None}
    for pivot in pivots:
        item = dict(pivot)
        old = previous[item["type"]]
        if old is None:
            label = "H" if item["type"] == "HIGH" else "L"
        else:
            change = (item["price"] - old) / old
            if item["type"] == "HIGH":
                label = "HH" if change > tolerance_pct else "LH" if change < -tolerance_pct else "EH"
            else:
                label = "HL" if change > tolerance_pct else "LL" if change < -tolerance_pct else "EL"
        item["structure"] = label
        previous[item["type"]] = item["price"]
        result.append(item)
    return result


def structure_state(pivots):
    highs = [p for p in pivots if p.get("structure") in ("HH", "LH", "EH")]
    lows = [p for p in pivots if p.get("structure") in ("HL", "LL", "EL")]
    high = highs[-1]["structure"] if highs else None
    low = lows[-1]["structure"] if lows else None
    if high == "HH" and low in ("HL", "EL"):
        state = "BULLISH_STRUCTURE"
    elif high in ("LH", "EH") and low == "LL":
        state = "BEARISH_STRUCTURE"
    elif high is None or low is None:
        state = "UNDEFINED"
    else:
        state = "TRANSITION_STRUCTURE"
    return {"state": state, "high_structure": high, "low_structure": low}


def detect_structure_breaks(candles, pivots, tolerance_pct):
    """Detect BOS/CHOCH using only structure known before each break candle.

    This deliberately avoids future leakage: the structural state for candle i
    is calculated only from pivots whose index is strictly earlier than i.
    """
    events = []
    broken = set()
    if len(candles) < 2 or not pivots:
        return events

    for i in range(1, len(candles)):
        prior = [p for p in pivots if int(p["index"]) < i]
        if not prior:
            continue

        prior_state = structure_state(prior)["state"]
        previous_close = _f(candles[i - 1], "close")
        close = _f(candles[i], "close")
        highs = [p for p in prior if p["type"] == "HIGH"]
        lows = [p for p in prior if p["type"] == "LOW"]
        crossings = []

        if highs:
            pivot = highs[-1]
            level = float(pivot["price"])
            upper = level * (1 + tolerance_pct)
            if previous_close <= upper and close > upper:
                crossings.append(("UP", pivot, level))

        if lows:
            pivot = lows[-1]
            level = float(pivot["price"])
            lower = level * (1 - tolerance_pct)
            if previous_close >= lower and close < lower:
                crossings.append(("DOWN", pivot, level))

        for direction, pivot, level in crossings:
            key = (int(pivot["index"]), direction)
            if key in broken:
                continue

            if direction == "UP":
                if prior_state == "BEARISH_STRUCTURE":
                    kind = "CHOCH"
                elif prior_state == "BULLISH_STRUCTURE":
                    kind = "BOS"
                else:
                    kind = "STRUCTURE_BREAK"
            else:
                if prior_state == "BULLISH_STRUCTURE":
                    kind = "CHOCH"
                elif prior_state == "BEARISH_STRUCTURE":
                    kind = "BOS"
                else:
                    kind = "STRUCTURE_BREAK"

            events.append({
                "kind": kind,
                "direction": direction,
                "level": level,
                "pivot_index": int(pivot["index"]),
                "break_index": i,
                "time": candles[i].get("time"),
                "close": close,
                "prior_state": prior_state,
            })
            broken.add(key)

    return events


def cluster_levels(pivots, tolerance_pct, min_touches=2):
    clusters = []
    for pivot in pivots:
        price = float(pivot["price"])
        candidates = [
            c for c in clusters
            if abs(price - c["center"]) / c["center"] <= tolerance_pct
        ]
        if candidates:
            cluster = min(candidates, key=lambda c: abs(price - c["center"]))
            cluster["prices"].append(price)
            cluster["pivots"].append(pivot)
            cluster["center"] = median(cluster["prices"])
        else:
            clusters.append({"center": price, "prices": [price], "pivots": [pivot]})

    levels = []
    for cluster in clusters:
        if len(cluster["pivots"]) < min_touches:
            continue
        highs = sum(p["type"] == "HIGH" for p in cluster["pivots"])
        lows = sum(p["type"] == "LOW" for p in cluster["pivots"])
        historical_type = "RESISTANCE" if highs > lows else "SUPPORT" if lows > highs else "PIVOT_ZONE"
        levels.append({
            "price": float(cluster["center"]),
            "historical_type": historical_type,
            "touches": len(cluster["pivots"]),
            "high_touches": highs,
            "low_touches": lows,
            "first_time": cluster["pivots"][0].get("time"),
            "last_time": cluster["pivots"][-1].get("time"),
        })
    return levels


def level_reaction(candles, level, tolerance_pct, lookback=24):
    """Track one level as a break -> extension -> retest/failure state machine.

    A decisive recross after a confirmed extension resolves the original break
    as FAILED. The same candle is not immediately re-labelled as a fresh break
    in the opposite direction; that prevents a failure from erasing its cause.
    """
    if len(candles) < 3:
        return {"event": "INSUFFICIENT_DATA"}

    sample = candles[-lookback:]
    level = float(level)
    upper = level * (1 + tolerance_pct)
    lower = level * (1 - tolerance_pct)
    extension = tolerance_pct * 1.5
    active = None
    last_resolved = None

    for i in range(1, len(sample)):
        previous_close = _f(sample[i - 1], "close")
        close = _f(sample[i], "close")
        cross_up = previous_close <= upper and close > upper
        cross_down = previous_close >= lower and close < lower

        if active is not None:
            direction = active["direction"]

            if direction == "UP":
                if close > level * (1 + extension):
                    active["extended"] = True
                if active["extended"] and _f(sample[i], "low") <= upper:
                    active["touched"] = True
                if close < lower:
                    last_resolved = {
                        "event": "BULLISH_BREAK_FAILED",
                        "direction": "UP",
                        "break_index": active["break_index"],
                        "failure_index": i,
                    }
                    active = None
                    continue
            else:
                if close < level * (1 - extension):
                    active["extended"] = True
                if active["extended"] and _f(sample[i], "high") >= lower:
                    active["touched"] = True
                if close > upper:
                    last_resolved = {
                        "event": "BEARISH_BREAK_FAILED",
                        "direction": "DOWN",
                        "break_index": active["break_index"],
                        "failure_index": i,
                    }
                    active = None
                    continue

        if active is None:
            if cross_up:
                active = {
                    "direction": "UP",
                    "break_index": i,
                    "extended": close > level * (1 + extension),
                    "touched": False,
                }
            elif cross_down:
                active = {
                    "direction": "DOWN",
                    "break_index": i,
                    "extended": close < level * (1 - extension),
                    "touched": False,
                }

    if active is not None:
        direction = active["direction"]
        prefix = "BULLISH" if direction == "UP" else "BEARISH"

        if not active["extended"]:
            return {"event": prefix + "_BREAK_UNCONFIRMED", "direction": direction}

        if active["touched"]:
            last_close = _f(sample[-1], "close")
            if direction == "UP":
                event = "BULLISH_RETEST_HELD" if last_close > upper else "BULLISH_RETEST_IN_PROGRESS"
            else:
                event = "BEARISH_RETEST_HELD" if last_close < lower else "BEARISH_RETEST_IN_PROGRESS"
        else:
            event = "AWAITING_" + prefix + "_RETEST"

        return {
            "event": event,
            "direction": direction,
            "break_index": active["break_index"],
        }

    if last_resolved is not None:
        return last_resolved

    last = sample[-1]
    if _f(last, "low") < lower and _f(last, "close") > upper:
        return {"event": "LOWER_SWEEP_RECLAIMED", "direction": "UP"}
    if _f(last, "high") > upper and _f(last, "close") < lower:
        return {"event": "UPPER_SWEEP_REJECTED", "direction": "DOWN"}
    return {"event": "NO_RECENT_BREAK"}


def analyze(candles, left=2, right=2, max_levels=5):
    if not candles:
        return {"state": "NO_DATA", "levels": [], "breaks": []}

    tolerance = adaptive_tolerance_pct(candles)
    raw = detect_raw_pivots(candles, left, right)
    normalized = normalize_pivots(raw)
    classified = classify_pivots(normalized, tolerance)
    state = structure_state(classified)
    breaks = detect_structure_breaks(candles, classified, tolerance)

    price = _f(candles[-1], "close")
    levels = cluster_levels(classified, tolerance)
    for level in levels:
        level["distance_pct"] = (level["price"] - price) / price
        if abs(level["distance_pct"]) <= tolerance:
            level["current_role"] = "ACTIVE_ZONE"
        elif level["price"] > price:
            level["current_role"] = "RESISTANCE"
        else:
            level["current_role"] = "SUPPORT"
        level["reaction"] = level_reaction(candles, level["price"], tolerance)

    levels.sort(key=lambda x: abs(x["distance_pct"]))

    return {
        "state": state["state"],
        "high_structure": state["high_structure"],
        "low_structure": state["low_structure"],
        "atr": atr(candles),
        "tolerance_pct": tolerance,
        "pivot_count": len(classified),
        "recent_pivots": classified[-12:],
        "breaks": breaks[-8:],
        "last_break": breaks[-1] if breaks else None,
        "levels": levels[:max_levels],
    }
