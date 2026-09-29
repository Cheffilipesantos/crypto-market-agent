import json
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import paper_positions
import shadow_engine


def _request(token, method, params=None, timeout=10):
    url = f"https://api.telegram.org/bot{token}/{method}"

    if params:
        data = urllib.parse.urlencode(params).encode()
    else:
        data = None

    req = urllib.request.Request(url, data=data)

    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def send_message(token, chat_id, text):
    try:
        return _request(
            token,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
            },
        )
    except Exception as e:
        print("❌ Telegram command send:", e)
        return None


def _connect(db_file):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    return conn


def _fmt_money(value):
    try:
        return f"${float(value):,.2f}"
    except Exception:
        return "$0.00"


def _fmt_price(value):
    try:
        v = float(value)

        if v >= 1000:
            return f"${v:,.2f}"

        return f"${v:.4f}"
    except Exception:
        return "-"


def _unrealized(row):
    entry = float(row["entry"] or 0)
    price = float(row["last_price"] or entry)
    notional = float(row["notional_usd"] or 0)

    if not entry:
        return 0.0

    if row["side"] == "LONG":
        return ((price - entry) / entry) * notional

    return ((entry - price) / entry) * notional


def positions_report(db_file):
    conn = _connect(db_file)

    rows = conn.execute(
        """
        SELECT *
        FROM paper_positions
        WHERE status = 'OPEN'
        ORDER BY opened_at
        """
    ).fetchall()

    conn.close()

    if not rows:
        return (
            "📭 POSIÇÕES PAPER\n\n"
            "Nenhuma posição aberta."
        )

    lines = [
        "📊 POSIÇÕES PAPER ABERTAS",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    total_unrealized = 0.0
    total_risk = 0.0

    for r in rows:
        pnl = _unrealized(r)
        total_unrealized += pnl
        total_risk += float(r["risk_usd"] or 0)

        risk = float(r["risk_usd"] or 0)
        current_r = pnl / risk if risk else 0

        lines += [
            "",
            f"🪙 {r['asset']} {r['side']}",
            f"Setup: {r['setup_type']}",
            f"Entrada: {_fmt_price(r['entry'])}",
            f"Atual: {_fmt_price(r['last_price'])}",
            f"Stop: {_fmt_price(r['stop'])}",
            (
                f"T1/T2/T3: "
                f"{'✅' if r['t1_hit'] else '⬜'} "
                f"{'✅' if r['t2_hit'] else '⬜'} "
                f"{'✅' if r['t3_hit'] else '⬜'}"
            ),
            f"Exposição: {_fmt_money(r['notional_usd'])}",
            f"Risco: {_fmt_money(r['risk_usd'])}",
            f"Alavancagem: {float(r['leverage'] or 0):.1f}x",
            f"P&L aberto: {_fmt_money(pnl)} ({current_r:+.2f}R)",
            f"MFE: {_fmt_money(r['mfe_usd'])}",
            f"MAE: {_fmt_money(r['mae_usd'])}",
        ]

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        f"💰 P&L aberto total: {_fmt_money(total_unrealized)}",
        f"🛡️ Risco inicial somado: {_fmt_money(total_risk)}",
        "🧪 PAPER — trading real desativado",
    ]

    return "\n".join(lines)


