import structural_core

def candle_anatomy(candle):
    o = float(candle["open"])
    h = float(candle["high"])
    l = float(candle["low"])
    c = float(candle["close"])
    v = float(candle.get("volume", 0.0))

    rng = max(h - l, 0.0)
    body = abs(c - o)
    upper_wick = max(h - max(o, c), 0.0)
    lower_wick = max(min(o, c) - l, 0.0)

    if c > o:
        direction = "BULLISH"
    elif c < o:
        direction = "BEARISH"
    else:
        direction = "DOJI"

    if rng > 0:
        body_ratio = body / rng
        upper_wick_ratio = upper_wick / rng
        lower_wick_ratio = lower_wick / rng
        close_position = (c - l) / rng
    else:
        body_ratio = 0.0
        upper_wick_ratio = 0.0
        lower_wick_ratio = 0.0
        close_position = 0.5

    return {
        "time": candle.get("time"),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
        "direction": direction,
        "range": rng,
        "body": body,
        "upper_wick": upper_wick,
        "lower_wick": lower_wick,
        "body_ratio": body_ratio,
        "upper_wick_ratio": upper_wick_ratio,
        "lower_wick_ratio": lower_wick_ratio,
        "close_position": close_position,
    }

def classify_candle(candle):
    a = candle_anatomy(candle)

    if a["body_ratio"] <= 0.15:
        candle_type = "DOJI"
    elif a["lower_wick_ratio"] >= 0.50 and a["close_position"] >= 0.60:
        candle_type = "LOWER_REJECTION"
    elif a["upper_wick_ratio"] >= 0.50 and a["close_position"] <= 0.40:
        candle_type = "UPPER_REJECTION"
    elif a["body_ratio"] >= 0.65 and a["direction"] == "BULLISH":
        candle_type = "BULLISH_IMPULSE"
    elif a["body_ratio"] >= 0.65 and a["direction"] == "BEARISH":
        candle_type = "BEARISH_IMPULSE"
    else:
        candle_type = "NORMAL"

    a["candle_type"] = candle_type
    return a


def analyze_candle_sequence(candles, lookback=5):
    sample = candles[-lookback:]
    classified = [classify_candle(c) for c in sample]

    bullish = sum(c["direction"] == "BULLISH" for c in classified)
    bearish = sum(c["direction"] == "BEARISH" for c in classified)
    lower_rejections = sum(c["candle_type"] == "LOWER_REJECTION" for c in classified)
    upper_rejections = sum(c["candle_type"] == "UPPER_REJECTION" for c in classified)
    bullish_impulses = sum(c["candle_type"] == "BULLISH_IMPULSE" for c in classified)
    bearish_impulses = sum(c["candle_type"] == "BEARISH_IMPULSE" for c in classified)

    return {
        "lookback": len(classified),
        "bullish_candles": bullish,
        "bearish_candles": bearish,
        "lower_rejections": lower_rejections,
        "upper_rejections": upper_rejections,
        "bullish_impulses": bullish_impulses,
        "bearish_impulses": bearish_impulses,
        "candles": classified,
    }

def detect_swings(candles, left=2, right=2):
    swings = []

    if len(candles) < left + right + 1:
        return swings

    for i in range(left, len(candles) - right):
        current = candles[i]

        left_part = candles[i-left:i]
        right_part = candles[i+1:i+1+right]

        is_swing_high = (
            all(current["high"] > c["high"] for c in left_part)
            and all(current["high"] >= c["high"] for c in right_part)
        )

        is_swing_low = (
            all(current["low"] < c["low"] for c in left_part)
            and all(current["low"] <= c["low"] for c in right_part)
        )

        if is_swing_high:
            swings.append({
                "index": i,
                "time": current.get("time"),
                "type": "SWING_HIGH",
                "price": float(current["high"]),
            })

        if is_swing_low:
            swings.append({
                "index": i,
                "time": current.get("time"),
                "type": "SWING_LOW",
                "price": float(current["low"]),
            })

    swings.sort(key=lambda x: x["index"])
    return swings

