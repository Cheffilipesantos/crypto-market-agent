from pathlib import Path

p = Path("agent.py")
s = p.read_text(encoding="utf-8")

# ------------------------------------------------------------
# NOVO MOTOR DE ALERTAS
# ------------------------------------------------------------

a = s.index("def should_alert(data):")
b = s.index(
    "# ============================================================\n# FORMATAÇÃO",
    a
)

novo_alerta = r'''def previous_scan_data(asset):
    conn = db()
    row = conn.execute("""
        SELECT price, rsi_1h, rsi_4h, funding, oi_delta,
               regime, setup_direction, timing,
               trading_score, accumulation_score,
               confluence, decision, momentum_4h
        FROM scans
        WHERE asset = ?
        ORDER BY id DESC
        LIMIT 1
    """, (asset,)).fetchone()
    conn.close()

    if not row:
        return None

    keys = [
        "price", "rsi_1h", "rsi_4h", "funding", "oi_delta",
        "regime", "setup_direction", "timing",
        "trading_score", "accumulation_score",
        "confluence", "decision", "momentum_4h"
    ]

    return dict(zip(keys, row))


def event_signature(data):
    return "|".join([
        data["regime"],
        data["setup_direction"],
        data["timing"],
        data["decision"],
        str(score_bucket(data["trading_score"])),
        str(accumulation_bucket(data["accumulation_score"])),
        str(data["confluence"] >= MIN_CONFLUENCE)
    ])


def event_importance(data, previous):
    if previous is None:
        return "NONE"

    # Eventos que merecem alerta imediato
    if (
        data["timing"] == "ENTRY_ZONE"
        and previous["timing"] != "ENTRY_ZONE"
    ):
        return "IMMEDIATE"

    if (
        data["decision"] in (
            "LONG_SETUP",
            "SHORT_SETUP",
            "ACCUMULATION_WATCH"
        )
        and data["decision"] != previous["decision"]
    ):
        return "IMMEDIATE"

    # Invalidação de uma oportunidade anterior
    if (
        previous["timing"] == "ENTRY_ZONE"
        and data["timing"] != "ENTRY_ZONE"
    ):
        return "IMMEDIATE"

    if (
        previous["decision"] in ("LONG_SETUP", "SHORT_SETUP")
        and data["decision"] not in ("LONG_SETUP", "SHORT_SETUP")
    ):
        return "IMMEDIATE"

    # Mudanças estruturais
    if data["regime"] != previous["regime"]:
        return "CONFIRM"

    if (
        data["setup_direction"] != previous["setup_direction"]
        and (
            data["trading_score"] >= 50
            or previous["trading_score"] >= 50
        )
    ):
        return "CONFIRM"

    if data["timing"] != previous["timing"]:
        return "CONFIRM"

    # Cruzamentos de score
    old_t = score_bucket(previous["trading_score"])
    new_t = score_bucket(data["trading_score"])

    if old_t != new_t:
        return "CONFIRM"

    old_a = accumulation_bucket(
        previous["accumulation_score"]
    )
    new_a = accumulation_bucket(
        data["accumulation_score"]
    )

    if old_a != new_a:
        return "CONFIRM"

    old_conf = previous["confluence"] >= MIN_CONFLUENCE
    new_conf = data["confluence"] >= MIN_CONFLUENCE

    if old_conf != new_conf:
        return "CONFIRM"

    return "NONE"


def should_alert(data, previous):
    importance = event_importance(data, previous)

    if importance == "NONE":
        set_system_state(
            "pending_" + data["asset"],
            ""
        )
        return False

    signature = event_signature(data)

    # Eventos fortes são enviados imediatamente.
    if importance == "IMMEDIATE":
        set_system_state(
            "pending_" + data["asset"],
            ""
        )
        return True

    # Mudanças normais precisam aparecer em 2 scans.
    key = "pending_" + data["asset"]
    raw = get_system_state(key)

    old_signature = ""
    count = 0

    if raw:
        try:
            old_signature, count_text = raw.rsplit("::", 1)
            count = int(count_text)
        except Exception:
            pass

    if old_signature == signature:
        count += 1
    else:
        count = 1

    set_system_state(
        key,
        signature + "::" + str(count)
    )

    if count >= 2:
        set_system_state(key, "")
        return True

    return False


'''

s = s[:a] + novo_alerta + s[b:]


# ------------------------------------------------------------
# NOVA FORMATAÇÃO EXPLICATIVA
# ------------------------------------------------------------

a = s.index("def format_telegram(data):")
b = s.index(
    "# ============================================================\n# RESUMO PERIÓDICO",
    a
)

