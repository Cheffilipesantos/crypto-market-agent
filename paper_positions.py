"""
PAPER POSITION / OUTCOME ENGINE V1

- PAPER ONLY
- Nenhuma execução real
- Controla ciclo de vida das posições
- Bloqueia posição duplicada
- Registra STOP / T1 / T2 / T3
- Calcula PnL, R, MAE, MFE e duração
"""

import sqlite3
from datetime import datetime, timezone


TEST_ERA = "STRUCTURAL_SHADOW_V1_20260929"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def init_storage(db_file):
    conn = sqlite3.connect(db_file)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            test_era TEXT NOT NULL,

            asset TEXT NOT NULL,
            side TEXT NOT NULL,
            setup_type TEXT NOT NULL,

            opened_at TEXT NOT NULL,
            closed_at TEXT,

            entry REAL NOT NULL,
            stop REAL NOT NULL,

            t1 REAL NOT NULL,
            t2 REAL NOT NULL,
            t3 REAL NOT NULL,

            leverage INTEGER,
            risk_usd REAL NOT NULL,
            notional_usd REAL NOT NULL,
            tactical_score REAL,

            status TEXT NOT NULL DEFAULT 'OPEN',

            t1_hit INTEGER NOT NULL DEFAULT 0,
            t2_hit INTEGER NOT NULL DEFAULT 0,
            t3_hit INTEGER NOT NULL DEFAULT 0,

            t1_at TEXT,
            t2_at TEXT,
            t3_at TEXT,

            exit_reason TEXT,
            exit_price REAL,

            realized_pnl_usd REAL NOT NULL DEFAULT 0,
            realized_r REAL NOT NULL DEFAULT 0,

            last_price REAL,

            highest_price REAL,
            lowest_price REAL,

            mfe_usd REAL NOT NULL DEFAULT 0,
            mae_usd REAL NOT NULL DEFAULT 0,

            updated_at TEXT NOT NULL,
            last_candle_ms INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS post_trade_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER NOT NULL UNIQUE,
            test_era TEXT NOT NULL,
            asset TEXT NOT NULL,
            side TEXT NOT NULL,
            exit_reason TEXT NOT NULL,
            opened_at TEXT NOT NULL,
            closed_at TEXT NOT NULL,
            entry REAL NOT NULL,
            stop REAL NOT NULL,
            t1 REAL NOT NULL,
            t2 REAL NOT NULL,
            t3 REAL NOT NULL,
            exit_price REAL NOT NULL,
            tracking_status TEXT NOT NULL DEFAULT 'ACTIVE',
            post_high REAL,
            post_low REAL,
            post_last_price REAL,
            post_mfe_usd REAL NOT NULL DEFAULT 0,
            post_mae_usd REAL NOT NULL DEFAULT 0,
            post_t1_hit INTEGER NOT NULL DEFAULT 0,
            post_t2_hit INTEGER NOT NULL DEFAULT 0,
            post_t3_hit INTEGER NOT NULL DEFAULT 0,
            post_t1_at TEXT,
            post_t2_at TEXT,
            post_t3_at TEXT,
            checkpoint_1h_price REAL,
            checkpoint_3h_price REAL,
            checkpoint_6h_price REAL,
            checkpoint_12h_price REAL,
            checkpoint_24h_price REAL,
            checkpoint_1h_at TEXT,
            checkpoint_3h_at TEXT,
            checkpoint_6h_at TEXT,
            checkpoint_12h_at TEXT,
            checkpoint_24h_at TEXT,
            last_candle_ms INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(position_id) REFERENCES paper_positions(id)
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_post_trade_active
        ON post_trade_tracking (tracking_status, asset)
    """)

    columns = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(paper_positions)"
        ).fetchall()
    }

    if "last_candle_ms" not in columns:
        conn.execute(
            """
            ALTER TABLE paper_positions
            ADD COLUMN last_candle_ms INTEGER
            NOT NULL DEFAULT 0
            """
        )

    conn.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_paper_positions_status
        ON paper_positions (
            test_era,
            asset,
            side,
            setup_type,
            status
        )
    """)

    conn.commit()
    conn.close()


def get_open_position(
    db_file,
    asset,
    side=None,
    setup_type=None
):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row

    sql = """
        SELECT *
        FROM paper_positions
        WHERE test_era = ?
          AND asset = ?
          AND status = 'OPEN'
    """

    params = [TEST_ERA, asset]

    if side is not None:
        sql += " AND side = ?"
        params.append(side)

    if setup_type is not None:
        sql += " AND setup_type = ?"
        params.append(setup_type)

    sql += " ORDER BY id DESC LIMIT 1"

    row = conn.execute(
        sql,
        params
    ).fetchone()

    conn.close()

    return dict(row) if row else None


