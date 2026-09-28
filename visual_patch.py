from pathlib import Path

path = Path("agent.py")
code = path.read_text(encoding="utf-8")

# ============================================================
# NOVA FICHA DETALHADA
# ============================================================

new_format = r'''def visual_score(score):
    if score >= 70:
        return "🟢"
    if score >= 50:
        return "🟡"
    return "⚪"


def visual_confluence(value):
    if value >= 4:
        return "🟢"
    if value >= 3:
        return "🟡"
    return "🔴"


def visual_rsi(value):
    if value >= 70 or value <= 30:
        return "🔴"
    if value >= 65 or value <= 35:
        return "🟡"
    return "🟢"


def visual_trend(regime):
    if regime == "UPTREND":
        return "🟢"
    if regime == "DOWNTREND":
        return "🔴"
    return "🟡"


def visual_timing(timing):
    if timing == "ENTRY_ZONE":
        return "🟢"
    if timing in ("WAIT_PULLBACK", "WAIT_BOUNCE",
                  "WAIT_CONFIRMATION", "EXTENDED"):
        return "🟡"
    return "⚪"


def visual_action(decision):
    if decision in ("LONG_SETUP", "SHORT_SETUP"):
        return "🟢"
    if decision in ("WATCH", "ACCUMULATION_WATCH"):
        return "🟡"
    return "🔴"


def format_telegram(data):
    funding_pct = data["funding"] * 100

    return (
        f"🤖 MARKET AGENT — {data['asset']}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"

        f"💰 PREÇO\n"
        f"${data['price']:,.2f}\n\n"

        f"{visual_trend(data['regime'])} TENDÊNCIA\n"
        f"{regime_pt(data['regime'])}\n\n"

        f"🎯 SETUP\n"
        f"{setup_pt(data['setup_direction'])}\n\n"

        f"{visual_timing(data['timing'])} TIMING\n"
        f"{timing_pt(data['timing'])}\n\n"

        f"📊 SCORES\n"
        f"{visual_score(data['trading_score'])} Trading       "
        f"{data['trading_score']}/100\n"
        f"{visual_score(data['accumulation_score'])} Acumulação    "
        f"{data['accumulation_score']}/100\n"
        f"{visual_confluence(data['confluence'])} Confluência   "
        f"{data['confluence']}/6\n\n"

        f"📈 INDICADORES\n"
        f"{visual_rsi(data['rsi_1h'])} RSI 1H: "
        f"{data['rsi_1h']:.1f}\n"
        f"{visual_rsi(data['rsi_4h'])} RSI 4H: "
        f"{data['rsi_4h']:.1f}\n"
        f"💸 Funding: {funding_pct:+.5f}%\n"
        f"🔄 OI Δ 5m: {data['oi_delta']:+.2f}%\n"
        f"⚡ Momentum 4H: {data['momentum_4h']:+.2f}%\n\n"

        f"🧠 LEITURA\n"
        f"{data['thesis']}\n\n"

        f"⚠️ POR QUE NÃO / RISCO\n"
        f"{data['counter_thesis']}\n\n"

        f"📌 AÇÃO\n"
        f"{visual_action(data['decision'])} "
        f"{action_pt(data['decision'])}\n\n"

        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🛡️ Trading real: DESATIVADO"
    )


'''

start = code.index("def format_telegram(data):")
end = code.index(
    "# ============================================================\n# RESUMO PERIÓDICO",
    start
)

code = code[:start] + new_format + code[end:]


# ============================================================
# NOVO RESUMO DOS 5 ATIVOS
# ============================================================

new_summary = r'''def send_summary(results):
    if not results:
        return

    lines = [
        "🤖 MARKET AGENT — RESUMO",
        "━━━━━━━━━━━━━━━━━━━━",
        ""
    ]

    icons = {
        "BTC": "₿",
        "ETH": "Ξ",
        "SOL": "◎",
        "AAVE": "👻",
        "BNB": "🔶"
    }

    for d in results:
        icon = icons.get(d["asset"], "◆")

        lines.extend([
            f"{icon} {d['asset']}   ${d['price']:,.2f}",
            f"{visual_trend(d['regime'])} Tendência: "
            f"{regime_pt(d['regime'])}",
            f"🎯 Setup: {setup_pt(d['setup_direction'])}",
            f"{visual_timing(d['timing'])} Timing: "
            f"{timing_pt(d['timing'])}",
            f"{visual_score(d['trading_score'])} Trading: "
            f"{d['trading_score']}/100   "
            f"{visual_score(d['accumulation_score'])} Acum: "
            f"{d['accumulation_score']}/100",
            f"{visual_confluence(d['confluence'])} "
            f"Confluência: {d['confluence']}/6",
            ""
        ])

    lines.extend([
        "━━━━━━━━━━━━━━━━━━━━",
        "🛡️ Trading real: DESATIVADO"
    ])

    if send_telegram("\n".join(lines)):
        set_system_state(
            "last_summary",
            datetime.now(timezone.utc).isoformat()
        )


'''

start = code.index("def send_summary(results):")
end = code.index(
    "# ============================================================\n# ANALISAR UM ATIVO",
    start
)

code = code[:start] + new_summary + code[end:]

path.write_text(code, encoding="utf-8")

print("VISUAL TELEGRAM ATUALIZADO")