def classify_swings(swings):
    result = []
    previous_high = None
    previous_low = None

    for swing in swings:
        item = dict(swing)

        if swing["type"] == "SWING_HIGH":
            if previous_high is None:
                item["structure"] = "H"
            elif swing["price"] > previous_high:
                item["structure"] = "HH"
            elif swing["price"] < previous_high:
                item["structure"] = "LH"
            else:
                item["structure"] = "EH"

            previous_high = swing["price"]

        elif swing["type"] == "SWING_LOW":
            if previous_low is None:
                item["structure"] = "L"
            elif swing["price"] > previous_low:
                item["structure"] = "HL"
            elif swing["price"] < previous_low:
                item["structure"] = "LL"
            else:
                item["structure"] = "EL"

            previous_low = swing["price"]

        result.append(item)

    return result


def detect_market_structure(candles, left=2, right=2):
    swings = classify_swings(
        detect_swings(candles, left, right)
    )

    recent = swings[-8:]

    highs = [
        s for s in recent
        if s["type"] == "SWING_HIGH"
    ]
    lows = [
        s for s in recent
        if s["type"] == "SWING_LOW"
    ]

    high_state = highs[-1]["structure"] if highs else None
    low_state = lows[-1]["structure"] if lows else None

    if high_state == "HH" and low_state == "HL":
        trend = "BULLISH_STRUCTURE"
    elif high_state == "LH" and low_state == "LL":
        trend = "BEARISH_STRUCTURE"
    else:
        trend = "MIXED_STRUCTURE"

    return {
        "trend": trend,
        "last_high_structure": high_state,
        "last_low_structure": low_state,
        "recent_swings": recent,
    }

def classify_swings_tolerant(swings, tolerance_pct=0.003):
    result = []
    previous_high = None
    previous_low = None

    for swing in swings:
        item = dict(swing)
        price = float(swing["price"])

        if swing["type"] == "SWING_HIGH":
            if previous_high is None:
                structure = "H"
            else:
                change = (price - previous_high) / previous_high
                if change > tolerance_pct:
                    structure = "HH"
                elif change < -tolerance_pct:
                    structure = "LH"
                else:
                    structure = "EH"

            previous_high = price

        else:
            if previous_low is None:
                structure = "L"
            else:
                change = (price - previous_low) / previous_low
                if change > tolerance_pct:
                    structure = "HL"
                elif change < -tolerance_pct:
                    structure = "LL"
                else:
                    structure = "EL"

            previous_low = price

        item["structure"] = structure
        result.append(item)

    return result


def detect_market_structure_tolerant(
    candles,
    left=2,
    right=2,
    tolerance_pct=0.003
):
    swings = classify_swings_tolerant(
        detect_swings(candles, left, right),
        tolerance_pct
    )

    recent = swings[-8:]
    highs = [s for s in recent if s["type"] == "SWING_HIGH"]
    lows = [s for s in recent if s["type"] == "SWING_LOW"]

    high_state = highs[-1]["structure"] if highs else None
    low_state = lows[-1]["structure"] if lows else None

    if high_state == "HH" and low_state in ("HL", "EL"):
        trend = "BULLISH_STRUCTURE"
    elif high_state in ("LH", "EH") and low_state == "LL":
        trend = "BEARISH_STRUCTURE"
    else:
        trend = "MIXED_STRUCTURE"

    return {
        "trend": trend,
        "last_high_structure": high_state,
        "last_low_structure": low_state,
        "recent_swings": recent,
        "tolerance_pct": tolerance_pct,
    }

INTERVAL_MS = {
    "1m": 60 * 1000,
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "2h": 2 * 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
}


def closed_candles(candles, interval, now_ms=None):
    import time

    if interval not in INTERVAL_MS:
        raise ValueError(f"Unsupported interval: {interval}")

    if now_ms is None:
        now_ms = int(time.time() * 1000)

    interval_ms = INTERVAL_MS[interval]

    return [
        c for c in candles
        if int(c["time"]) + interval_ms <= now_ms
    ]

