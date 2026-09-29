import os
import sqlite3
import tempfile

import paper_positions
from shadow_engine import (
    init_storage,
    process_completed_comparison_autopsies,
)


def insert_official(conn, era, asset, side, realized_r, exit_reason="STOP"):
    cur = conn.execute(
        """
        INSERT INTO paper_positions (
            test_era, asset, side, setup_type, opened_at, closed_at,
            entry, stop, t1, t2, t3, leverage, risk_usd, notional_usd,
            tactical_score, status, exit_reason, exit_price,
            realized_pnl_usd, realized_r, last_price, highest_price,
            lowest_price, mfe_usd, mae_usd, updated_at, last_candle_ms
        ) VALUES (?, ?, ?, 'TEST', '2026-09-29T00:00:00+00:00',
                  '2026-09-29T01:00:00+00:00',
                  100, 101, 99, 98, 97, 1, 1, 30, 70,
                  'CLOSED', ?, 101, ?, ?, 101, 101, 97, 3, -1,
                  '2026-09-29T01:00:00+00:00', 1)
        """,
        (era, asset, side, exit_reason, realized_r, realized_r),
    )
    return cur.lastrowid


def insert_shadow(conn, era, asset, side, pnl_r, exit_reason="T3"):
    cur = conn.execute(
        """
        INSERT INTO shadow_positions (
            test_era, asset, side, setup_type, entry, stop, t1, t2, t3,
            risk_per_unit, status, opened_at, closed_at, exit_reason, pnl_r,
            mfe_r, mae_r, t1_hit, t2_hit, last_candle_ms, entry_snapshot_id
        ) VALUES (?, ?, ?, 'STRUCTURAL_ALIGNMENT', 100, 101, 99, 98, 97,
                  1, 'CLOSED', '2026-09-29T00:00:00+00:00',
                  '2026-09-29T01:00:00+00:00', ?, ?, 3, -1, 1, 1, 1, 1)
        """,
        (era, asset, side, exit_reason, pnl_r),
    )
    return cur.lastrowid


def insert_comparison(conn, era, asset, category, official_id=None, shadow_id=None,
                      official_side=None, shadow_side=None):
    cur = conn.execute(
        """
        INSERT INTO shadow_comparisons (
            test_era, timestamp, asset, snapshot_id, category,
            official_position_id, shadow_position_id, official_side, shadow_side
        ) VALUES (?, '2026-09-29T00:00:00+00:00', ?, 1, ?, ?, ?, ?, ?)
        """,
        (era, asset, category, official_id, shadow_id, official_side, shadow_side),
    )
    return cur.lastrowid


fd, db = tempfile.mkstemp(suffix=".db")
os.close(fd)
ERA = "AUTOPSY_TEST"

try:
    paper_positions.init_storage(db)
    init_storage(db)
    conn = sqlite3.connect(db)

    o_loss = insert_official(conn, ERA, "LOSS", "SHORT", -1.0)
    insert_comparison(conn, ERA, "LOSS", "OFFICIAL_ONLY", official_id=o_loss,
                      official_side="SHORT")

    o_win = insert_official(conn, ERA, "WIN", "SHORT", 3.0, "T3")
    insert_comparison(conn, ERA, "WIN", "OFFICIAL_ONLY", official_id=o_win,
                      official_side="SHORT")

    s_win = insert_shadow(conn, ERA, "SHADOW", "LONG", 3.0)
    insert_comparison(conn, ERA, "SHADOW", "SHADOW_ONLY", shadow_id=s_win,
                      shadow_side="LONG")

    o_pair = insert_official(conn, ERA, "PAIR", "SHORT", -1.0)
    s_pair = insert_shadow(conn, ERA, "PAIR", "SHORT", 3.0)
    insert_comparison(conn, ERA, "PAIR", "BOTH_SAME_DIRECTION",
                      official_id=o_pair, shadow_id=s_pair,
                      official_side="SHORT", shadow_side="SHORT")
    conn.commit()
    conn.close()

    created = process_completed_comparison_autopsies(db, ERA)
    classes = {x["classification"] for x in created}
    assert "SHADOW_AVOIDED_OFFICIAL_LOSS" in classes
    assert "SHADOW_MISSED_OFFICIAL_WIN" in classes
    assert "SHADOW_ONLY_WIN" in classes
    assert "PAIRED_SHADOW_HIGHER_R" in classes

    pair = [x for x in created if x["classification"] == "PAIRED_SHADOW_HIGHER_R"][0]
    assert pair["shadow_minus_official_r"] == 4.0

    # Idempotence: completed comparisons are autopsied exactly once.
    assert process_completed_comparison_autopsies(db, ERA) == []

    conn = sqlite3.connect(db)
    count = conn.execute(
        "SELECT COUNT(*) FROM comparison_autopsies WHERE test_era = ?",
        (ERA,),
    ).fetchone()[0]
    conn.close()
    assert count == 4

    print("SHADOW AUTOPSY TESTS: OK")
finally:
    os.remove(db)