novo_format = r'''def status_icon(data):
    if data["timing"] == "ENTRY_ZONE":
        return "🟢"

    if data["decision"] in (
        "WATCH",
        "ACCUMULATION_WATCH"
    ):
        return "🟡"

    if data["decision"] in (
        "LONG_SETUP",
        "SHORT_SETUP"
    ):
        return "🟢"

    return "⚪"


def explain_wait(data):
    timing = data["timing"]

    if timing == "WAIT_PULLBACK":
        reasons = []

        if data["rsi_1h"] >= 75:
            reasons.append(
                f"RSI 1H está elevado ({data['rsi_1h']:.1f})"
            )

        if data["rsi_4h"] >= 75:
            reasons.append(
                f"RSI 4H está elevado ({data['rsi_4h']:.1f})"
            )

        if data["ema20_1h"]:
            dist = (
                (data["price"] - data["ema20_1h"])
                / data["ema20_1h"]
            ) * 100

            if dist > 3:
                reasons.append(
                    f"preço está {dist:.1f}% acima da EMA20 1H"
                )

        if not reasons:
            reasons.append(
                "o preço está estendido dentro da tendência"
            )

        return (
            "A tendência favorece alta, mas entrar agora "
            "significaria perseguir o preço. "
            + "; ".join(reasons)
            + ". O agente espera correção/reteste."
        )

    if timing == "WAIT_BOUNCE":
        return (
            "A estrutura favorece baixa, mas o movimento "
            "já está estendido. O agente espera um repique "
            "antes de considerar uma entrada short."
        )

    if timing == "EXTENDED":
        return (
            "Existe direção, mas o preço está esticado. "
            "O risco/retorno atual não justifica perseguir "
            "o movimento."
        )

    if timing == "WAIT_CONFIRMATION":
        missing = []

        if data["trading_score"] < TRADING_THRESHOLD:
            missing.append(
                f"Trading Score {data['trading_score']}/100 "
                f"ainda abaixo de {TRADING_THRESHOLD}"
            )

        if data["confluence"] < MIN_CONFLUENCE:
            missing.append(
                f"confluência {data['confluence']}/6 "
                f"ainda abaixo de {MIN_CONFLUENCE}/6"
            )

        if not missing:
            missing.append(
                "a estrutura ainda não confirmou um ponto de entrada"
            )

        return (
            "Existe uma leitura direcional, mas faltam "
            "confirmações. " + "; ".join(missing) + "."
        )

    if timing == "ENTRY_ZONE":
        return (
            "Os filtros mínimos de score, confluência e "
            "estrutura estão alinhados. O timing passou "
            "para zona de entrada."
        )

    return (
        "Não existe setup direcional suficientemente claro "
        "neste momento."
    )


def change_lines(data, previous):
    if previous is None:
        return ["Primeiro estado registrado pelo agente."]

    changes = []

    if previous["regime"] != data["regime"]:
        changes.append(
            f"Tendência: {regime_pt(previous['regime'])} → "
            f"{regime_pt(data['regime'])}"
        )

    if previous["setup_direction"] != data["setup_direction"]:
        changes.append(
            f"Setup: {setup_pt(previous['setup_direction'])} → "
            f"{setup_pt(data['setup_direction'])}"
        )

    if previous["timing"] != data["timing"]:
        changes.append(
            f"Timing: {timing_pt(previous['timing'])} → "
            f"{timing_pt(data['timing'])}"
        )

    if previous["decision"] != data["decision"]:
        changes.append(
            f"Ação: {action_pt(previous['decision'])} → "
            f"{action_pt(data['decision'])}"
        )

    if previous["trading_score"] != data["trading_score"]:
        changes.append(
            f"Trading Score: {previous['trading_score']} → "
            f"{data['trading_score']}"
        )

    if previous["accumulation_score"] != data["accumulation_score"]:
        changes.append(
            f"Acumulação: {previous['accumulation_score']} → "
            f"{data['accumulation_score']}"
        )

    if previous["confluence"] != data["confluence"]:
        changes.append(
            f"Confluência: {previous['confluence']}/6 → "
            f"{data['confluence']}/6"
        )

    if abs(data["rsi_1h"] - previous["rsi_1h"]) >= 3:
        changes.append(
            f"RSI 1H: {previous['rsi_1h']:.1f} → "
            f"{data['rsi_1h']:.1f}"
        )

    if not changes:
        changes.append(
            "Os indicadores cruzaram um nível relevante "
            "do modelo."
        )

    return changes[:6]


def invalidation_text(data):
    if data["setup_direction"] == "BULLISH":
        return (
            "Deterioração da estrutura 1H/4H, perda das "
            "médias relevantes ou enfraquecimento da "
            "confluência."
        )

    if data["setup_direction"] == "BEARISH":
        return (
            "Recuperação da estrutura 1H/4H, retomada das "
            "médias relevantes ou enfraquecimento da "
            "confluência bearish."
        )

    return (
        "O agente precisa de nova estrutura direcional "
        "antes de considerar operação."
    )


def format_telegram(data, previous=None):
    funding_pct = data["funding"] * 100
    changes = change_lines(data, previous)
    change_text = "\n".join(
        "• " + x for x in changes
    )

    return (
        f"🚨 MUDANÇA DE CENÁRIO — {data['asset']}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"

        f"💰 PREÇO\n"
        f"${data['price']:,.2f}\n\n"

        f"{status_icon(data)} ESTADO ATUAL\n"
        f"Tendência: {regime_pt(data['regime'])}\n"
        f"Setup: {setup_pt(data['setup_direction'])}\n"
        f"Timing: {timing_pt(data['timing'])}\n"
        f"Ação: {action_pt(data['decision'])}\n\n"

        f"🔄 O QUE MUDOU\n"
        f"{change_text}\n\n"

        f"🧠 POR QUE O AGENTE DECIDIU ISSO\n"
        f"{explain_wait(data)}\n\n"

        f"📊 INDICADORES\n"
        f"Trading: {data['trading_score']}/100\n"
        f"Acumulação: {data['accumulation_score']}/100\n"
        f"Confluência: {data['confluence']}/6\n"
        f"RSI 1H: {data['rsi_1h']:.1f}\n"
        f"RSI 4H: {data['rsi_4h']:.1f}\n"
        f"Funding: {funding_pct:+.5f}%\n"
        f"OI Δ: {data['oi_delta']:+.2f}%\n"
        f"Momentum 4H: {data['momentum_4h']:+.2f}%\n\n"

        f"💡 TESE\n"
        f"{data['thesis']}\n\n"

        f"⚠️ O QUE INVALIDARIA / RISCO\n"
        f"{invalidation_text(data)}\n"
        f"{data['counter_thesis']}\n\n"

        f"📌 AGORA\n"
        f"{status_icon(data)} {action_pt(data['decision'])}\n\n"

        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🛡️ Trading real: DESATIVADO"
    )


'''

