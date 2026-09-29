import os
import tempfile

from shadow_engine import (
    init_storage,
    open_shadow_position,
    update_shadow_position,
)


def candle(t, o, h, l, c):
    return {
        "time": t,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 1,
    }


fd, db = tempfile.mkstemp(suffix=".db")
os.close(fd)

try:
    init_storage(db)

    data = {
        "timestamp": "2026-09-29T00:00:00+00:00",
        "asset": "TEST",
        "price": 100.0,
    }
    plan = {
        "decision": "SHORT",
        "side": "SHORT",
        "setup_type": "STRUCTURAL_ALIGNMENT",
        "entry": 100.0,
        "stop": 101.0,
        "t1": 99.0,
        "t2": 98.0,
        "t3": 97.0,
        "risk_per_unit": 1.0,
    }

    position_id = open_shadow_position(
        db,
        "TEST_ERA",
        data,
        plan,
        1,
        [candle(1000, 100, 100.2, 99.8, 100)],
    )
    assert position_id is not None

    event = update_shadow_position(
        db,
        "TEST_ERA",
        "TEST",
        [
            candle(1000, 100, 100.2, 99.8, 100),
            candle(2000, 100, 100.4, 98.5, 98.8),
        ],
        "2026-09-29T00:01:00+00:00",
    )
    assert event["status"] == "OPEN"
    assert event["t1_hit"] is True
    assert event["t2_hit"] is False

    event = update_shadow_position(
        db,
        "TEST_ERA",
        "TEST",
        [
            candle(3000, 98.8, 99.0, 96.8, 97.1),
        ],
        "2026-09-29T00:02:00+00:00",
    )
    assert event["status"] == "CLOSED"
    assert event["exit_reason"] == "T3"
    assert event["pnl_r"] == 3.0
    assert event["mfe_r"] >= 3.0

    # A same-candle stop+target has unknowable order from OHLC alone.
    data["asset"] = "AMB"
    ambiguous_id = open_shadow_position(
        db,
        "TEST_ERA",
        data,
        plan,
        2,
        [candle(1000, 100, 100.2, 99.8, 100)],
    )
    assert ambiguous_id is not None

    event = update_shadow_position(
        db,
        "TEST_ERA",
        "AMB",
        [candle(2000, 100, 101.2, 98.8, 100)],
        "2026-09-29T00:01:00+00:00",
    )
    assert event["status"] == "CLOSED"
    assert event["exit_reason"] == "AMBIGUOUS"
    assert event["pnl_r"] == 0.0

    print("SHADOW POSITION TESTS: OK")
finally:
    os.remove(db)
