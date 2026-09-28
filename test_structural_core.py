from structural_core import (
    adaptive_tolerance_pct,
    normalize_pivots,
    classify_pivots,
    structure_state,
    level_reaction,
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

print("STRUCTURAL CORE TESTS: OK")