s = s[:a] + novo_format + s[b:]


# ------------------------------------------------------------
# NOVO CICLO: SCAN CONTÍNUO, TELEGRAM POR EVENTO
# ------------------------------------------------------------

a = s.index("def run_cycle():")
b = s.index(
    "# ============================================================\n# MAIN",
    a
)

novo_ciclo = r'''def run_cycle():
    print()
    print(datetime.now(timezone.utc).isoformat())
    print("Obtendo dados de mercado...")

    markets = get_market_contexts()
    results = []

    for asset in ASSETS:
        try:
            if asset not in markets:
                print(
                    f"⚠️ {asset}: não encontrado na Hyperliquid"
                )
                continue

            # Estado imediatamente anterior.
            previous = previous_scan_data(asset)

            data = analyze_asset(
                asset,
                markets[asset]
            )

            # Decide antes de salvar o scan atual.
            alert = should_alert(
                data,
                previous
            )

            # TODO histórico continua completo,
            # mesmo quando Telegram fica silencioso.
            save_scan(data)
            print_analysis(data)

            if alert:
                ok = send_telegram(
                    format_telegram(
                        data,
                        previous
                    )
                )

                if ok:
                    print(
                        f"📨 Telegram: mudança relevante "
                        f"enviada para {asset}"
                    )

            else:
                print(
                    f"🔕 {asset}: sem mudança confirmada "
                    f"para Telegram"
                )

            update_alert_state(data)
            results.append(data)

        except Exception as e:
            print(
                f"\n❌ ERRO {asset}: {e}"
            )

    # Não existe mais resumo obrigatório por tempo.
    # O banco continua recebendo todos os scans.

    return results


'''

s = s[:a] + novo_ciclo + s[b:]


# ------------------------------------------------------------
# STARTUP MAIS CLARO
# ------------------------------------------------------------

old = '''"🤖 Crypto Market Agent V0.2 iniciado\\n"
            "📊 BTC • ETH • SOL • AAVE • BNB\\n"
            "⏱ Scan a cada 5 minutos\\n"
            "🔔 Alertas somente em mudanças relevantes\\n"
            "🛡️ Trading real: DESATIVADO"'''

new = '''"🤖 Crypto Market Agent — EVENT MODE\\n"
            "📊 BTC • ETH • SOL • AAVE • BNB\\n"
            "🔎 Varredura contínua a cada 5 minutos\\n"
            "🔕 Sem mudança relevante = silêncio\\n"
            "🔔 Telegram somente por mudança de cenário\\n"
            "🧠 Alertas explicam o motivo da decisão\\n"
            "🛡️ Trading real: DESATIVADO"'''

if old in s:
    s = s.replace(old, new)

p.write_text(s, encoding="utf-8")

print("EVENT MODE INSTALADO")
