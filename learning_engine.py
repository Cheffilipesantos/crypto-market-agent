"""
LEARNING ENGINE V0.1
Memória e observabilidade do Crypto Market Agent.

Nesta versão:
- não altera o motor OFICIAL;
- não executa trades;
- registra snapshots das decisões;
- prepara memória para reflexão e SHADOW.
"""

import json
import sqlite3
from datetime import datetime, timezone


def init_storage(db_file):
    conn = sqlite3.connect(db_file)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS decision_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_era TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            asset TEXT NOT NULL,

            price REAL,
            regime TEXT,
            setup_direction TEXT,
            timing TEXT,
            decision TEXT,

            trading_score REAL,
            long_score REAL,
            short_score REAL,
            tactical_score REAL,
            confluence INTEGER,

            rsi_1h REAL,
            rsi_4h REAL,
            ema20_1h REAL,
            ema50_1h REAL,
            ema20_4h REAL,
            ema50_4h REAL,
            atr_1h REAL,
            momentum_4h REAL,

            funding REAL,
            oi REAL,
            oi_delta REAL,

            leverage_status TEXT,
            leverage_side TEXT,
            setup_type TEXT,

            entry REAL,
            stop REAL,
            stop_pct REAL,
            t1 REAL,
            t2 REAL,
            t3 REAL,

            thesis TEXT,
            counter_thesis TEXT,
            plan_reason TEXT,

            full_snapshot TEXT,

            created_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_decision_snapshots_asset_time
        ON decision_snapshots (
            test_era,
            asset,
            timestamp
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS learning_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_era TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            asset TEXT NOT NULL,
            event_type TEXT NOT NULL,
            position_id INTEGER,
            snapshot_id INTEGER,
            details TEXT,
            created_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_learning_events_asset
        ON learning_events (
            test_era,
            asset,
            event_type,
            timestamp
        )
    """)

    conn.commit()
    conn.close()


def save_decision_snapshot(
    db_file,
    test_era,
    data,
    plan
):
    now = datetime.now(timezone.utc).isoformat()

    snapshot = {
        "data": data,
        "plan": plan,
    }

    conn = sqlite3.connect(db_file)

    cur = conn.execute(
        """
        INSERT INTO decision_snapshots (
            test_era,
            timestamp,
            asset,
            price,
            regime,
            setup_direction,
            timing,
            decision,
            trading_score,
            long_score,
            short_score,
            tactical_score,
            confluence,
            rsi_1h,
            rsi_4h,
            ema20_1h,
            ema50_1h,
            ema20_4h,
            ema50_4h,
            atr_1h,
            momentum_4h,
            funding,
            oi,
            oi_delta,
            leverage_status,
            leverage_side,
            setup_type,
            entry,
            stop,
            stop_pct,
            t1,
            t2,
            t3,
            thesis,
            counter_thesis,
            plan_reason,
            full_snapshot,
            created_at
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            test_era,
            data["timestamp"],
            data["asset"],
            data.get("price"),
            data.get("regime"),
            data.get("setup_direction"),
            data.get("timing"),
            data.get("decision"),
            data.get("trading_score"),
            data.get("long_score"),
            data.get("short_score"),
            plan.get("tactical_score"),
            data.get("confluence"),
            data.get("rsi_1h"),
            data.get("rsi_4h"),
            data.get("ema20_1h"),
            data.get("ema50_1h"),
            data.get("ema20_4h"),
            data.get("ema50_4h"),
            data.get("atr_1h"),
            data.get("momentum_4h"),
            data.get("funding"),
            data.get("oi"),
            data.get("oi_delta"),
            plan.get("status"),
            plan.get("side"),
            plan.get("setup_type"),
            plan.get("entry"),
            plan.get("stop"),
            plan.get("stop_pct"),
            plan.get("t1"),
            plan.get("t2"),
            plan.get("t3"),
            data.get("thesis"),
            data.get("counter_thesis"),
            plan.get("reason"),
            json.dumps(
                snapshot,
                ensure_ascii=False,
                default=str
            ),
            now,
        )
    )

    snapshot_id = cur.lastrowid

    conn.commit()
    conn.close()

    return snapshot_id


def get_last_similar_trade(
    db_file,
    test_era,
    asset,
    side,
    setup_type
):
    if not side or side == "NONE":
        return None

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row

    row = conn.execute(
        """
        SELECT
            id,
            asset,
            side,
            setup_type,
            opened_at,
            closed_at,
            exit_reason,
            realized_r,
            t1_hit,
            t2_hit,
            t3_hit,
            entry,
            stop,
            highest_price,
            lowest_price,
            mfe_usd,
            mae_usd
        FROM paper_positions
        WHERE test_era = ?
          AND asset = ?
          AND side = ?
          AND setup_type = ?
          AND status = 'CLOSED'
        ORDER BY closed_at DESC
        LIMIT 1
        """,
        (
            test_era,
            asset,
            side,
            setup_type,
        )
    ).fetchone()

    conn.close()

    if row is None:
        return None

    return dict(row)


def analyze_recent_memory(
    db_file,
    test_era,
    data,
    plan
):
    result = {
        "has_previous": False,
        "previous_position_id": None,
        "previous_exit_reason": None,
        "previous_realized_r": None,
        "minutes_since_close": None,
        "recent_stop": False,
        "same_thesis_warning": False,
    }

    if plan.get("side") in (None, "NONE"):
        return result

    previous = get_last_similar_trade(
        db_file,
        test_era,
        data["asset"],
        plan["side"],
        plan["setup_type"]
    )

    if previous is None:
        return result

    result["has_previous"] = True
    result["previous_position_id"] = previous["id"]
    result["previous_exit_reason"] = previous["exit_reason"]
    result["previous_realized_r"] = previous["realized_r"]

    closed_at = previous.get("closed_at")

    if closed_at:
        closed_dt = datetime.fromisoformat(closed_at)
        now_dt = datetime.fromisoformat(data["timestamp"])

        minutes = (
            now_dt - closed_dt
        ).total_seconds() / 60.0

        result["minutes_since_close"] = max(0.0, minutes)

    if previous["exit_reason"] == "STOP":
        result["recent_stop"] = True

        # Nesta primeira versão é apenas um alerta de memória.
        # Ainda NÃO bloqueia o Oficial.
        if (
            result["minutes_since_close"] is not None
            and result["minutes_since_close"] <= 360
        ):
            result["same_thesis_warning"] = True

    return result

