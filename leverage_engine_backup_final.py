"""
LEVERAGE ENGINE V0.4
Motor PAPER para LONG / SHORT.

IMPORTANTE:
- Não envia ordens.
- Não possui chave de exchange.
- Não executa trading real.
- HOLD/ACUMULAÇÃO permanece separado.
"""

import sqlite3


def calculate_tactical_short(data):
    """
    Detecta deterioração de curto prazo mesmo antes
    de o regime 4H necessariamente virar DOWNTREND.
    """

    score = 0
    reasons = []

    price = float(data["price"])
    ema20 = float(data["ema20_1h"])
    ema50 = float(data["ema50_1h"])
    rsi1 = float(data["rsi_1h"])
    rsi4 = float(data["rsi_4h"])
    momentum = float(data["momentum_4h"])
    oi_delta = float(data["oi_delta"])
    funding = float(data["funding"])

    if data["regime"] in (
        "UPTREND",
        "TRANSITION/RANGE"
    ):
        score += 10
        reasons.append("4H ainda não confirmou DOWNTREND")

    if price < ema20:
        score += 25
        reasons.append("preço perdeu EMA20 1H")

    if ema20 < ema50:
        score += 20
        reasons.append("estrutura 1H bearish")

    if momentum <= -0.5:
        score += 20
        reasons.append("momentum 4H negativo")

    if rsi1 < 50:
        score += 10
        reasons.append("RSI 1H abaixo de 50")

    if oi_delta > 0.25:
        score += 10
        reasons.append("OI expandindo")

    if funding > 0:
        score += 5
        reasons.append("funding positivo")

    # Dupla proteção:
    # não basta pontuação; precisa haver deterioração real.
    core_deterioration = (
        price < ema20
        and momentum < 0
    )

    # Evita vender depois de queda já muito esticada.
    blocked = (
        rsi1 <= 28
        or rsi4 <= 30
    )

    ready = (
        score >= 70
        and core_deterioration
        and not blocked
    )

    return {
        "score": min(score, 100),
        "ready": ready,
        "blocked": blocked,
        "core_deterioration": core_deterioration,
        "reasons": reasons,
    }