def report(db_file, days=1):
    conn = _connect(db_file)

    rows = conn.execute(
        """
        SELECT *
        FROM paper_positions
        WHERE datetime(opened_at) >= datetime('now', ?)
        ORDER BY opened_at
        """,
        (f"-{int(days)} days",),
    ).fetchall()

    conn.close()

    title = "HOJE" if days == 1 else f"ÚLTIMOS {days} DIAS"

    if not rows:
        return (
            f"📊 RELATÓRIO PAPER — {title}\n\n"
            "Nenhuma operação encontrada."
        )

    closed = [r for r in rows if r["status"] == "CLOSED"]
    opened = [r for r in rows if r["status"] == "OPEN"]
    ambiguous = [r for r in rows if r["status"] == "AMBIGUOUS"]

    realized = sum(
        float(r["realized_pnl_usd"] or 0)
        for r in closed
    )

    unrealized = sum(_unrealized(r) for r in opened)

    wins = sum(
        1
        for r in closed
        if float(r["realized_pnl_usd"] or 0) > 0
    )

    losses = sum(
        1
        for r in closed
        if float(r["realized_pnl_usd"] or 0) < 0
    )

    decided = wins + losses
    winrate = (wins / decided * 100) if decided else 0

    total_r = sum(
        float(r["realized_r"] or 0)
        for r in closed
    )

    gross_profit = sum(
        float(r["realized_pnl_usd"] or 0)
        for r in closed
        if float(r["realized_pnl_usd"] or 0) > 0
    )

    gross_loss = abs(
        sum(
            float(r["realized_pnl_usd"] or 0)
            for r in closed
            if float(r["realized_pnl_usd"] or 0) < 0
        )
    )

    if gross_loss:
        pf = f"{gross_profit / gross_loss:.2f}"
    elif gross_profit:
        pf = "∞"
    else:
        pf = "-"

    lines = [
        f"📊 RELATÓRIO PAPER — {title}",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Operações registradas: {len(rows)}",
        f"🟢 Abertas: {len(opened)}",
        f"🏁 Encerradas: {len(closed)}",
        f"⚠️ Ambíguas: {len(ambiguous)}",
        "",
        f"✅ Wins: {wins}",
        f"❌ Losses: {losses}",
        f"🎯 Win rate: {winrate:.1f}%",
        f"📐 Resultado realizado: {total_r:+.2f}R",
        f"💰 P&L realizado: {_fmt_money(realized)}",
        f"📈 P&L aberto: {_fmt_money(unrealized)}",
        f"⚖️ Profit Factor: {pf}",
        "",
        "POR ATIVO",
    ]

    assets = sorted(set(r["asset"] for r in rows))

    for asset in assets:
        asset_rows = [r for r in rows if r["asset"] == asset]
        asset_closed = [
            r for r in asset_rows
            if r["status"] == "CLOSED"
        ]
        asset_open = [
            r for r in asset_rows
            if r["status"] == "OPEN"
        ]

        asset_realized = sum(
            float(r["realized_pnl_usd"] or 0)
            for r in asset_closed
        )

        asset_unrealized = sum(
            _unrealized(r)
            for r in asset_open
        )

        lines.append(
            f"• {asset}: "
            f"{len(asset_closed)} fechada(s), "
            f"{len(asset_open)} aberta(s) | "
            f"Real. {_fmt_money(asset_realized)} | "
            f"Aberto {_fmt_money(asset_unrealized)}"
        )

    lines += [
        "",
        "🧪 PAPER — trading real desativado",
    ]

    return "\n".join(lines)