def persistent_structure(swings, lookback=12):
    recent = swings[-lookback:]

    last_high_signal = None
    last_low_signal = None
    state = "UNDEFINED"
    bullish_confirmations = 0
    bearish_confirmations = 0

    for swing in recent:
        structure = swing.get("structure")

        if structure in ("HH", "LH"):
            last_high_signal = structure

        if structure in ("HL", "LL"):
            last_low_signal = structure

        if last_high_signal == "HH" and last_low_signal == "HL":
            if state != "BULLISH_STRUCTURE":
                bullish_confirmations += 1
            state = "BULLISH_STRUCTURE"

        elif last_high_signal == "LH" and last_low_signal == "LL":
            if state != "BEARISH_STRUCTURE":
                bearish_confirmations += 1
            state = "BEARISH_STRUCTURE"

    return {
        "trend": state,
        "last_significant_high": last_high_signal,
        "last_significant_low": last_low_signal,
        "bullish_confirmations": bullish_confirmations,
        "bearish_confirmations": bearish_confirmations,
        "recent_swings": recent,
    }


def analyze_structure(candles, left=2, right=2, tolerance_pct=0.003, lookback=12):
    swings = classify_swings_tolerant(
        detect_swings(candles, left, right),
        tolerance_pct
    )

    result = persistent_structure(swings, lookback)
    result["tolerance_pct"] = tolerance_pct
    result["swing_count"] = len(swings)

    return result

def structural_state(swings, lookback=12):
    recent = swings[-lookback:]

    last_high_signal = None
    last_low_signal = None
    confirmed_state = "UNDEFINED"
    transition_reason = None

    for swing in recent:
        structure = swing.get("structure")

        if structure in ("HH", "LH"):
            last_high_signal = structure

        if structure in ("HL", "LL"):
            last_low_signal = structure

        if last_high_signal == "HH" and last_low_signal == "HL":
            confirmed_state = "BULLISH_STRUCTURE"
            transition_reason = None

        elif last_high_signal == "LH" and last_low_signal == "LL":
            confirmed_state = "BEARISH_STRUCTURE"
            transition_reason = None

        elif confirmed_state == "BEARISH_STRUCTURE" and last_high_signal == "HH":
            transition_reason = "BEARISH_STRUCTURE_BROKEN_BY_HH"

        elif confirmed_state == "BULLISH_STRUCTURE" and last_low_signal == "LL":
            transition_reason = "BULLISH_STRUCTURE_BROKEN_BY_LL"

    if transition_reason:
        state = "TRANSITION_STRUCTURE"
    else:
        state = confirmed_state

    return {
        "trend": state,
        "confirmed_state": confirmed_state,
        "transition_reason": transition_reason,
        "last_significant_high": last_high_signal,
        "last_significant_low": last_low_signal,
        "recent_swings": recent,
    }


def analyze_structure_v2(candles, left=2, right=2, tolerance_pct=0.003, lookback=12):
    swings = classify_swings_tolerant(
        detect_swings(candles, left, right),
        tolerance_pct
    )

    result = structural_state(swings, lookback)
    result["tolerance_pct"] = tolerance_pct
    result["swing_count"] = len(swings)

    return result