def build_plan(data, config, trading_threshold=70, min_confluence=4):
    """
    Monta plano PAPER:
    - TREND_LONG
    - TREND_SHORT
    - TACTICAL_SHORT
    - NO_LEVERAGE
    """

    tactical = calculate_tactical_short(data)

    side = "NONE"
    setup_type = "NONE"

    # SHORT de tendência
    if (
        data["setup_direction"] == "BEARISH"
        and data["timing"] == "ENTRY_ZONE"
        and data["trading_score"] >= trading_threshold
        and data["confluence"] >= min_confluence
    ):
        side = "SHORT"
        setup_type = "TREND_SHORT"

    # SHORT tático
    elif tactical["ready"]:
        side = "SHORT"
        setup_type = "TACTICAL_SHORT"

    # LONG de tendência
    elif (
        data["setup_direction"] == "BULLISH"
        and data["timing"] == "ENTRY_ZONE"
        and data["trading_score"] >= trading_threshold
        and data["confluence"] >= min_confluence
    ):
        side = "LONG"
        setup_type = "TREND_LONG"

    plan = {
        "status": "NO_LEVERAGE",
        "side": side,
        "setup_type": setup_type,
        "entry": float(data["price"]),
        "stop": 0.0,
        "stop_pct": 0.0,
        "leverage": 0,
        "model_leverage_cap": 0,
        "risk_usd": 0.0,
        "notional_usd": 0.0,
        "t1": 0.0,
        "t2": 0.0,
        "t3": 0.0,
        "tactical_score": tactical["score"],
        "reason": "",
    }

    if side == "NONE":
        plan["reason"] = "Nenhum setup alavancado confirmado."
        return plan

    price = float(data["price"])
    atr = float(data["atr_1h"])
    ema20 = float(data["ema20_1h"])

    if price <= 0 or atr <= 0:
        plan["reason"] = "Preço ou ATR inválido."
        return plan

    # ========================================================
    # STOP PRIMEIRO, ALAVANCAGEM DEPOIS
    #
    # Combina ATR com EMA20 1H como referência estrutural.
    # ========================================================

    if side == "LONG":
        atr_stop = price - (1.5 * atr)
        structure_stop = ema20 - (0.5 * atr)
        stop = min(atr_stop, structure_stop)

    else:
        atr_stop = price + (1.5 * atr)
        structure_stop = ema20 + (0.5 * atr)
        stop = max(atr_stop, structure_stop)

    stop_distance = abs(price - stop)
    stop_pct = (stop_distance / price) * 100

    plan["stop"] = stop
    plan["stop_pct"] = stop_pct

    # Stop muito largo = nenhuma operação.
    if stop_pct <= 0 or stop_pct > 10:
        plan["reason"] = (
            f"Stop técnico rejeitado: {stop_pct:.2f}%."
        )
        return plan

    # ========================================================
    # CAP DE ALAVANCAGEM DO MODELO
    #
    # Isto NÃO é cálculo exato de liquidação da Hyperliquid.
    # É apenas uma trava conservadora do nosso modelo PAPER.
    # ========================================================

    safety_factor = float(
        config.get("leverage_safety_factor", 2.0)
    )

    model_cap = int(
        100 / (stop_pct * safety_factor)
    )

    hard_cap = 5 if setup_type == "TACTICAL_SHORT" else 10

    model_cap = max(
        1,
        min(model_cap, hard_cap)
    )

    plan["model_leverage_cap"] = model_cap

    choices = config.get(
        "leverage_choices",
        [3, 5, 10]
    )

    choices = sorted(int(x) for x in choices)

    allowed = [
        lev for lev in choices
        if lev <= model_cap
    ]

    if not allowed:
        plan["reason"] = (
            f"Stop {stop_pct:.2f}% não permite nem 3x "
            "dentro da trava do modelo."
        )
        return plan

    leverage = max(allowed)

    # ========================================================
    # POSITION SIZING PELO RISCO
    # ========================================================

    balance = float(
        config.get("paper_balance", 10000)
    )

    risk_pct = float(
        config.get("leverage_risk_pct", 0.01)
    )

    # Contra-tendência = metade do risco.
    if setup_type == "TACTICAL_SHORT":
        risk_pct *= 0.5

    risk_usd = balance * risk_pct

    notional_usd = risk_usd / (stop_pct / 100)

    # Limite nocional da conta PAPER.
    notional_usd = min(
        notional_usd,
        balance * leverage
    )

    # ========================================================
    # TARGETS 1R / 2R / 3R
    # ========================================================

    if side == "LONG":
        t1 = price + stop_distance
        t2 = price + (2 * stop_distance)
        t3 = price + (3 * stop_distance)

    else:
        t1 = price - stop_distance
        t2 = price - (2 * stop_distance)
        t3 = price - (3 * stop_distance)

    plan.update({
        "status": "PAPER_READY",
        "leverage": leverage,
        "risk_usd": risk_usd,
        "notional_usd": notional_usd,
        "t1": t1,
        "t2": t2,
        "t3": t3,
    })

    if setup_type == "TACTICAL_SHORT":
        plan["reason"] = (
            f"SHORT tático confirmado "
            f"({tactical['score']}/100), "
            "com risco reduzido."
        )
    else:
        plan["reason"] = (
            f"{setup_type} confirmado por "
            "score, timing e confluência."
        )

    return plan