def record_learning_event(
    db_file,
    test_era,
    timestamp,
    asset,
    event_type,
    position_id=None,
    snapshot_id=None,
    details=None,
):
    now = datetime.now(timezone.utc).isoformat()

    if isinstance(details, (dict, list)):
        details = json.dumps(
            details,
            ensure_ascii=False,
            default=str,
        )

    conn = sqlite3.connect(db_file)

    cursor = conn.execute(
        """
        INSERT INTO learning_events (
            test_era,
            timestamp,
            asset,
            event_type,
            position_id,
            snapshot_id,
            details,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            test_era,
            timestamp,
            asset,
            event_type,
            position_id,
            snapshot_id,
            details,
            now,
        ),
    )

    conn.commit()
    event_id = cursor.lastrowid
    conn.close()

    return event_id


def get_position_entry_snapshot(
    db_file,
    test_era,
    position_id
):
    if position_id is None:
        return None

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row

    row = conn.execute(
        """
        SELECT
            ds.*
        FROM learning_events le
        JOIN decision_snapshots ds
          ON ds.id = le.snapshot_id
        WHERE le.test_era = ?
          AND le.position_id = ?
          AND le.event_type = 'OFFICIAL_POSITION_OPENED'
        ORDER BY le.id DESC
        LIMIT 1
        """,
        (
            test_era,
            position_id,
        ),
    ).fetchone()

    conn.close()

    if row is None:
        return None

    return dict(row)

def process_completed_post_trade_reflections(db_file, test_era):
    """
    Cria uma reflexão final de 24h para trackers COMPLETED
    que ainda não possuem POST_TRADE_REFLECTION_24H.

    As classificações são observacionais.
    Não afirmam causalidade sobre direção, timing ou stop.
    """
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT
            pt.*,
            p.realized_r,
            p.realized_pnl_usd,
            p.t1_hit AS trade_t1_hit,
            p.t2_hit AS trade_t2_hit,
            p.t3_hit AS trade_t3_hit,
            p.mfe_usd AS trade_mfe_usd,
            p.mae_usd AS trade_mae_usd
        FROM post_trade_tracking pt
        JOIN paper_positions p
          ON p.id = pt.position_id
        WHERE pt.test_era = ?
          AND pt.tracking_status = 'COMPLETED'
          AND NOT EXISTS (
              SELECT 1
              FROM learning_events le
              WHERE le.test_era = pt.test_era
                AND le.position_id = pt.position_id
                AND le.event_type = 'POST_TRADE_REFLECTION_24H'
          )
        ORDER BY pt.closed_at, pt.id
        """,
        (test_era,)
    ).fetchall()

    conn.close()

    created = []

    for row in rows:
        r = dict(row)

        observations = []

        if r["exit_reason"] == "STOP":
            if int(r["trade_t1_hit"] or 0):
                observations.append("STOP_AFTER_T1")
            else:
                observations.append("STOP_BEFORE_T1")

            if int(r["post_t3_hit"] or 0):
                observations.append("POST_STOP_REACHED_OLD_T3")
            elif int(r["post_t2_hit"] or 0):
                observations.append("POST_STOP_REACHED_OLD_T2")
            elif int(r["post_t1_hit"] or 0):
                observations.append("POST_STOP_REACHED_OLD_T1")
            else:
                observations.append("POST_STOP_NO_OLD_TARGET_24H")

        elif r["exit_reason"] == "T3":
            observations.append("TARGET_T3_COMPLETED")

        details = {
            "exit_reason": r["exit_reason"],
            "realized_r": r["realized_r"],
            "realized_pnl_usd": r["realized_pnl_usd"],
            "trade_t1_hit": int(r["trade_t1_hit"] or 0),
            "trade_t2_hit": int(r["trade_t2_hit"] or 0),
            "trade_t3_hit": int(r["trade_t3_hit"] or 0),
            "trade_mfe_usd": r["trade_mfe_usd"],
            "trade_mae_usd": r["trade_mae_usd"],
            "post_mfe_usd": r["post_mfe_usd"],
            "post_mae_usd": r["post_mae_usd"],
            "post_t1_hit": int(r["post_t1_hit"] or 0),
            "post_t2_hit": int(r["post_t2_hit"] or 0),
            "post_t3_hit": int(r["post_t3_hit"] or 0),
            "checkpoint_1h_price": r["checkpoint_1h_price"],
            "checkpoint_3h_price": r["checkpoint_3h_price"],
            "checkpoint_6h_price": r["checkpoint_6h_price"],
            "checkpoint_12h_price": r["checkpoint_12h_price"],
            "checkpoint_24h_price": r["checkpoint_24h_price"],
            "observations": observations,
        }

        event_id = record_learning_event(
            db_file,
            test_era,
            r["updated_at"],
            r["asset"],
            "POST_TRADE_REFLECTION_24H",
            position_id=r["position_id"],
            snapshot_id=None,
            details=details,
        )

        created.append({
            "event_id": event_id,
            "position_id": r["position_id"],
            "asset": r["asset"],
            "observations": observations,
        })

    return created