def performance(db_file, asset=None):
    conn = _connect(db_file)

    if asset:
        rows = conn.execute(
            """
            SELECT *
            FROM paper_positions
            WHERE asset = ?
              AND status = 'CLOSED'
            ORDER BY opened_at
            """,
            (asset.upper(),),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT *
            FROM paper_positions
            WHERE status = 'CLOSED'
            ORDER BY opened_at
            """
        ).fetchall()

    conn.close()

    name = asset.upper() if asset else "GERAL"

    if not rows:
        return (
            f"📈 PERFORMANCE — {name}\n\n"
            "Ainda não existem operações encerradas suficientes."
        )

    wins = [
        r for r in rows
        if float(r["realized_pnl_usd"] or 0) > 0
    ]

    losses = [
        r for r in rows
        if float(r["realized_pnl_usd"] or 0) < 0
    ]

    pnl = sum(
        float(r["realized_pnl_usd"] or 0)
        for r in rows
    )

    total_r = sum(
        float(r["realized_r"] or 0)
        for r in rows
    )

    winrate = len(wins) / len(rows) * 100
    expectancy = total_r / len(rows)

    gross_profit = sum(
        float(r["realized_pnl_usd"] or 0)
        for r in wins
    )

    gross_loss = abs(
        sum(
            float(r["realized_pnl_usd"] or 0)
            for r in losses
        )
    )

    if gross_loss:
        pf = f"{gross_profit / gross_loss:.2f}"
    elif gross_profit:
        pf = "∞"
    else:
        pf = "-"

    return "\n".join([
        f"📈 PERFORMANCE — {name}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"Operações encerradas: {len(rows)}",
        f"✅ Wins: {len(wins)}",
        f"❌ Losses: {len(losses)}",
        f"🎯 Win rate: {winrate:.1f}%",
        f"💰 P&L: {_fmt_money(pnl)}",
        f"📐 Resultado: {total_r:+.2f}R",
        f"📊 Expectancy: {expectancy:+.2f}R/trade",
        f"⚖️ Profit Factor: {pf}",
        "",
        "🧪 PAPER — trading real desativado",
    ])


def posttrade_report(db_file, asset=None):
    conn = _connect(db_file)

    sql = """
        SELECT
            pt.*,
            pp.t1_hit AS pre_t1_hit,
            pp.t2_hit AS pre_t2_hit,
            pp.t3_hit AS pre_t3_hit
        FROM post_trade_tracking pt
        JOIN paper_positions pp
          ON pp.id = pt.position_id
        WHERE pt.test_era = ?
          AND pt.exit_reason = 'STOP'
    """

    params = [paper_positions.TEST_ERA]

    if asset:
        sql += " AND pt.asset = ?"
        params.append(asset.upper())

    sql += " ORDER BY pt.closed_at"

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    name = asset.upper() if asset else "GERAL"

    if not rows:
        return (
            f"🔬 POST-TRADE — {name}\n\n"
            "Ainda não existem STOPs rastreados nesta era."
        )

    completed = [
        r for r in rows
        if r["tracking_status"] == "COMPLETED"
    ]

    active = [
        r for r in rows
        if r["tracking_status"] == "ACTIVE"
    ]

    post_t1 = [
        r for r in completed
        if int(r["post_t1_hit"] or 0)
    ]
    post_t2 = [
        r for r in completed
        if int(r["post_t2_hit"] or 0)
    ]
    post_t3 = [
        r for r in completed
        if int(r["post_t3_hit"] or 0)
    ]

    pre_t1 = [r for r in rows if int(r["pre_t1_hit"] or 0)]
    pre_t2 = [r for r in rows if int(r["pre_t2_hit"] or 0)]

    if completed:
        avg_mfe = sum(
            float(r["post_mfe_usd"] or 0)
            for r in completed
        ) / len(completed)

        avg_mae = sum(
            float(r["post_mae_usd"] or 0)
            for r in completed
        ) / len(completed)

        post_stats = [
            f"🎯 T1 atingido: {len(post_t1)}/{len(completed)}",
            f"🎯 T2 atingido: {len(post_t2)}/{len(completed)}",
            f"🎯 T3 atingido: {len(post_t3)}/{len(completed)}",
            f"📈 MFE médio 24h: {_fmt_money(avg_mfe)}",
            f"📉 MAE médio 24h: {_fmt_money(avg_mae)}",
        ]
    else:
        post_stats = [
            "⏳ Nenhuma operação completou 24h ainda."
        ]

    lines = [
        f"🔬 POST-TRADE — {name}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"STOPs rastreados: {len(rows)}",
        f"⏳ Em acompanhamento: {len(active)}",
        f"✅ 24h completas: {len(completed)}",
        "",
        "DEPOIS DO STOP — AMOSTRA 24H",
        *post_stats,
        "",
        "ANTES DO STOP — TODOS OS RASTREADOS",
        f"✅ Já havia atingido T1: {len(pre_t1)}/{len(rows)}",
        f"✅ Já havia atingido T2: {len(pre_t2)}/{len(rows)}",
        "",
        "⚠️ Amostra em formação; não altera a estratégia.",
        "🧪 PAPER — trading real desativado",
    ]

    return "\n".join(lines)


def shadow_report(db_file):
    summary = shadow_engine.comparison_summary(
        db_file,
        paper_positions.TEST_ERA,
    )

    conn = _connect(db_file)
    open_rows = conn.execute(
        """
        SELECT *
        FROM shadow_positions
        WHERE test_era = ? AND status = 'OPEN'
        ORDER BY opened_at
        """,
        (paper_positions.TEST_ERA,),
    ).fetchall()
    conn.close()

    lines = [
        "👥 OFICIAL × SHADOW",
        "━━━━━━━━━━━━━━━━━━━━",
        f"Comparações de abertura: {summary['comparisons']}",
        f"🤝 Mesma direção: {summary['both_same_direction']}",
        f"↔️ Direções diferentes: {summary['both_different_direction']}",
        f"🔵 Só Oficial: {summary['official_only']}",
        f"🟣 Só Shadow: {summary['shadow_only']}",
        "",
        "RESULTADOS JÁ CONCLUÍDOS",
        f"🛡️ Loss Oficial evitado: {summary['shadow_avoided_official_loss']}",
        f"⚠️ Win Oficial recusado: {summary['shadow_missed_official_win']}",
        f"✅ Wins Shadow-only: {summary['shadow_only_wins']}",
        f"❌ Losses Shadow-only: {summary['shadow_only_losses']}",
        f"📊 Pares completos: {summary['completed_pairs']}",
    ]

    delta = summary["average_shadow_minus_official_r"]
    if delta is not None:
        lines.append(f"📐 Shadow − Oficial: {delta:+.2f}R/trade")

    lines += [
        "",
        f"POSIÇÕES SHADOW ABERTAS: {len(open_rows)}",
    ]

    for row in open_rows:
        lines.append(
            f"• {row['asset']} {row['side']} | "
            f"entrada {_fmt_price(row['entry'])} | "
            f"stop {_fmt_price(row['stop'])} | "
            f"MFE {float(row['mfe_r'] or 0):+.2f}R | "
            f"MAE {float(row['mae_r'] or 0):+.2f}R"
        )

    lines += [
        "",
        "🧪 Shadow observacional — não envia ordens.",
        "⚠️ Amostra pequena não promove estratégia.",
    ]
    return "\n".join(lines)


def help_text():
    return "\n".join([
        "🤖 COMANDOS DO AGENTE",
        "━━━━━━━━━━━━━━━━━━━━",
        "/posicoes",
        "Posições PAPER abertas",
        "",
        "/relatorio",
        "Relatório das últimas 24h",
        "",
        "/relatorio 7d",
        "Relatório dos últimos 7 dias",
        "",
        "/relatorio 30d",
        "Relatório dos últimos 30 dias",
        "",
        "/performance",
        "Performance geral",
        "",
        "/performance BTC",
        "Performance de um ativo",
        "",
        "/posttrade",
        "Análise dos movimentos após STOP",
        "",
        "/posttrade BTC",
        "Post-trade de um ativo",
        "",
        "/shadow",
        "Comparativo Oficial × Shadow",
        "",
        "Ativos: BTC ETH SOL AAVE BNB",
    ])


def process_command(db_file, token, allowed_chat_id, text):
    text = (text or "").strip()

    if not text.startswith("/"):
        return None

    parts = text.split()
    command = parts[0].split("@")[0].lower()

    if command in ("/ajuda", "/help", "/start"):
        return help_text()

    if command == "/posicoes":
        return positions_report(db_file)

    if command == "/relatorio":
        days = 1

        if len(parts) > 1:
            arg = parts[1].lower()

            if arg == "7d":
                days = 7
            elif arg == "30d":
                days = 30
            elif arg not in ("hoje", "1d"):
                return "Use: /relatorio, /relatorio 7d ou /relatorio 30d"

        return report(db_file, days)

    if command == "/performance":
        asset = None

        if len(parts) > 1:
            asset = parts[1].upper()

            if asset not in ("BTC", "ETH", "SOL", "AAVE", "BNB"):
                return "Ativo inválido. Use BTC, ETH, SOL, AAVE ou BNB."

        return performance(db_file, asset)

    if command == "/shadow":
        return shadow_report(db_file)

    if command == "/posttrade":
        asset = None

        if len(parts) > 1:
            asset = parts[1].upper()

            if asset not in ("BTC", "ETH", "SOL", "AAVE", "BNB"):
                return "Ativo inválido. Use BTC, ETH, SOL, AAVE ou BNB."

        return posttrade_report(db_file, asset)

    return None


def poll_commands(db_file, token, allowed_chat_id, state):
    if not token or not allowed_chat_id:
        return

    try:
        params = {
            "timeout": 0,
            "limit": 20,
        }

        if state.get("offset") is not None:
            params["offset"] = state["offset"]

        result = _request(
            token,
            "getUpdates",
            params,
            timeout=5,
        )

        for update in result.get("result", []):
            update_id = update.get("update_id")

            if update_id is not None:
                state["offset"] = update_id + 1

            message = update.get("message") or {}
            chat = message.get("chat") or {}

            if str(chat.get("id")) != str(allowed_chat_id):
                continue

            response = process_command(
                db_file,
                token,
                allowed_chat_id,
                message.get("text"),
            )

            if response:
                send_message(
                    token,
                    allowed_chat_id,
                    response,
                )

    except Exception as e:
        print("⚠️ Telegram commands:", e)