def has_open_position(
    db_file,
    asset,
    side,
    setup_type
):
    return (
        get_open_position(
            db_file,
            asset,
            side,
            setup_type
        )
        is not None
    )


def open_position(db_file, data, plan):
    if plan["status"] != "PAPER_READY":
        return None

    asset = data["asset"]
    side = plan["side"]
    setup_type = plan["setup_type"]

    # Uma posição OPEN do mesmo ativo/lado/setup
    # impede reentrada duplicada.
    existing = get_open_position(
        db_file,
        asset,
        side,
        setup_type
    )

    if existing:
        return None

    ts = data["timestamp"]
    entry = float(plan["entry"])

    conn = sqlite3.connect(db_file)

    cur = conn.execute(
        """
        INSERT INTO paper_positions (
            test_era,
            asset,
            side,
            setup_type,
            opened_at,
            entry,
            stop,
            t1,
            t2,
            t3,
            leverage,
            risk_usd,
            notional_usd,
            tactical_score,
            status,
            last_price,
            highest_price,
            lowest_price,
            updated_at
        )
        VALUES (
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            'OPEN',
            ?, ?, ?, ?
        )
        """,
        (
            TEST_ERA,
            asset,
            side,
            setup_type,
            ts,
            entry,
            float(plan["stop"]),
            float(plan["t1"]),
            float(plan["t2"]),
            float(plan["t3"]),
            int(plan["leverage"]),
            float(plan["risk_usd"]),
            float(plan["notional_usd"]),
            float(plan["tactical_score"]),
            entry,
            entry,
            entry,
            ts,
        )
    )

    position_id = cur.lastrowid

    conn.commit()
    conn.close()

    return position_id


def directional_pnl(
    side,
    entry,
    price,
    notional
):
    if side == "LONG":
        return (
            (price - entry)
            / entry
        ) * notional

    return (
        (entry - price)
        / entry
    ) * notional


