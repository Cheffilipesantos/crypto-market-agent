from structural_core import (
    adaptive_tolerance_pct,
    normalize_pivots,
    classify_pivots,
    structure_state,
    level_reaction,
    detect_structure_breaks,
    detect_raw_pivots,
)


def candle(o, h, l, c, t=0):
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": 1}


# Volatility-aware tolerance must expand when candle ranges expand.
quiet = [candle(100, 100.2, 99.8, 100, i) for i in range(20)]
volatile = [candle(100, 103, 97, 100, i) for i in range(20)]
assert adaptive_tolerance_pct(volatile) > adaptive_tolerance_pct(quiet)


# Consecutive pivot types retain the more extreme point.
pivots = [
    {"index": 1, "type": "HIGH", "price": 105},
    {"index": 2, "type": "HIGH", "price": 107},
    {"index": 3, "type": "LOW", "price": 101},
]
normalized = normalize_pivots(pivots)
assert len(normalized) == 2
assert normalized[0]["price"] == 107


# Explicit HH + HL must read bullish; LH + LL bearish.
bull = classify_pivots([
    {"index": 1, "type": "HIGH", "price": 100},
    {"index": 2, "type": "LOW", "price": 90},
    {"index": 3, "type": "HIGH", "price": 110},
    {"index": 4, "type": "LOW", "price": 95},
], 0.001)
assert structure_state(bull)["state"] == "BULLISH_STRUCTURE"

bear = classify_pivots([
    {"index": 1, "type": "HIGH", "price": 110},
    {"index": 2, "type": "LOW", "price": 100},
    {"index": 3, "type": "HIGH", "price": 105},
    {"index": 4, "type": "LOW", "price": 90},
], 0.001)
assert structure_state(bear)["state"] == "BEARISH_STRUCTURE"


# A downside break that extends, retests, and closes back below must be recognized.
series = [
    candle(102, 103, 101, 102, 1),
    candle(102, 102.5, 100.5, 101, 2),
    candle(101, 101.2, 98.8, 99, 3),   # break below 100
    candle(99, 99.2, 97.5, 98, 4),     # extension
    candle(98, 100.1, 97.8, 98.5, 5),  # retest, held below
]
reaction = level_reaction(series, 100, 0.0015)
assert reaction["event"] == "BEARISH_RETEST_HELD"


# A break that later closes decisively back through the level is a failure.
failed = [
    candle(102, 103, 101, 102, 1),
    candle(102, 102.5, 100.5, 101, 2),
    candle(101, 101.2, 98.8, 99, 3),
    candle(99, 99.2, 97.5, 98, 4),
    candle(98, 101.5, 97.8, 101, 5),
]
reaction = level_reaction(failed, 100, 0.0015)
assert reaction["event"] == "BEARISH_BREAK_FAILED"



# BOS/CHOCH must use only the structure available before the break candle.
bullish_pivots = [
    {"index": 1, "type": "HIGH", "price": 100, "structure": "H"},
    {"index": 2, "type": "LOW", "price": 90, "structure": "L"},
    {"index": 3, "type": "HIGH", "price": 110, "structure": "HH"},
    {"index": 4, "type": "LOW", "price": 95, "structure": "HL"},
]
bullish_candles = [
    candle(96, 97, 95, 96, 0),
    candle(96, 101, 95, 99, 1),
    candle(99, 100, 89, 92, 2),
    candle(92, 111, 91, 108, 3),
    candle(108, 109, 94, 97, 4),
    candle(97, 98, 95, 96, 5),
    candle(96, 97, 93, 94, 6),
]
events = detect_structure_breaks(bullish_candles, bullish_pivots, 0.001)
down = [e for e in events if e["direction"] == "DOWN" and e["pivot_index"] == 4]
assert down
assert down[-1]["kind"] == "CHOCH"
assert down[-1]["prior_state"] == "BULLISH_STRUCTURE"

bearish_pivots = [
    {"index": 1, "type": "HIGH", "price": 110, "structure": "H"},
    {"index": 2, "type": "LOW", "price": 100, "structure": "L"},
    {"index": 3, "type": "HIGH", "price": 105, "structure": "LH"},
    {"index": 4, "type": "LOW", "price": 90, "structure": "LL"},
]
bearish_candles = [
    candle(104, 105, 103, 104, 0),
    candle(104, 111, 103, 108, 1),
    candle(108, 109, 99, 101, 2),
    candle(101, 106, 100, 103, 3),
    candle(103, 104, 89, 92, 4),
    candle(92, 104, 91, 104, 5),
    candle(104, 107, 103, 106, 6),
]
events = detect_structure_breaks(bearish_candles, bearish_pivots, 0.001)
up = [e for e in events if e["direction"] == "UP" and e["pivot_index"] == 3]
assert up
assert up[-1]["kind"] == "CHOCH"
assert up[-1]["prior_state"] == "BEARISH_STRUCTURE"

# A raw pivot is not usable until its right-side confirmation candles have closed.
confirmation_series = [
    candle(100, 101, 99, 100, 0),
    candle(100, 102, 99, 101, 1),
    candle(101, 110, 100, 105, 2),  # candidate HIGH
    candle(105, 109, 104, 108, 3),  # first right candle
    candle(108, 109, 107, 108, 4),  # confirms pivot at index 2
    candle(108, 112, 108, 112, 5),  # only now may break confirmed high
]
raw = detect_raw_pivots(confirmation_series, left=2, right=2)
high = [p for p in raw if p["type"] == "HIGH" and p["index"] == 2][0]
assert high["confirmed_index"] == 4

classified = classify_pivots(normalize_pivots(raw), 0.001)
events = detect_structure_breaks(confirmation_series, classified, 0.001)
assert not any(e["pivot_index"] == 2 and e["break_index"] <= 4 for e in events)
assert any(e["pivot_index"] == 2 and e["break_index"] == 5 for e in events)

print("STRUCTURAL CORE TESTS: OK")
