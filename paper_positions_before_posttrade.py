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


TEST_ERA = "PAPER_V2_MARGIN30_20260923"


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
