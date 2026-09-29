"""
SHADOW ENGINE V0.1

Camada experimental do Crypto Market Agent.

Regras:
- observa os mesmos dados do agente oficial;
- não abre nem fecha posições;
- não altera leverage_engine.py;
- não envia ordens;
- registra decisões alternativas para comparação futura.
"""

import json
import sqlite3
from datetime import datetime, timezone


def init_storage(db_file):
    conn = sqlite3.connect(db_file)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS shadow_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_era TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            asset TEXT NOT NULL,

            snapshot_id INTEGER,

            official_status TEXT,
            official_side TEXT,
            official_setup_type TEXT,

            shadow_decision TEXT NOT NULL,
            shadow_side TEXT,
            shadow_setup_type TEXT,

            reason TEXT,
            evidence TEXT,

            created_at TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_shadow_decisions_asset_time
        ON shadow_decisions (
            test_era,
            asset,
            timestamp
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS shadow_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_era TEXT NOT NULL,
            asset TEXT NOT NULL,
            side TEXT NOT NULL,
            setup_type TEXT NOT NULL,
            entry REAL NOT NULL,
            stop REAL NOT NULL,
            t1 REAL NOT NULL,
            t2 REAL NOT NULL,
            t3 REAL NOT NULL,
            risk_per_unit REAL NOT NULL,
            status TEXT NOT NULL,
            opened_at TEXT NOT NULL,
            closed_at TEXT,
            exit_reason TEXT,
            pnl_r REAL,
            mfe_r REAL NOT NULL DEFAULT 0,
            mae_r REAL NOT NULL DEFAULT 0,
            t1_hit INTEGER NOT NULL DEFAULT 0,
            t2_hit INTEGER NOT NULL DEFAULT 0,
            last_candle_ms INTEGER,
            entry_snapshot_id INTEGER
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_shadow_positions_open
        ON shadow_positions (
            test_era,
            asset,
            status
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS shadow_comparisons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            test_era TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            asset TEXT NOT NULL,
            snapshot_id INTEGER,
            category TEXT NOT NULL,
            official_position_id INTEGER,
            shadow_position_id INTEGER,
            official_side TEXT,
            shadow_side TEXT
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_shadow_comparisons_era
        ON shadow_comparisons (
            test_era,
            asset,
            timestamp
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS comparison_autopsies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comparison_id INTEGER NOT NULL UNIQUE,
            test_era TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            asset TEXT NOT NULL,
            category TEXT NOT NULL,
            classification TEXT NOT NULL,
            official_position_id INTEGER,
            shadow_position_id INTEGER,
            official_r REAL,
            shadow_r REAL,
            shadow_minus_official_r REAL,
            details_json TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_comparison_autopsies_era
        ON comparison_autopsies (
            test_era,
            asset,
            timestamp
        )
    """)

    conn.commit()
    conn.close()


def save_shadow_decision(
    db_file,
    test_era,
    data,
    official_plan,
    snapshot_id,
    shadow_decision,
    shadow_side="NONE",
    shadow_setup_type="NONE",
    reason=None,
    evidence=None,
):
    now = datetime.now(timezone.utc).isoformat()

    if isinstance(evidence, (dict, list)):
        evidence = json.dumps(
            evidence,
            ensure_ascii=False,
            default=str,
        )

    conn = sqlite3.connect(db_file)

    cursor = conn.execute(
        """
        INSERT INTO shadow_decisions (
            test_era,
            timestamp,
            asset,
            snapshot_id,
            official_status,
            official_side,
            official_setup_type,
            shadow_decision,
            shadow_side,
            shadow_setup_type,
            reason,
            evidence,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            test_era,
            data["timestamp"],
            data["asset"],
            snapshot_id,
            official_plan.get("status"),
            official_plan.get("side"),
            official_plan.get("setup_type"),
            shadow_decision,
            shadow_side,
            shadow_setup_type,
            reason,
            evidence,
            now,
        ),
    )

    conn.commit()
    decision_id = cursor.lastrowid
    conn.close()

    return decision_id


def compare_market_state(previous, current):
    if not previous:
        return {
            "comparable": False,
            "meaningful_changes": [],
            "change_count": 0,
        }

    changes = []

    def changed_text(field, current_key=None):
        key = current_key or field
        old = previous.get(field)
        new = current.get(key)

        if old is not None and new is not None and old != new:
            changes.append({
                "field": field,
                "old": old,
                "new": new,
            })

    def changed_number(field, threshold, current_key=None):
        key = current_key or field
        old = previous.get(field)
        new = current.get(key)

        if old is None or new is None:
            return

        delta = float(new) - float(old)

        if abs(delta) >= threshold:
            changes.append({
                "field": field,
                "old": old,
                "new": new,
                "delta": delta,
            })

    # Mudanças estruturais/categóricas.
    changed_text("regime")
    changed_text("setup_direction")
    changed_text("timing")

    def relation_change(
        name,
        old_left_key,
        old_right_key,
        new_left_key=None,
        new_right_key=None,
    ):
        new_left_key = new_left_key or old_left_key
        new_right_key = new_right_key or old_right_key

        old_left = previous.get(old_left_key)
        old_right = previous.get(old_right_key)
        new_left = current.get(new_left_key)
        new_right = current.get(new_right_key)

        if None in (old_left, old_right, new_left, new_right):
            return

        old_relation = (
            "ABOVE" if float(old_left) > float(old_right)
            else "BELOW" if float(old_left) < float(old_right)
            else "EQUAL"
        )
        new_relation = (
            "ABOVE" if float(new_left) > float(new_right)
            else "BELOW" if float(new_left) < float(new_right)
            else "EQUAL"
        )

        if old_relation != new_relation:
            changes.append({
                "field": name,
                "old": old_relation,
                "new": new_relation,
            })

    # Relações estruturais: cruzamentos importam mesmo quando
    # a variação numérica absoluta é pequena.
    relation_change(
        "price_vs_ema20_1h",
        "price",
        "ema20_1h",
    )
    relation_change(
        "ema20_vs_ema50_1h",
        "ema20_1h",
        "ema50_1h",
    )
    relation_change(
        "price_vs_ema20_4h",
        "price",
        "ema20_4h",
    )
    relation_change(
        "ema20_vs_ema50_4h",
        "ema20_4h",
        "ema50_4h",
    )

    # Mudanças quantitativas relevantes.
    # Evita tratar ruído mínimo como "nova evidência".
    changed_number("rsi_1h", 3.0)
    changed_number("rsi_4h", 3.0)
    changed_number("momentum_4h", 0.5)
    changed_number("oi_delta", 0.25)

    return {
        "comparable": True,
        "meaningful_changes": changes,
        "change_count": len(changes),
    }


def get_open_shadow_position(db_file, test_era, asset):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT *
        FROM shadow_positions
        WHERE test_era = ? AND asset = ? AND status = 'OPEN'
        ORDER BY id DESC
        LIMIT 1
        """,
        (test_era, asset),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _previous_shadow_signature(db_file, test_era, asset):
    conn = sqlite3.connect(db_file)
    row = conn.execute(
        """
        SELECT shadow_decision, shadow_side, shadow_setup_type
        FROM shadow_decisions
        WHERE test_era = ? AND asset = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (test_era, asset),
    ).fetchone()
    conn.close()
    return tuple(row) if row else None


def open_shadow_position(
    db_file,
    test_era,
    data,
    plan,
    snapshot_id,
    execution_candles=None,
):
    if plan.get("decision") not in ("LONG", "SHORT"):
        return None
    if get_open_shadow_position(db_file, test_era, data["asset"]):
        return None

    previous_signature = _previous_shadow_signature(
        db_file,
        test_era,
        data["asset"],
    )
    current_signature = (
        plan.get("decision"),
        plan.get("side"),
        plan.get("setup_type"),
    )
    if previous_signature == current_signature:
        return None

    last_candle_ms = None
    if execution_candles:
        last_candle_ms = max(int(c["time"]) for c in execution_candles)

    conn = sqlite3.connect(db_file)
    cursor = conn.execute(
        """
        INSERT INTO shadow_positions (
            test_era, asset, side, setup_type,
            entry, stop, t1, t2, t3, risk_per_unit,
            status, opened_at, last_candle_ms, entry_snapshot_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)
        """,
        (
            test_era,
            data["asset"],
            plan["side"],
            plan["setup_type"],
            float(plan["entry"]),
            float(plan["stop"]),
            float(plan["t1"]),
            float(plan["t2"]),
            float(plan["t3"]),
            float(plan["risk_per_unit"]),
            data["timestamp"],
            last_candle_ms,
            snapshot_id,
        ),
    )
    conn.commit()
    position_id = cursor.lastrowid
    conn.close()
    return position_id


def update_shadow_position(
    db_file,
    test_era,
    asset,
    execution_candles,
    closed_at,
):
    position = get_open_shadow_position(db_file, test_era, asset)
    if position is None or not execution_candles:
        return None

    candles = sorted(execution_candles, key=lambda x: int(x["time"]))
    last_ms = position.get("last_candle_ms")
    if last_ms is not None:
        candles = [c for c in candles if int(c["time"]) > int(last_ms)]
    if not candles:
        return None

    side = position["side"]
    entry = float(position["entry"])
    stop = float(position["stop"])
    t1 = float(position["t1"])
    t2 = float(position["t2"])
    t3 = float(position["t3"])
    risk = float(position["risk_per_unit"])
    mfe_r = float(position.get("mfe_r") or 0.0)
    mae_r = float(position.get("mae_r") or 0.0)
    t1_hit = int(position.get("t1_hit") or 0)
    t2_hit = int(position.get("t2_hit") or 0)
    exit_reason = None
    pnl_r = None
    final_ms = int(candles[-1]["time"])

    for candle in candles:
        high = float(candle["high"])
        low = float(candle["low"])
        final_ms = int(candle["time"])

        if side == "LONG":
            favorable = (high - entry) / risk
            adverse = (low - entry) / risk
            stop_hit = low <= stop
            t1_now = high >= t1
            t2_now = high >= t2
            t3_now = high >= t3
        else:
            favorable = (entry - low) / risk
            adverse = (entry - high) / risk
            stop_hit = high >= stop
            t1_now = low <= t1
            t2_now = low <= t2
            t3_now = low <= t3

        mfe_r = max(mfe_r, favorable)
        mae_r = min(mae_r, adverse)

        # With OHLC only, target+stop inside the same 1m candle has no
        # trustworthy event order. Exclude it from normal win/loss learning.
        if stop_hit and (t1_now or t2_now or t3_now):
            exit_reason = "AMBIGUOUS"
            pnl_r = 0.0
            break

        if stop_hit:
            exit_reason = "STOP"
            pnl_r = -1.0
            break

        if t1_now:
            t1_hit = 1
        if t2_now:
            t2_hit = 1
        if t3_now:
            t1_hit = 1
            t2_hit = 1
            exit_reason = "T3"
            pnl_r = 3.0
            break

    status = "CLOSED" if exit_reason else "OPEN"

    conn = sqlite3.connect(db_file)
    conn.execute(
        """
        UPDATE shadow_positions
        SET status = ?,
            closed_at = CASE WHEN ? = 'CLOSED' THEN ? ELSE closed_at END,
            exit_reason = COALESCE(?, exit_reason),
            pnl_r = COALESCE(?, pnl_r),
            mfe_r = ?,
            mae_r = ?,
            t1_hit = ?,
            t2_hit = ?,
            last_candle_ms = ?
        WHERE id = ?
        """,
        (
            status,
            status,
            closed_at,
            exit_reason,
            pnl_r,
            mfe_r,
            mae_r,
            t1_hit,
            t2_hit,
            final_ms,
            position["id"],
        ),
    )
    conn.commit()
    conn.close()

    return {
        "id": position["id"],
        "status": status,
        "side": side,
        "exit_reason": exit_reason,
        "pnl_r": pnl_r,
        "mfe_r": mfe_r,
        "mae_r": mae_r,
        "t1_hit": bool(t1_hit),
        "t2_hit": bool(t2_hit),
    }


def record_opening_comparison(
    db_file,
    test_era,
    data,
    snapshot_id,
    official_position_id,
    shadow_position_id,
):
    if official_position_id is None and shadow_position_id is None:
        return None

    conn = sqlite3.connect(db_file)

    official_side = None
    if official_position_id is not None:
        row = conn.execute(
            "SELECT side FROM paper_positions WHERE id = ?",
            (official_position_id,),
        ).fetchone()
        official_side = row[0] if row else None

    shadow_side = None
    if shadow_position_id is not None:
        row = conn.execute(
            "SELECT side FROM shadow_positions WHERE id = ?",
            (shadow_position_id,),
        ).fetchone()
        shadow_side = row[0] if row else None

    if official_position_id is not None and shadow_position_id is not None:
        category = (
            "BOTH_SAME_DIRECTION"
            if official_side == shadow_side
            else "BOTH_DIFFERENT_DIRECTION"
        )
    elif official_position_id is not None:
        category = "OFFICIAL_ONLY"
    else:
        category = "SHADOW_ONLY"

    cursor = conn.execute(
        """
        INSERT INTO shadow_comparisons (
            test_era, timestamp, asset, snapshot_id, category,
            official_position_id, shadow_position_id,
            official_side, shadow_side
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            test_era,
            data["timestamp"],
            data["asset"],
            snapshot_id,
            category,
            official_position_id,
            shadow_position_id,
            official_side,
            shadow_side,
        ),
    )
    conn.commit()
    comparison_id = cursor.lastrowid
    conn.close()
    return comparison_id


def comparison_summary(db_file, test_era):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
            c.*,
            p.status AS official_status,
            p.exit_reason AS official_exit_reason,
            p.realized_r AS official_r,
            s.status AS shadow_status,
            s.exit_reason AS shadow_exit_reason,
            s.pnl_r AS shadow_r
        FROM shadow_comparisons c
        LEFT JOIN paper_positions p
            ON p.id = c.official_position_id
        LEFT JOIN shadow_positions s
            ON s.id = c.shadow_position_id
        WHERE c.test_era = ?
        ORDER BY c.id
        """,
        (test_era,),
    ).fetchall()
    conn.close()

    summary = {
        "comparisons": len(rows),
        "official_only": 0,
        "shadow_only": 0,
        "both_same_direction": 0,
        "both_different_direction": 0,
        "shadow_avoided_official_loss": 0,
        "shadow_missed_official_win": 0,
        "shadow_only_wins": 0,
        "shadow_only_losses": 0,
        "completed_pair_r_delta": [],
    }

    for row in rows:
        category = row["category"]
        if category == "OFFICIAL_ONLY":
            summary["official_only"] += 1
            if row["official_status"] == "CLOSED":
                result = float(row["official_r"] or 0.0)
                if result < 0:
                    summary["shadow_avoided_official_loss"] += 1
                elif result > 0:
                    summary["shadow_missed_official_win"] += 1

        elif category == "SHADOW_ONLY":
            summary["shadow_only"] += 1
            if row["shadow_status"] == "CLOSED":
                result = float(row["shadow_r"] or 0.0)
                if result > 0:
                    summary["shadow_only_wins"] += 1
                elif result < 0:
                    summary["shadow_only_losses"] += 1

        elif category == "BOTH_SAME_DIRECTION":
            summary["both_same_direction"] += 1

        elif category == "BOTH_DIFFERENT_DIRECTION":
            summary["both_different_direction"] += 1

        if (
            row["official_position_id"] is not None
            and row["shadow_position_id"] is not None
            and row["official_status"] == "CLOSED"
            and row["shadow_status"] == "CLOSED"
            and row["official_exit_reason"] != "AMBIGUOUS"
            and row["shadow_exit_reason"] != "AMBIGUOUS"
        ):
            summary["completed_pair_r_delta"].append(
                float(row["shadow_r"] or 0.0)
                - float(row["official_r"] or 0.0)
            )

    deltas = summary["completed_pair_r_delta"]
    summary["completed_pairs"] = len(deltas)
    summary["average_shadow_minus_official_r"] = (
        sum(deltas) / len(deltas) if deltas else None
    )
    return summary


def _canonical_tf(market_structure_context, timeframe):
    if not market_structure_context:
        return {}
    mtf = market_structure_context.get("mtf", {})
    return mtf.get(timeframe, {}).get("canonical", {}) or {}


def _direction(state):
    if state == "BULLISH_STRUCTURE":
        return "LONG"
    if state == "BEARISH_STRUCTURE":
        return "SHORT"
    return None


def _trigger_matches(core, side):
    expected_break = "UP" if side == "LONG" else "DOWN"
    if _direction(core.get("state")) == side:
        return True

    last_break = core.get("last_break") or {}
    if last_break.get("direction") == expected_break:
        return True

    wanted = (
        {"BULLISH_RETEST_HELD", "LOWER_SWEEP_RECLAIMED"}
        if side == "LONG"
        else {"BEARISH_RETEST_HELD", "UPPER_SWEEP_REJECTED"}
    )
    return any(
        (level.get("reaction") or {}).get("event") in wanted
        for level in core.get("levels", [])
    )


def _structural_invalidation(core_5m, core_15m, side, entry):
    pivot_type = "LOW" if side == "LONG" else "HIGH"
    candidates = []

    for core in (core_5m, core_15m):
        for pivot in reversed(core.get("recent_pivots", [])):
            if pivot.get("type") != pivot_type:
                continue
            price = float(pivot["price"])
            if side == "LONG" and price < entry:
                candidates.append((price, core))
                break
            if side == "SHORT" and price > entry:
                candidates.append((price, core))
                break

    if not candidates:
        return None

    # Nearest valid structural invalidation keeps risk tied to current structure.
    if side == "LONG":
        pivot_price, core = max(candidates, key=lambda x: x[0])
        buffer_value = max(
            float(core.get("atr") or 0.0) * 0.25,
            entry * float(core.get("tolerance_pct") or 0.0),
        )
        return pivot_price - buffer_value

    pivot_price, core = min(candidates, key=lambda x: x[0])
    buffer_value = max(
        float(core.get("atr") or 0.0) * 0.25,
        entry * float(core.get("tolerance_pct") or 0.0),
    )
    return pivot_price + buffer_value


def _next_structural_obstacle(cores, side, entry):
    """Nearest clustered structure in the trade direction."""
    candidates = []
    for timeframe, core in cores:
        for level in core.get("levels", []):
            try:
                price = float(level["price"])
            except (KeyError, TypeError, ValueError):
                continue
            if side == "LONG" and price > entry:
                candidates.append((price, timeframe, level))
            elif side == "SHORT" and price < entry:
                candidates.append((price, timeframe, level))

    if not candidates:
        return None

    if side == "LONG":
        price, timeframe, level = min(candidates, key=lambda x: x[0])
    else:
        price, timeframe, level = max(candidates, key=lambda x: x[0])

    return {
        "price": price,
        "timeframe": timeframe,
        "touches": level.get("touches"),
        "strength": level.get("strength"),
        "current_role": level.get("current_role"),
    }


def build_structural_plan(data, market_structure_context):
    """Independent Shadow hypothesis. Never sends orders or changes Official."""
    entry = float(data["price"])
    core_4h = _canonical_tf(market_structure_context, "4h")
    core_2h = _canonical_tf(market_structure_context, "2h")
    core_1h = _canonical_tf(market_structure_context, "1h")
    core_15m = _canonical_tf(market_structure_context, "15m")
    core_5m = _canonical_tf(market_structure_context, "5m")
    core_1m = _canonical_tf(market_structure_context, "1m")

    states = {
        "4h": core_4h.get("state"),
        "2h": core_2h.get("state"),
        "1h": core_1h.get("state"),
        "15m": core_15m.get("state"),
        "5m": core_5m.get("state"),
        "1m": core_1m.get("state"),
    }

    side_1h = _direction(states["1h"])
    side_15m = _direction(states["15m"])

    evidence = {
        "states": states,
        "trigger_5m": False,
        "trigger_1m": False,
        "macro_opposition": False,
    }

    if side_1h is None or side_15m is None:
        return {
            "decision": "WAIT",
            "side": "NONE",
            "setup_type": "STRUCTURAL_WAIT",
            "reason": "1H/15m ainda sem estrutura direcional confirmada.",
            "evidence": evidence,
        }

    if side_1h != side_15m:
        return {
            "decision": "NO_TRADE",
            "side": "NONE",
            "setup_type": "STRUCTURAL_CONFLICT",
            "reason": "1H e 15m apontam direções estruturais opostas.",
            "evidence": evidence,
        }

    side = side_1h
    opposite = "SHORT" if side == "LONG" else "LONG"
    macro_sides = [_direction(states["4h"]), _direction(states["2h"])]
    macro_opposition = all(x == opposite for x in macro_sides)
    evidence["macro_opposition"] = macro_opposition

    if macro_opposition:
        return {
            "decision": "NO_TRADE",
            "side": "NONE",
            "setup_type": "STRUCTURAL_MACRO_CONFLICT",
            "reason": "4H e 2H confirmam estrutura oposta à hipótese 1H/15m.",
            "evidence": evidence,
        }

    trigger_5m = _trigger_matches(core_5m, side)
    trigger_1m = _trigger_matches(core_1m, side)
    evidence["trigger_5m"] = trigger_5m
    evidence["trigger_1m"] = trigger_1m

    if not trigger_5m:
        return {
            "decision": "WAIT",
            "side": side,
            "setup_type": "STRUCTURAL_WAIT_TRIGGER",
            "reason": "Direção estrutural existe, mas 5m ainda não confirmou o gatilho.",
            "evidence": evidence,
        }

    if not trigger_1m:
        return {
            "decision": "WAIT",
            "side": side,
            "setup_type": "STRUCTURAL_WAIT_EXECUTION",
            "reason": "5m confirmou, mas 1m ainda não confirmou execução.",
            "evidence": evidence,
        }

    stop = _structural_invalidation(core_5m, core_15m, side, entry)
    if stop is None:
        return {
            "decision": "NO_TRADE",
            "side": side,
            "setup_type": "STRUCTURAL_NO_INVALIDATION",
            "reason": "Sem swing válido para definir invalidação estrutural.",
            "evidence": evidence,
        }

    risk_per_unit = abs(entry - stop)
    risk_pct = risk_per_unit / entry if entry else 0.0
    evidence["risk_pct"] = risk_pct

    obstacle = _next_structural_obstacle(
        (
            ("5m", core_5m),
            ("15m", core_15m),
            ("1h", core_1h),
        ),
        side,
        entry,
    )
    obstacle_room_r = None
    if obstacle is not None and risk_per_unit > 0:
        obstacle_room_r = abs(obstacle["price"] - entry) / risk_per_unit
    evidence["next_obstacle"] = obstacle
    evidence["obstacle_room_r"] = obstacle_room_r

    if risk_per_unit <= 0 or risk_pct > 0.05:
        return {
            "decision": "NO_TRADE",
            "side": side,
            "setup_type": "STRUCTURAL_RISK_REJECTED",
            "reason": "Invalidação estrutural produz risco inválido ou superior a 5%.",
            "evidence": evidence,
        }

    # A structural level before 1R means the first planned reward already
    # runs into clustered opposing structure. Keep it as NO_TRADE in Shadow;
    # room in R remains recorded for replay/statistical review.
    if obstacle_room_r is not None and obstacle_room_r < 1.0:
        return {
            "decision": "NO_TRADE",
            "side": side,
            "setup_type": "STRUCTURAL_ROOM_REJECTED",
            "reason": "Próximo obstáculo estrutural aparece antes de 1R.",
            "evidence": evidence,
        }

    sign = 1 if side == "LONG" else -1
    targets = {
        "t1": entry + sign * risk_per_unit,
        "t2": entry + sign * risk_per_unit * 2,
        "t3": entry + sign * risk_per_unit * 3,
    }

    return {
        "decision": side,
        "side": side,
        "setup_type": "STRUCTURAL_ALIGNMENT",
        "entry": entry,
        "stop": stop,
        **targets,
        "risk_per_unit": risk_per_unit,
        "risk_pct": risk_pct,
        "reason": "1H/15m alinhados, sem oposição conjunta 4H/2H, com confirmação 5m e 1m.",
        "evidence": evidence,
    }


def evaluate_shadow(
    db_file,
    test_era,
    data,
    official_plan,
    snapshot_id,
    learning_memory,
    previous_entry_snapshot=None,
    market_structure_context=None,
    execution_candles=None,
):
    structural_plan = build_structural_plan(
        data,
        market_structure_context,
    )

    evidence = {
        "previous_position_id": learning_memory.get("previous_position_id"),
        "previous_exit_reason": learning_memory.get("previous_exit_reason"),
        "minutes_since_close": learning_memory.get("minutes_since_close"),
        "recent_stop": learning_memory.get("recent_stop", False),
        "structural_plan": structural_plan,
    }

    # Keep the old re-entry comparison as observational memory, not as the
    # Shadow decision itself. Structural Shadow must be independent.
    if previous_entry_snapshot is not None:
        evidence["reentry_comparison"] = compare_market_state(
            previous_entry_snapshot,
            data,
        )

    decision = structural_plan["decision"]
    side = structural_plan.get("side", "NONE")
    setup_type = structural_plan.get("setup_type", "NONE")
    reason = structural_plan.get("reason")

    # Open before saving the current decision so signal novelty is compared
    # against the previous cycle, never against itself.
    opened_position_id = open_shadow_position(
        db_file,
        test_era,
        data,
        structural_plan,
        snapshot_id,
        execution_candles=execution_candles,
    )

    save_shadow_decision(
        db_file,
        test_era,
        data,
        official_plan,
        snapshot_id,
        decision,
        shadow_side=side,
        shadow_setup_type=setup_type,
        reason=reason,
        evidence=evidence,
    )

    official_side = (
        official_plan.get("side")
        if official_plan.get("status") == "PAPER_READY"
        else "NONE"
    )
    comparison = (
        "AGREE_DIRECTION"
        if side != "NONE" and side == official_side
        else "SHADOW_ONLY"
        if side != "NONE" and official_side == "NONE"
        else "OFFICIAL_ONLY"
        if side == "NONE" and official_side != "NONE"
        else "DISAGREE_DIRECTION"
        if side != "NONE" and official_side != "NONE"
        else "BOTH_NO_TRADE"
    )

    return {
        "decision": decision,
        "side": side,
        "setup_type": setup_type,
        "reason": reason,
        "plan": structural_plan,
        "comparison": comparison,
        "opened_position_id": opened_position_id,
        "evidence": evidence,
    }


def process_completed_comparison_autopsies(db_file, test_era):
    """Persist factual Official x Shadow outcome comparisons exactly once.

    This is observational bookkeeping. It records what each system did and the
    realized R when outcomes are complete; it does not infer why one outcome
    was better or claim causality from structural differences.
    """
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
            c.*,
            p.status AS official_status,
            p.exit_reason AS official_exit_reason,
            p.realized_r AS official_r,
            s.status AS shadow_status,
            s.exit_reason AS shadow_exit_reason,
            s.pnl_r AS shadow_r,
            s.mfe_r AS shadow_mfe_r,
            s.mae_r AS shadow_mae_r
        FROM shadow_comparisons c
        LEFT JOIN paper_positions p
            ON p.id = c.official_position_id
        LEFT JOIN shadow_positions s
            ON s.id = c.shadow_position_id
        LEFT JOIN comparison_autopsies a
            ON a.comparison_id = c.id
        WHERE c.test_era = ?
          AND a.id IS NULL
        ORDER BY c.id
        """,
        (test_era,),
    ).fetchall()

    created = []
    for row in rows:
        official_done = (
            row["official_position_id"] is None
            or row["official_status"] in ("CLOSED", "AMBIGUOUS")
        )
        shadow_done = (
            row["shadow_position_id"] is None
            or row["shadow_status"] == "CLOSED"
        )
        if not (official_done and shadow_done):
            continue

        # Ambiguous OHLC ordering is deliberately excluded from comparative
        # conclusions. Preserve it as an explicit factual classification.
        if (
            str(row["official_exit_reason"] or "").startswith("AMBIGUOUS")
            or str(row["shadow_exit_reason"] or "").startswith("AMBIGUOUS")
        ):
            classification = "AMBIGUOUS_EXCLUDED"
        elif row["category"] == "OFFICIAL_ONLY":
            r = float(row["official_r"] or 0.0)
            classification = (
                "SHADOW_AVOIDED_OFFICIAL_LOSS"
                if r < 0
                else "SHADOW_MISSED_OFFICIAL_WIN"
                if r > 0
                else "OFFICIAL_ONLY_FLAT"
            )
        elif row["category"] == "SHADOW_ONLY":
            r = float(row["shadow_r"] or 0.0)
            classification = (
                "SHADOW_ONLY_WIN"
                if r > 0
                else "SHADOW_ONLY_LOSS"
                if r < 0
                else "SHADOW_ONLY_FLAT"
            )
        else:
            official_r = float(row["official_r"] or 0.0)
            shadow_r = float(row["shadow_r"] or 0.0)
            delta = shadow_r - official_r
            classification = (
                "PAIRED_SHADOW_HIGHER_R"
                if delta > 0
                else "PAIRED_OFFICIAL_HIGHER_R"
                if delta < 0
                else "PAIRED_EQUAL_R"
            )

        official_r = (
            None if row["official_position_id"] is None
            else float(row["official_r"] or 0.0)
        )
        shadow_r = (
            None if row["shadow_position_id"] is None
            else float(row["shadow_r"] or 0.0)
        )
        delta = (
            shadow_r - official_r
            if official_r is not None and shadow_r is not None
            else None
        )
        details = {
            "comparison_id": row["id"],
            "category": row["category"],
            "official": {
                "position_id": row["official_position_id"],
                "side": row["official_side"],
                "exit_reason": row["official_exit_reason"],
                "r": official_r,
            },
            "shadow": {
                "position_id": row["shadow_position_id"],
                "side": row["shadow_side"],
                "exit_reason": row["shadow_exit_reason"],
                "r": shadow_r,
                "mfe_r": row["shadow_mfe_r"],
                "mae_r": row["shadow_mae_r"],
            },
            "shadow_minus_official_r": delta,
            "interpretation": "OBSERVATIONAL_NOT_CAUSAL",
        }
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO comparison_autopsies (
                comparison_id, test_era, timestamp, asset, category,
                classification, official_position_id, shadow_position_id,
                official_r, shadow_r, shadow_minus_official_r, details_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                test_era,
                row["timestamp"],
                row["asset"],
                row["category"],
                classification,
                row["official_position_id"],
                row["shadow_position_id"],
                official_r,
                shadow_r,
                delta,
                json.dumps(details, ensure_ascii=False, sort_keys=True),
            ),
        )
        if cursor.rowcount:
            created.append({
                "id": cursor.lastrowid,
                "comparison_id": row["id"],
                "asset": row["asset"],
                "classification": classification,
                "official_r": official_r,
                "shadow_r": shadow_r,
                "shadow_minus_official_r": delta,
            })

    conn.commit()
    conn.close()
    return created