def structural_state_v2(swings, lookback=12):
    recent = swings[-lookback:]

    last_high_signal = None
    last_low_signal = None
    last_confirmed_state = "UNDEFINED"

    for swing in recent:
        structure = swing.get("structure")

        if structure in ("HH", "LH"):
            last_high_signal = structure

        if structure in ("HL", "LL"):
            last_low_signal = structure

        if last_high_signal == "HH" and last_low_signal == "HL":
            last_confirmed_state = "BULLISH_STRUCTURE"

        elif last_high_signal == "LH" and last_low_signal == "LL":
            last_confirmed_state = "BEARISH_STRUCTURE"

    pair = (last_high_signal, last_low_signal)

    if pair == ("HH", "HL"):
        state = "BULLISH_STRUCTURE"
        reason = None

    elif pair == ("LH", "LL"):
        state = "BEARISH_STRUCTURE"
        reason = None

    elif pair == ("HH", "LL"):
        state = "TRANSITION_STRUCTURE"
        reason = "HIGHER_HIGH_WITH_LOWER_LOW"

    elif pair == ("LH", "HL"):
        state = "TRANSITION_STRUCTURE"
        reason = "LOWER_HIGH_WITH_HIGHER_LOW"

    else:
        state = "UNDEFINED"
        reason = "INSUFFICIENT_STRUCTURE"

    return {
        "trend": state,
        "confirmed_state": last_confirmed_state,
        "transition_reason": reason,
        "last_significant_high": last_high_signal,
        "last_significant_low": last_low_signal,
        "recent_swings": recent,
    }


def analyze_structure_v3(candles, left=2, right=2, tolerance_pct=0.003, lookback=12):
    swings = classify_swings_tolerant(
        detect_swings(candles, left, right),
        tolerance_pct
    )

    result = structural_state_v2(swings, lookback)
    result["tolerance_pct"] = tolerance_pct
    result["swing_count"] = len(swings)

    return result

def detect_sr_levels(candles, left=2, right=2, tolerance_pct=0.003, min_touches=2):
    swings = detect_swings(candles, left, right)

    if not swings:
        return []

    clusters = []

    for swing in swings:
        price = float(swing["price"])
        matched = None

        for cluster in clusters:
            distance = abs(price - cluster["center"]) / cluster["center"]

            if distance <= tolerance_pct:
                matched = cluster
                break

        if matched is None:
            clusters.append({
                "center": price,
                "prices": [price],
                "touches": 1,
                "high_touches": 1 if swing["type"] == "SWING_HIGH" else 0,
                "low_touches": 1 if swing["type"] == "SWING_LOW" else 0,
                "first_time": swing.get("time"),
                "last_time": swing.get("time"),
            })
        else:
            matched["prices"].append(price)
            matched["touches"] += 1

            if swing["type"] == "SWING_HIGH":
                matched["high_touches"] += 1
            else:
                matched["low_touches"] += 1

            matched["center"] = sum(matched["prices"]) / len(matched["prices"])
            matched["last_time"] = swing.get("time")

    levels = []

    for cluster in clusters:
        if cluster["touches"] < min_touches:
            continue

        if cluster["high_touches"] > cluster["low_touches"]:
            level_type = "RESISTANCE"
        elif cluster["low_touches"] > cluster["high_touches"]:
            level_type = "SUPPORT"
        else:
            level_type = "PIVOT_ZONE"

        levels.append({
            "price": cluster["center"],
            "type": level_type,
            "touches": cluster["touches"],
            "high_touches": cluster["high_touches"],
            "low_touches": cluster["low_touches"],
            "first_time": cluster["first_time"],
            "last_time": cluster["last_time"],
        })

    levels.sort(key=lambda x: x["price"])
    return levels

def contextualize_sr_levels(levels, current_price, proximity_pct=0.003):
    contextual = []

    for level in levels:
        item = dict(level)
        level_price = float(level["price"])
        distance_pct = (level_price - current_price) / current_price

        if abs(distance_pct) <= proximity_pct:
            current_role = "ACTIVE_PIVOT_ZONE"
        elif level_price > current_price:
            current_role = "RESISTANCE"
        else:
            current_role = "SUPPORT"

        item["historical_type"] = level["type"]
        item["current_role"] = current_role
        item["distance_pct"] = distance_pct

        if (
            level["type"] == "SUPPORT"
            and current_role == "RESISTANCE"
        ):
            item["role_reversal"] = "SUPPORT_TO_RESISTANCE"

        elif (
            level["type"] == "RESISTANCE"
            and current_role == "SUPPORT"
        ):
            item["role_reversal"] = "RESISTANCE_TO_SUPPORT"

        else:
            item["role_reversal"] = None

        contextual.append(item)

    contextual.sort(
        key=lambda x: abs(x["distance_pct"])
    )

    return contextual