def init_storage(db_file):
    """
    Tabelas exclusivas do motor LONG/SHORT PAPER.
    Não altera a tabela scans existente.
    """
    conn = sqlite3.connect(db_file)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS leverage_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            asset TEXT NOT NULL,
            price REAL,
            long_score REAL,
            short_score REAL,
            tactical_short_score REAL,
            setup_direction TEXT,
            timing TEXT,
            trading_score REAL,
            confluence INTEGER,
            leverage_status TEXT,
            leverage_side TEXT,
            setup_type TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS leverage_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            asset TEXT NOT NULL,
            side TEXT NOT NULL,
            setup_type TEXT NOT NULL,
            entry REAL,
            stop REAL,
            stop_pct REAL,
            leverage INTEGER,
            model_leverage_cap INTEGER,
            risk_usd REAL,
            notional_usd REAL,
            t1 REAL,
            t2 REAL,
            t3 REAL,
            tactical_score REAL,
            status TEXT,
            reason TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS leverage_state (
            asset TEXT PRIMARY KEY,
            signature TEXT,
            updated_at TEXT
        )
    """)

    conn.commit()
    conn.close()


def save_cycle(db_file, data, plan):
    """
    Guarda score de TODOS os scans.

    Um leverage_signal só nasce quando o ativo entra
    em PAPER_READY. O mesmo estado não é duplicado
    a cada cinco minutos.
    """
    conn = sqlite3.connect(db_file)

    conn.execute(
        """
        INSERT INTO leverage_scores (
            timestamp,
            asset,
            price,
            long_score,
            short_score,
            tactical_short_score,
            setup_direction,
            timing,
            trading_score,
            confluence,
            leverage_status,
            leverage_side,
            setup_type
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data["timestamp"],
            data["asset"],
            data["price"],
            data.get("long_score", 0),
            data.get("short_score", 0),
            plan["tactical_score"],
            data["setup_direction"],
            data["timing"],
            data["trading_score"],
            data["confluence"],
            plan["status"],
            plan["side"],
            plan["setup_type"],
        )
    )

    signature = (
        f"{plan['status']}|"
        f"{plan['side']}|"
        f"{plan['setup_type']}"
    )

    row = conn.execute(
        """
        SELECT signature
        FROM leverage_state
        WHERE asset = ?
        """,
        (data["asset"],)
    ).fetchone()

    previous_signature = row[0] if row else None
    new_signal = False

    if (
        plan["status"] == "PAPER_READY"
        and signature != previous_signature
    ):
        conn.execute(
            """
            INSERT INTO leverage_signals (
                timestamp, asset, side, setup_type,
                entry, stop, stop_pct, leverage,
                model_leverage_cap, risk_usd,
                notional_usd, t1, t2, t3,
                tactical_score, status, reason
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                data["timestamp"],
                data["asset"],
                plan["side"],
                plan["setup_type"],
                plan["entry"],
                plan["stop"],
                plan["stop_pct"],
                plan["leverage"],
                plan["model_leverage_cap"],
                plan["risk_usd"],
                plan["notional_usd"],
                plan["t1"],
                plan["t2"],
                plan["t3"],
                plan["tactical_score"],
                plan["status"],
                plan["reason"],
            )
        )

        new_signal = True

    conn.execute(
        """
        INSERT INTO leverage_state (
            asset, signature, updated_at
        )
        VALUES (?, ?, ?)
        ON CONFLICT(asset)
        DO UPDATE SET
            signature = excluded.signature,
            updated_at = excluded.updated_at
        """,
        (
            data["asset"],
            signature,
            data["timestamp"],
        )
    )

    conn.commit()
    conn.close()

    return new_signal


def format_telegram_signal(data, plan):
    """
    Formata somente sinais PAPER_READY para Telegram.
    Não envia mensagens e não executa ordens.
    """

    if plan["status"] != "PAPER_READY":
        return None

    icon = "🟢" if plan["side"] == "LONG" else "🔴"

    text = (
        f"{icon} NOVO SINAL PAPER — {data['asset']}\n"
        f"\n"
        f"Direção: {plan['side']}\n"
        f"Setup: {plan['setup_type']}\n"
        f"Entrada: ${plan['entry']:,.2f}\n"
        f"Stop: ${plan['stop']:,.2f} "
        f"({plan['stop_pct']:.2f}%)\n"
        f"Alavancagem PAPER: {plan['leverage']}x\n"
        f"Risco da banca: ${plan['risk_usd']:,.2f}\n"
        f"Notional: ${plan['notional_usd']:,.2f}\n"
        f"\n"
        f"🎯 T1: ${plan['t1']:,.2f}\n"
        f"🎯 T2: ${plan['t2']:,.2f}\n"
        f"🎯 T3: ${plan['t3']:,.2f}\n"
        f"\n"
        f"Tactical Short Score: "
        f"{plan['tactical_score']}/100\n"
        f"Motivo: {plan['reason']}\n"
        f"\n"
        f"🛡️ PAPER ONLY — trading real DESATIVADO"
    )

    return text
