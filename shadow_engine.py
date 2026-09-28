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
    evidence = {
        "previous_position_id": (
            learning_memory.get("previous_position_id")
        ),
        "previous_exit_reason": (
            learning_memory.get("previous_exit_reason")
        ),
        "minutes_since_close": (
            learning_memory.get("minutes_since_close")
        ),
        "recent_stop": (
            learning_memory.get("recent_stop", False)
        ),
        "market_structure": market_structure_context,
    }

    # Sem sinal oficial: apenas observa.
    if official_plan.get("status") != "PAPER_READY":
        decision = "OBSERVE"
        reason = "Oficial sem PAPER_READY."

    # Primeiro setup desse tipo ou sem STOP anterior.
    elif not learning_memory.get("recent_stop"):
        decision = "AGREE"
        reason = "Sem STOP anterior comparável."

    # Existe STOP, mas ele ocorreu antes da nova memória
    # possuir snapshot de entrada vinculado.
    elif previous_entry_snapshot is None:
        decision = "INSUFFICIENT_HISTORY"
        reason = (
            "STOP anterior encontrado, mas sem snapshot "
            "de entrada vinculado."
        )

    else:
        comparison = compare_market_state(
            previous_entry_snapshot,
            data,
        )

        evidence["comparison"] = comparison

        if comparison["change_count"] == 0:
            decision = "REENTRY_NO_NEW_EVIDENCE"
            reason = (
                "Mesmo setup após STOP sem mudança relevante "
                "detectada no contexto."
            )
        else:
            decision = "REENTRY_CHANGED_CONTEXT"
            reason = (
                "Mesmo setup após STOP, mas com mudança "
                "relevante detectada no contexto."
            )

    save_shadow_decision(
        db_file,
        test_era,
        data,
        official_plan,
        snapshot_id,
        decision,
        shadow_side=official_plan.get("side", "NONE"),
        shadow_setup_type=official_plan.get(
            "setup_type",
            "NONE",
        ),
        reason=reason,
        evidence=evidence,
    )

    return {
        "decision": decision,
        "reason": reason,
        "evidence": evidence,
    }