def detect_level_event(candles, level_price, tolerance_pct=0.0015, lookback=12):
    if not candles:
        return {
            "event": "NO_DATA",
            "level": level_price,
        }

    sample = candles[-lookback:]
    level = float(level_price)
    tolerance = level * tolerance_pct

    closes_above = 0
    closes_below = 0
    wick_above = 0
    wick_below = 0

    for candle in sample:
        close = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])

        if close > level + tolerance:
            closes_above += 1
        elif close < level - tolerance:
            closes_below += 1

        if high > level + tolerance and close <= level + tolerance:
            wick_above += 1

        if low < level - tolerance and close >= level - tolerance:
            wick_below += 1

    last = sample[-1]
    last_close = float(last["close"])
    previous_close = float(sample[-2]["close"]) if len(sample) >= 2 else last_close

    if (
        previous_close <= level + tolerance
        and last_close > level + tolerance
    ):
        event = "BREAKOUT_CLOSE"

    elif (
        previous_close >= level - tolerance
        and last_close < level - tolerance
    ):
        event = "BREAKDOWN_CLOSE"

    elif (
        float(last["low"]) < level - tolerance
        and last_close >= level - tolerance
    ):
        event = "LOWER_WICK_SWEEP"

    elif (
        float(last["high"]) > level + tolerance
        and last_close <= level + tolerance
    ):
        event = "UPPER_WICK_SWEEP"

    elif abs(last_close - level) <= tolerance:
        event = "TESTING_LEVEL"

    else:
        event = "NO_CURRENT_EVENT"

    return {
        "event": event,
        "level": level,
        "tolerance_pct": tolerance_pct,
        "last_close": last_close,
        "closes_above": closes_above,
        "closes_below": closes_below,
        "wick_sweeps_above": wick_above,
        "wick_sweeps_below": wick_below,
    }

def detect_level_event_v2(candles, level_price, tolerance_pct=0.0015):
    if not candles:
        return {"event": "NO_DATA", "level": level_price}

    level = float(level_price)
    tolerance = level * tolerance_pct
    upper = level + tolerance
    lower = level - tolerance

    last = candles[-1]
    previous = candles[-2] if len(candles) >= 2 else last

    prev_close = float(previous["close"])
    o = float(last["open"])
    h = float(last["high"])
    l = float(last["low"])
    c = float(last["close"])

    if prev_close > upper and c < lower:
        event = "BREAKDOWN_CLOSE"

    elif prev_close < lower and c > upper:
        event = "BREAKOUT_CLOSE"

    elif prev_close > upper and l <= upper and c >= lower:
        event = "TESTING_LEVEL_FROM_ABOVE"

    elif prev_close < lower and h >= lower and c <= upper:
        event = "TESTING_LEVEL_FROM_BELOW"

    elif prev_close > upper and l < lower and c > upper:
        event = "LOWER_LIQUIDITY_SWEEP_RECLAIMED"

    elif prev_close < lower and h > upper and c < lower:
        event = "UPPER_LIQUIDITY_SWEEP_REJECTED"

    elif lower <= c <= upper:
        event = "INSIDE_LEVEL_ZONE"

    elif c > upper:
        event = "HOLDING_ABOVE"

    elif c < lower:
        event = "HOLDING_BELOW"

    else:
        event = "NO_CURRENT_EVENT"

    return {
        "event": event,
        "level": level,
        "zone_low": lower,
        "zone_high": upper,
        "previous_close": prev_close,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
    }

