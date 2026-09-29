from shadow_engine import build_structural_plan


def core(state, pivots=None, atr=1.0, tolerance=0.002):
    return {
        "state": state,
        "recent_pivots": pivots or [],
        "atr": atr,
        "tolerance_pct": tolerance,
        "last_break": None,
        "levels": [],
    }


def context(states, pivots_5m=None):
    return {
        "mtf": {
            tf: {
                "canonical": core(
                    state,
                    pivots=pivots_5m if tf == "5m" else None,
                )
            }
            for tf, state in states.items()
        }
    }


data = {"price": 100.0}

# Fully aligned bearish structure should produce an independent SHORT plan.
bearish = context(
    {
        "4h": "BEARISH_STRUCTURE",
        "2h": "BEARISH_STRUCTURE",
        "1h": "BEARISH_STRUCTURE",
        "15m": "BEARISH_STRUCTURE",
        "5m": "BEARISH_STRUCTURE",
        "1m": "BEARISH_STRUCTURE",
    },
    pivots_5m=[
        {"type": "LOW", "price": 98.0},
        {"type": "HIGH", "price": 103.0},
    ],
)
plan = build_structural_plan(data, bearish)
assert plan["decision"] == "SHORT"
assert plan["stop"] > plan["entry"]
assert plan["t1"] < plan["entry"]
assert plan["t3"] < plan["t2"] < plan["t1"]

# Opposite 1H/15m structure must veto a trade.
conflict = context(
    {
        "4h": "BEARISH_STRUCTURE",
        "2h": "BEARISH_STRUCTURE",
        "1h": "BEARISH_STRUCTURE",
        "15m": "BULLISH_STRUCTURE",
        "5m": "BEARISH_STRUCTURE",
        "1m": "BEARISH_STRUCTURE",
    }
)
plan = build_structural_plan(data, conflict)
assert plan["decision"] == "NO_TRADE"
assert plan["setup_type"] == "STRUCTURAL_CONFLICT"

# If both macro timeframes oppose the lower-timeframe thesis, veto it.
macro_conflict = context(
    {
        "4h": "BULLISH_STRUCTURE",
        "2h": "BULLISH_STRUCTURE",
        "1h": "BEARISH_STRUCTURE",
        "15m": "BEARISH_STRUCTURE",
        "5m": "BEARISH_STRUCTURE",
        "1m": "BEARISH_STRUCTURE",
    }
)
plan = build_structural_plan(data, macro_conflict)
assert plan["decision"] == "NO_TRADE"
assert plan["setup_type"] == "STRUCTURAL_MACRO_CONFLICT"

# Direction may exist while execution is not ready.
wait_execution = context(
    {
        "4h": "BEARISH_STRUCTURE",
        "2h": "BEARISH_STRUCTURE",
        "1h": "BEARISH_STRUCTURE",
        "15m": "BEARISH_STRUCTURE",
        "5m": "BEARISH_STRUCTURE",
        "1m": "TRANSITION_STRUCTURE",
    }
)
plan = build_structural_plan(data, wait_execution)
assert plan["decision"] == "WAIT"
assert plan["setup_type"] == "STRUCTURAL_WAIT_EXECUTION"


# A clustered opposing level before 1R must reject the entry and preserve room evidence.
room_blocked = context(
    {
        "4h": "BEARISH_STRUCTURE",
        "2h": "BEARISH_STRUCTURE",
        "1h": "BEARISH_STRUCTURE",
        "15m": "BEARISH_STRUCTURE",
        "5m": "BEARISH_STRUCTURE",
        "1m": "BEARISH_STRUCTURE",
    },
    pivots_5m=[
        {"type": "LOW", "price": 98.0},
        {"type": "HIGH", "price": 103.0},
    ],
)
room_blocked["mtf"]["15m"]["canonical"]["levels"] = [
    {"price": 98.0, "touches": 3, "strength": 3, "current_role": "SUPPORT"},
]
plan = build_structural_plan(data, room_blocked)
assert plan["decision"] == "NO_TRADE"
assert plan["setup_type"] == "STRUCTURAL_ROOM_REJECTED"
assert plan["evidence"]["next_obstacle"]["price"] == 98.0
assert plan["evidence"]["obstacle_room_r"] < 1.0

# A level beyond 1R is recorded but does not veto the structural plan.
room_clear = context(
    {
        "4h": "BEARISH_STRUCTURE",
        "2h": "BEARISH_STRUCTURE",
        "1h": "BEARISH_STRUCTURE",
        "15m": "BEARISH_STRUCTURE",
        "5m": "BEARISH_STRUCTURE",
        "1m": "BEARISH_STRUCTURE",
    },
    pivots_5m=[
        {"type": "LOW", "price": 98.0},
        {"type": "HIGH", "price": 103.0},
    ],
)
room_clear["mtf"]["1h"]["canonical"]["levels"] = [
    {"price": 94.0, "touches": 3, "strength": 3, "current_role": "SUPPORT"},
]
plan = build_structural_plan(data, room_clear)
assert plan["decision"] == "SHORT"
assert plan["evidence"]["obstacle_room_r"] > 1.0

print("SHADOW STRUCTURAL TESTS: OK")
