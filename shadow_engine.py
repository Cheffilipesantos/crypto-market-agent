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

    if risk_per_unit <= 0 or risk_pct > 0.05:
        return {
            "decision": "NO_TRADE",
            "side": side,
            "setup_type": "STRUCTURAL_RISK_REJECTED",
            "reason": "Invalidação estrutural produz risco inválido ou superior a 5%.",
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
        "evidence": evidence,
    }