def detect_break_retest(candles, level_price, tolerance_pct=0.0015, lookback=20):
    if len(candles) < 3:
        return {"event": "INSUFFICIENT_DATA", "level": level_price}

    level = float(level_price)
    tolerance = level * tolerance_pct
    upper = level + tolerance
    lower = level - tolerance
    sample = candles[-lookback:]

    last_break = None

    for i in range(1, len(sample)):
        prev_close = float(sample[i - 1]["close"])
        close = float(sample[i]["close"])

        if prev_close <= upper and close > upper:
            last_break = {
                "index": i,
                "direction": "UP",
                "time": sample[i].get("time"),
                "close": close,
            }

        elif prev_close >= lower and close < lower:
            last_break = {
                "index": i,
                "direction": "DOWN",
                "time": sample[i].get("time"),
                "close": close,
            }

    if last_break is None:
        return {
            "event": "NO_RECENT_BREAK",
            "level": level,
        }

    after = sample[last_break["index"] + 1:]

    if not after:
        return {
            "event": "BREAK_JUST_OCCURRED",
            "level": level,
            "break": last_break,
        }

    if last_break["direction"] == "UP":
        touched = [
            c for c in after
            if float(c["low"]) <= upper
        ]

        if touched:
            last = after[-1]
            if float(last["close"]) > upper:
                event = "BULLISH_RETEST_HELD"
            elif float(last["close"]) < lower:
                event = "BULLISH_BREAK_FAILED"
            else:
                event = "BULLISH_RETEST_IN_PROGRESS"
        else:
            event = "AWAITING_BULLISH_RETEST"

    else:
        touched = [
            c for c in after
            if float(c["high"]) >= lower
        ]

        if touched:
            last = after[-1]
            if float(last["close"]) < lower:
                event = "BEARISH_RETEST_HELD"
            elif float(last["close"]) > upper:
                event = "BEARISH_BREAK_FAILED"
            else:
                event = "BEARISH_RETEST_IN_PROGRESS"
        else:
            event = "AWAITING_BEARISH_RETEST"

    return {
        "event": event,
        "level": level,
        "zone_low": lower,
        "zone_high": upper,
        "break": last_break,
        "candles_after_break": len(after),
        "retest_touches": len(touched) if "touched" in locals() else 0,
    }

def detect_break_retest_v2(candles, level_price, tolerance_pct=0.0015, lookback=20):
    if len(candles) < 4:
        return {"event": "INSUFFICIENT_DATA", "level": level_price}

    level = float(level_price)
    tolerance = level * tolerance_pct
    upper = level + tolerance
    lower = level - tolerance
    sample = candles[-lookback:]

    breaks = []

    for i in range(1, len(sample)):
        prev_close = float(sample[i - 1]["close"])
        close = float(sample[i]["close"])

        if prev_close <= upper and close > upper:
            breaks.append((i, "UP"))
        elif prev_close >= lower and close < lower:
            breaks.append((i, "DOWN"))

    if not breaks:
        return {"event": "NO_RECENT_BREAK", "level": level}

    break_index, direction = breaks[-1]
    after = sample[break_index + 1:]

    break_info = {
        "index": break_index,
        "direction": direction,
        "time": sample[break_index].get("time"),
        "close": float(sample[break_index]["close"]),
    }

    if not after:
        return {
            "event": "BREAK_JUST_OCCURRED",
            "level": level,
            "break": break_info,
        }

    separation_pct = tolerance_pct * 2

    if direction == "UP":
        separated_index = None

        for j, candle in enumerate(after):
            if float(candle["close"]) >= level * (1 + separation_pct):
                separated_index = j
                break

        if separated_index is None:
            event = "BULLISH_BREAK_NOT_EXTENDED"
            retest_touches = 0
        else:
            post_separation = after[separated_index + 1:]
            touches = [
                c for c in post_separation
                if float(c["low"]) <= upper
            ]
            retest_touches = len(touches)

            if not touches:
                event = "AWAITING_BULLISH_RETEST"
            else:
                last = after[-1]
                if float(last["close"]) > upper:
                    event = "BULLISH_RETEST_HELD"
                elif float(last["close"]) < lower:
                    event = "BULLISH_BREAK_FAILED"
                else:
                    event = "BULLISH_RETEST_IN_PROGRESS"

    else:
        separated_index = None

        for j, candle in enumerate(after):
            if float(candle["close"]) <= level * (1 - separation_pct):
                separated_index = j
                break

        if separated_index is None:
            event = "BEARISH_BREAK_NOT_EXTENDED"
            retest_touches = 0
        else:
            post_separation = after[separated_index + 1:]
            touches = [
                c for c in post_separation
                if float(c["high"]) >= lower
            ]
            retest_touches = len(touches)

            if not touches:
                event = "AWAITING_BEARISH_RETEST"
            else:
                last = after[-1]
                if float(last["close"]) < lower:
                    event = "BEARISH_RETEST_HELD"
                elif float(last["close"]) > upper:
                    event = "BEARISH_BREAK_FAILED"
                else:
                    event = "BEARISH_RETEST_IN_PROGRESS"

    return {
        "event": event,
        "level": level,
        "zone_low": lower,
        "zone_high": upper,
        "break": break_info,
        "candles_after_break": len(after),
        "retest_touches": retest_touches,
    }