def update_position(
    db_file,
    asset,
    price,
    timestamp,
    candle_high=None,
    candle_low=None
):
    """
    Atualiza uma posição PAPER usando preço observado
    ou high/low de candle.

    Com candle_high/candle_low, STOP e targets podem ser
    detectados mesmo que o close não tenha parado no nível.
    """

    position = get_open_position(
        db_file,
        asset
    )

    if not position:
        return None

    price = float(price)

    high_now = (
        float(candle_high)
        if candle_high is not None
        else price
    )

    low_now = (
        float(candle_low)
        if candle_low is not None
        else price
    )

    entry = float(position["entry"])
    stop = float(position["stop"])

    t1 = float(position["t1"])
    t2 = float(position["t2"])
    t3 = float(position["t3"])

    risk = float(position["risk_usd"])
    notional = float(position["notional_usd"])

    high = max(
        float(position["highest_price"]),
        high_now
    )

    low = min(
        float(position["lowest_price"]),
        low_now
    )

    side = position["side"]

    if side == "LONG":
        best_price = high
        worst_price = low

        stop_hit = low_now <= stop
        t1_hit = high_now >= t1
        t2_hit = high_now >= t2
        t3_hit = high_now >= t3

    else:
        best_price = low
        worst_price = high

        stop_hit = high_now >= stop
        t1_hit = low_now <= t1
        t2_hit = low_now <= t2
        t3_hit = low_now <= t3

    mfe = max(
        0,
        directional_pnl(
            side,
            entry,
            best_price,
            notional
        )
    )

    adverse = directional_pnl(
        side,
        entry,
        worst_price,
        notional
    )

    mae = min(0, adverse)

    old_t1 = int(position["t1_hit"])
    old_t2 = int(position["t2_hit"])
    old_t3 = int(position["t3_hit"])

    new_t1 = old_t1 or int(t1_hit)
    new_t2 = old_t2 or int(t2_hit)
    new_t3 = old_t3 or int(t3_hit)

    t1_at = position["t1_at"]
    t2_at = position["t2_at"]
    t3_at = position["t3_at"]

    events = []

    # OHLC de 1 minuto não informa a ordem intraminuto.
    # Se STOP e algum target novo aparecem no mesmo candle,
    # marcamos a ocorrência como ambígua em vez de inventar
    # qual aconteceu primeiro.
    new_target_same_candle = (
        (t1_hit and not old_t1)
        or (t2_hit and not old_t2)
        or (t3_hit and not old_t3)
    )

    ambiguous = (
        candle_high is not None
        and candle_low is not None
        and stop_hit
        and new_target_same_candle
    )

    if ambiguous:
        conn = sqlite3.connect(db_file)

        conn.execute(
            """
            UPDATE paper_positions
            SET
                status = 'AMBIGUOUS',
                closed_at = ?,
                exit_reason = 'AMBIGUOUS_1M',
                exit_price = ?,
                last_price = ?,
                highest_price = ?,
                lowest_price = ?,
                mfe_usd = ?,
                mae_usd = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                timestamp,
                price,
                price,
                high,
                low,
                mfe,
                mae,
                timestamp,
                position["id"],
            )
        )

        conn.commit()
        conn.close()

        return {
            "id": position["id"],
            "asset": asset,
            "side": side,
            "status": "AMBIGUOUS",
            "events": ["AMBIGUOUS"],
            "exit_reason": "AMBIGUOUS_1M",
            "pnl_usd": 0.0,
            "pnl_r": 0.0,
        }

    if new_t1 and not old_t1:
        t1_at = timestamp
        events.append("T1")

    if new_t2 and not old_t2:
        t2_at = timestamp
        events.append("T2")

    if new_t3 and not old_t3:
        t3_at = timestamp
        events.append("T3")

    status = "OPEN"
    exit_reason = None
    exit_price = None
    pnl = 0.0
    pnl_r = 0.0
    closed_at = None

    if stop_hit:
        status = "CLOSED"
        exit_reason = "STOP"
        exit_price = stop

        pnl = directional_pnl(
            side,
            entry,
            stop,
            notional
        )

        pnl_r = (
            pnl / risk
            if risk > 0
            else 0
        )

        closed_at = timestamp
        events.append("STOP")

    elif t3_hit:
        status = "CLOSED"
        exit_reason = "T3"
        exit_price = t3

        pnl = directional_pnl(
            side,
            entry,
            t3,
            notional
        )

        pnl_r = (
            pnl / risk
            if risk > 0
            else 0
        )

        closed_at = timestamp

    conn = sqlite3.connect(db_file)

    conn.execute(
        """
        UPDATE paper_positions
        SET
            status = ?,
            closed_at = ?,
            t1_hit = ?,
            t2_hit = ?,
            t3_hit = ?,
            t1_at = ?,
            t2_at = ?,
            t3_at = ?,
            exit_reason = ?,
            exit_price = ?,
            realized_pnl_usd = ?,
            realized_r = ?,
            last_price = ?,
            highest_price = ?,
            lowest_price = ?,
            mfe_usd = ?,
            mae_usd = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            closed_at,
            new_t1,
            new_t2,
            new_t3,
            t1_at,
            t2_at,
            t3_at,
            exit_reason,
            exit_price,
            pnl,
            pnl_r,
            price,
            high,
            low,
            mfe,
            mae,
            timestamp,
            position["id"],
        )
    )

    conn.commit()
    conn.close()

    if status == "CLOSED":
        start_post_trade_tracking(
            db_file,
            position["id"]
        )

    return {
        "id": position["id"],
        "asset": asset,
        "side": side,
        "status": status,
        "events": events,
        "exit_reason": exit_reason,
        "pnl_usd": pnl,
        "pnl_r": pnl_r,
    }


def format_event(result):
    if not result:
        return None

    if not result["events"]:
        return None

    lines = [
        f"📊 PAPER POSITION — {result['asset']}",
        f"Direção: {result['side']}",
    ]

    for event in result["events"]:
        if event == "T1":
            lines.append("🎯 T1 atingido")
        elif event == "T2":
            lines.append("🎯 T2 atingido")
        elif event == "T3":
            lines.append("🏆 T3 atingido")
        elif event == "STOP":
            lines.append("🛑 STOP atingido")

    if result["status"] == "CLOSED":
        lines.append(
            f"PnL: ${result['pnl_usd']:+,.2f} "
            f"({result['pnl_r']:+.2f}R)"
        )
        lines.append("Status: CLOSED")

    lines.append(
        "🛡️ PAPER ONLY — trading real DESATIVADO"
    )

    return "\n".join(lines)