def analyze_range(candles, lookback=24, edge_pct=0.15):
    if len(candles) < lookback:
        return {"state": "INSUFFICIENT_DATA"}

    sample = candles[-lookback:]

    high = max(float(c["high"]) for c in sample)
    low = min(float(c["low"]) for c in sample)
    width = high - low

    if width <= 0:
        return {"state": "INVALID_RANGE"}

    midpoint = (high + low) / 2

    ranges = [
        float(c["high"]) - float(c["low"])
        for c in sample[-14:]
    ]
    atr14 = sum(ranges) / len(ranges)
    range_atr = width / atr14 if atr14 else 0

    mid_crosses = sum(
        1
        for i in range(1, len(sample))
        if (
            (float(sample[i - 1]["close"]) - midpoint)
            * (float(sample[i]["close"]) - midpoint)
            < 0
        )
    )

    top_boundary = high - width * edge_pct
    bottom_boundary = low + width * edge_pct

    top_touches = sum(
        1 for c in sample
        if float(c["high"]) >= top_boundary
    )

    bottom_touches = sum(
        1 for c in sample
        if float(c["low"]) <= bottom_boundary
    )

    closes_above_mid = sum(
        1 for c in sample
        if float(c["close"]) > midpoint
    )

    closes_below_mid = sum(
        1 for c in sample
        if float(c["close"]) < midpoint
    )

    last_close = float(sample[-1]["close"])
    location = (last_close - low) / width

    balanced_edges = (
        top_touches >= 2
        and bottom_touches >= 2
    )

    rotational = mid_crosses >= 4

    if balanced_edges and rotational:
        state = "RANGE_ROTATION"

    elif mid_crosses <= 2 and location <= 0.35:
        state = "DOWNWARD_DISPLACEMENT"

    elif mid_crosses <= 2 and location >= 0.65:
        state = "UPWARD_DISPLACEMENT"

    else:
        state = "MIXED_RANGE_BEHAVIOR"

    return {
        "state": state,
        "high": high,
        "low": low,
        "midpoint": midpoint,
        "width": width,
        "range_atr": range_atr,
        "mid_crosses": mid_crosses,
        "top_touches": top_touches,
        "bottom_touches": bottom_touches,
        "closes_above_mid": closes_above_mid,
        "closes_below_mid": closes_below_mid,
        "last_location": location,
    }