def performance_report(db_file):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT *
        FROM paper_positions
        WHERE test_era = ?
        ORDER BY id
        """,
        (TEST_ERA,)
    ).fetchall()

    conn.close()

    if not rows:
        return (
            "PAPER PERFORMANCE\n"
            "Nova era ainda sem operações."
        )

    closed = [
        r for r in rows
        if r["status"] == "CLOSED"
    ]

    opened = [
        r for r in rows
        if r["status"] == "OPEN"
    ]

    wins = [
        r for r in closed
        if r["realized_pnl_usd"] > 0
    ]

    losses = [
        r for r in closed
        if r["realized_pnl_usd"] < 0
    ]

    realized = sum(
        r["realized_pnl_usd"]
        for r in closed
    )

    gross_profit = sum(
        r["realized_pnl_usd"]
        for r in wins
    )

    gross_loss = abs(sum(
        r["realized_pnl_usd"]
        for r in losses
    ))

    win_rate = (
        len(wins) / len(closed) * 100
        if closed else 0
    )

    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0
        else 0
    )

    expectancy_r = (
        sum(r["realized_r"] for r in closed)
        / len(closed)
        if closed else 0
    )

    lines = [
        "========== PAPER PERFORMANCE ==========",
        f"Era: {TEST_ERA}",
        f"Operações: {len(rows)}",
        f"Abertas: {len(opened)}",
        f"Fechadas: {len(closed)}",
        f"Positivas: {len(wins)}",
        f"Negativas: {len(losses)}",
        f"Win rate: {win_rate:.1f}%",
        f"PnL realizado: ${realized:+,.2f}",
        f"Profit Factor: {profit_factor:.2f}",
        f"Expectancy: {expectancy_r:+.2f}R",
        "======================================="
    ]

    return "\n".join(lines)

def start_post_trade_tracking(db_file, position_id):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row

    position = conn.execute(
        """
        SELECT *
        FROM paper_positions
        WHERE id = ?
          AND test_era = ?
          AND status = 'CLOSED'
          AND exit_reason IS NOT NULL
          AND closed_at IS NOT NULL
          AND exit_price IS NOT NULL
        """,
        (position_id, TEST_ERA)
    ).fetchone()

    if not position:
        conn.close()
        return False

    timestamp = now_iso()

    conn.execute(
        """
        INSERT OR IGNORE INTO post_trade_tracking (
            position_id,
            test_era,
            asset,
            side,
            exit_reason,
            opened_at,
            closed_at,
            entry,
            stop,
            t1,
            t2,
            t3,
            exit_price,
            tracking_status,
            post_high,
            post_low,
            post_last_price,
            last_candle_ms,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE',
                ?, ?, ?, 0, ?, ?)
        """,
        (
            position["id"],
            position["test_era"],
            position["asset"],
            position["side"],
            position["exit_reason"],
            position["opened_at"],
            position["closed_at"],
            position["entry"],
            position["stop"],
            position["t1"],
            position["t2"],
            position["t3"],
            position["exit_price"],
            position["exit_price"],
            position["exit_price"],
            position["exit_price"],
            timestamp,
            timestamp,
        )
    )

    inserted = conn.total_changes > 0
    conn.commit()
    conn.close()

    return inserted

def get_active_post_trade_trackers(db_file, asset=None):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row

    sql = """
        SELECT *
        FROM post_trade_tracking
        WHERE test_era = ?
          AND tracking_status = 'ACTIVE'
    """

    params = [TEST_ERA]

    if asset is not None:
        sql += " AND asset = ?"
        params.append(asset)

    sql += " ORDER BY closed_at, id"

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    return [dict(row) for row in rows]

def update_post_trade_candle(
    db_file,
    tracker_id,
    candle_ms,
    candle_high,
    candle_low,
    candle_close,
    candle_ts
):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row

    row = conn.execute(
        """
        SELECT *
        FROM post_trade_tracking
        WHERE id = ?
          AND test_era = ?
          AND tracking_status = 'ACTIVE'
        """,
        (tracker_id, TEST_ERA)
    ).fetchone()

    if not row:
        conn.close()
        return None

    if int(candle_ms) <= int(row["last_candle_ms"] or 0):
        conn.close()
        return None

    high_now = float(candle_high)
    low_now = float(candle_low)
    close_now = float(candle_close)

    post_high = max(
        float(row["post_high"] if row["post_high"] is not None else row["exit_price"]),
        high_now
    )

    post_low = min(
        float(row["post_low"] if row["post_low"] is not None else row["exit_price"]),
        low_now
    )

    exit_price = float(row["exit_price"])
    notional_row = conn.execute(
        "SELECT notional_usd FROM paper_positions WHERE id = ?",
        (row["position_id"],)
    ).fetchone()

    notional = float(notional_row["notional_usd"]) if notional_row else 0.0
    side = row["side"]

    if side == "LONG":
        favorable_price = post_high
        adverse_price = post_low
        post_mfe = max(
            0.0,
            ((favorable_price - exit_price) / exit_price) * notional
        )
        post_mae = min(
            0.0,
            ((adverse_price - exit_price) / exit_price) * notional
        )
        hit_t1 = post_high >= float(row["t1"])
        hit_t2 = post_high >= float(row["t2"])
        hit_t3 = post_high >= float(row["t3"])
    else:
        favorable_price = post_low
        adverse_price = post_high
        post_mfe = max(
            0.0,
            ((exit_price - favorable_price) / exit_price) * notional
        )
        post_mae = min(
            0.0,
            ((exit_price - adverse_price) / exit_price) * notional
        )
        hit_t1 = post_low <= float(row["t1"])
        hit_t2 = post_low <= float(row["t2"])
        hit_t3 = post_low <= float(row["t3"])

    t1_at = row["post_t1_at"]
    t2_at = row["post_t2_at"]
    t3_at = row["post_t3_at"]

    if hit_t1 and not row["post_t1_hit"]:
        t1_at = candle_ts

    if hit_t2 and not row["post_t2_hit"]:
        t2_at = candle_ts

    if hit_t3 and not row["post_t3_hit"]:
        t3_at = candle_ts

    closed_dt = datetime.fromisoformat(row["closed_at"])
    candle_dt = datetime.fromisoformat(candle_ts)
    elapsed_hours = (
        candle_dt - closed_dt
    ).total_seconds() / 3600.0

    checkpoints = {
        1: (
            "checkpoint_1h_price",
            "checkpoint_1h_at"
        ),
        3: (
            "checkpoint_3h_price",
            "checkpoint_3h_at"
        ),
        6: (
            "checkpoint_6h_price",
            "checkpoint_6h_at"
        ),
        12: (
            "checkpoint_12h_price",
            "checkpoint_12h_at"
        ),
        24: (
            "checkpoint_24h_price",
            "checkpoint_24h_at"
        ),
    }

    checkpoint_values = {}

    for hours, (price_col, at_col) in checkpoints.items():
        if (
            elapsed_hours >= hours
            and row[price_col] is None
        ):
            checkpoint_values[price_col] = close_now
            checkpoint_values[at_col] = candle_ts

    tracking_status = (
        "COMPLETED"
        if elapsed_hours >= 24
        else "ACTIVE"
    )

    conn.execute(
        """
        UPDATE post_trade_tracking
        SET
            post_high = ?,
            post_low = ?,
            post_last_price = ?,
            post_mfe_usd = ?,
            post_mae_usd = ?,
            post_t1_hit = ?,
            post_t2_hit = ?,
            post_t3_hit = ?,
            post_t1_at = ?,
            post_t2_at = ?,
            post_t3_at = ?,
            checkpoint_1h_price = COALESCE(?, checkpoint_1h_price),
            checkpoint_3h_price = COALESCE(?, checkpoint_3h_price),
            checkpoint_6h_price = COALESCE(?, checkpoint_6h_price),
            checkpoint_12h_price = COALESCE(?, checkpoint_12h_price),
            checkpoint_24h_price = COALESCE(?, checkpoint_24h_price),
            checkpoint_1h_at = COALESCE(?, checkpoint_1h_at),
            checkpoint_3h_at = COALESCE(?, checkpoint_3h_at),
            checkpoint_6h_at = COALESCE(?, checkpoint_6h_at),
            checkpoint_12h_at = COALESCE(?, checkpoint_12h_at),
            checkpoint_24h_at = COALESCE(?, checkpoint_24h_at),
            tracking_status = ?,
            last_candle_ms = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            post_high,
            post_low,
            close_now,
            post_mfe,
            post_mae,
            int(hit_t1),
            int(hit_t2),
            int(hit_t3),
            t1_at,
            t2_at,
            t3_at,
            checkpoint_values.get("checkpoint_1h_price"),
            checkpoint_values.get("checkpoint_3h_price"),
            checkpoint_values.get("checkpoint_6h_price"),
            checkpoint_values.get("checkpoint_12h_price"),
            checkpoint_values.get("checkpoint_24h_price"),
            checkpoint_values.get("checkpoint_1h_at"),
            checkpoint_values.get("checkpoint_3h_at"),
            checkpoint_values.get("checkpoint_6h_at"),
            checkpoint_values.get("checkpoint_12h_at"),
            checkpoint_values.get("checkpoint_24h_at"),
            tracking_status,
            int(candle_ms),
            candle_ts,
            tracker_id,
        )
    )

    conn.commit()
    conn.close()

    return True