def analyze_multitimeframe(candles_by_tf):
    roles = {
        "4h": "MACRO_CONTEXT",
        "2h": "TRANSITION_CONTEXT",
        "1h": "OPERATIONAL_STRUCTURE",
        "15m": "CONFIRMATION",
        "5m": "TRIGGER_CONTEXT",
        "1m": "EXECUTION_CONTEXT",
    }

    result = {}

    for tf, role in roles.items():
        candles = candles_by_tf.get(tf, [])

        if not candles:
            result[tf] = {
                "role": role,
                "state": "NO_DATA",
            }
            continue

        structure = analyze_structure_v3(candles)
        range_state = analyze_range(candles)
        sequence = analyze_candle_sequence(candles)
        sr_context = analyze_sr_context(candles)
        canonical = structural_core.analyze(candles)

        result[tf] = {
            "role": role,
            "structure": structure["trend"],
            "high_structure": structure.get("last_significant_high"),
            "low_structure": structure.get("last_significant_low"),
            "transition_reason": structure.get("transition_reason"),
            "behavior": range_state["state"],
            "range_location": range_state.get("last_location"),
            "mid_crosses": range_state.get("mid_crosses"),
            "bullish_candles": sequence.get("bullish_candles"),
            "bearish_candles": sequence.get("bearish_candles"),
            "bullish_impulses": sequence.get("bullish_impulses"),
            "bearish_impulses": sequence.get("bearish_impulses"),
            "lower_rejections": sequence.get("lower_rejections"),
            "upper_rejections": sequence.get("upper_rejections"),
            "sr_context": sr_context,
            "canonical": canonical,
        }

    return result

def interpret_multitimeframe(mtf):
    tf4 = mtf.get("4h", {})
    tf2 = mtf.get("2h", {})
    tf1 = mtf.get("1h", {})
    tf15 = mtf.get("15m", {})
    tf5 = mtf.get("5m", {})

    structures = {
        "4h": tf4.get("structure"),
        "2h": tf2.get("structure"),
        "1h": tf1.get("structure"),
        "15m": tf15.get("structure"),
        "5m": tf5.get("structure"),
    }

    bearish_operational = all(
        structures.get(tf) == "BEARISH_STRUCTURE"
        for tf in ("2h", "1h", "15m")
    )

    bullish_operational = all(
        structures.get(tf) == "BULLISH_STRUCTURE"
        for tf in ("2h", "1h", "15m")
    )

    trigger_bullish_reaction = (
        (tf5.get("bullish_candles") or 0) >= 3
        and (tf5.get("bullish_impulses") or 0) >= 1
    )

    trigger_bearish_reaction = (
        (tf5.get("bearish_candles") or 0) >= 3
        and (tf5.get("bearish_impulses") or 0) >= 1
    )

    if bearish_operational:
        if trigger_bullish_reaction:
            context = "BEARISH_WITH_BULLISH_REACTION"
        else:
            context = "BEARISH_ALIGNMENT"

    elif bullish_operational:
        if trigger_bearish_reaction:
            context = "BULLISH_WITH_BEARISH_REACTION"
        else:
            context = "BULLISH_ALIGNMENT"

    elif any(
        value == "TRANSITION_STRUCTURE"
        for value in structures.values()
    ):
        context = "STRUCTURAL_TRANSITION"

    else:
        context = "MIXED_TIMEFRAMES"

    return {
        "context": context,
        "macro_structure": structures["4h"],
        "operational_structure": structures["1h"],
        "confirmation_structure": structures["15m"],
        "trigger_structure": structures["5m"],
        "trigger_bullish_reaction": trigger_bullish_reaction,
        "trigger_bearish_reaction": trigger_bearish_reaction,
        "structures": structures,
    }

def analyze_sr_context(candles, max_levels=3):
    if not candles:
        return {
            "current_price": None,
            "levels": [],
        }

    current_price = float(candles[-1]["close"])

    levels = detect_sr_levels(candles)
    contextual = contextualize_sr_levels(
        levels,
        current_price,
    )

    selected = contextual[:max_levels]
    result_levels = []

    for level in selected:
        item = dict(level)
        item["break_retest"] = detect_break_retest_v2(
            candles,
            level["price"],
        )
        result_levels.append(item)

    return {
        "current_price": current_price,
        "levels": result_levels,
    }
