import sqlite3
import json
import urllib.request
from datetime import datetime, timezone

DB = "agent.db"
HL_INFO = "https://api.hyperliquid.xyz/info"


def hl_post(payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        HL_INFO,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def candles(asset, start_ms, end_ms):
    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": asset,
            "interval": "1m",
            "startTime": start_ms,
            "endTime": end_ms,
        },
    }
    return hl_post(payload)


def parse_time(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def money(x):
    return f"${x:,.2f}"


def classify_trade(row):
    (
        trade_id, timestamp, asset, side, setup_type,
        entry, stop, risk_usd, notional,
        t1, t2, t3, tactical_score
    ) = row

    start = parse_time(timestamp)
    start_ms = int(start.timestamp() * 1000)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # Hyperliquid limita a quantidade de candles retornados.
    # Buscamos em blocos.
    cursor = start_ms
    all_candles = []

    while cursor < now_ms:
        end = min(cursor + (4500 * 60 * 1000), now_ms)

        batch = candles(asset, cursor, end)

        if not batch:
            cursor = end + 1
            continue

        all_candles.extend(batch)

        last_t = int(batch[-1]["t"])

        if last_t <= cursor:
            break

        cursor = last_t + 60000

    highest_target = 0
    first_target_time = None
    stop_time = None
    exit_price = None
    exit_reason = "OPEN"

    highest_price = entry
    lowest_price = entry

    for c in all_candles:
        high = float(c["h"])
        low = float(c["l"])
        ts = int(c["t"])

        highest_price = max(highest_price, high)
        lowest_price = min(lowest_price, low)

        if side == "SHORT":
            stop_hit = high >= stop
            hit1 = low <= t1
            hit2 = low <= t2
            hit3 = low <= t3
        else:
            stop_hit = low <= stop
            hit1 = high >= t1
            hit2 = high >= t2
            hit3 = high >= t3

        # Conservador: se STOP e target aparecem no mesmo
        # candle de 1 minuto e não sabemos a ordem intraminuto,
        # considera STOP primeiro.
        if stop_hit:
            stop_time = ts
            exit_price = stop
            exit_reason = "STOP"
            break

        if hit3:
            highest_target = 3
            first_target_time = first_target_time or ts
            exit_price = t3
            exit_reason = "T3"
            break

        if hit2 and highest_target < 2:
            highest_target = 2
            first_target_time = first_target_time or ts

        elif hit1 and highest_target < 1:
            highest_target = 1
            first_target_time = first_target_time or ts

    if exit_reason == "STOP":
        pnl_r = -1.0
        pnl_usd = -risk_usd

    elif exit_reason == "T3":
        pnl_r = 3.0
        pnl_usd = risk_usd * 3

    else:
        # T1/T2 não são tratados como fechamento porque
        # o agente atual ainda não definiu saída parcial.
        current = None

        try:
            mids = hl_post({"type": "allMids"})
            current = float(mids[asset])
        except Exception:
            pass

        if current is not None:
            if side == "SHORT":
                pnl_usd = (
                    (entry - current) / entry
                ) * notional
            else:
                pnl_usd = (
                    (current - entry) / entry
                ) * notional

            pnl_r = (
                pnl_usd / risk_usd
                if risk_usd else 0
            )
        else:
            pnl_usd = 0
            pnl_r = 0

        if highest_target == 2:
            exit_reason = "OPEN_T2"
        elif highest_target == 1:
            exit_reason = "OPEN_T1"
        else:
            exit_reason = "OPEN"

    event_ms = stop_time or first_target_time

    event_time = "-"
    if event_ms:
        event_time = datetime.fromtimestamp(
            event_ms / 1000,
            tz=timezone.utc
        ).strftime("%d/%m %H:%M")

    return {
        "id": trade_id,
        "time": start.strftime("%d/%m %H:%M"),
        "asset": asset,
        "side": side,
        "setup": setup_type,
        "entry": entry,
        "stop": stop,
        "t1": t1,
        "t2": t2,
        "t3": t3,
        "score": tactical_score,
        "result": exit_reason,
        "event_time": event_time,
        "pnl": pnl_usd,
        "r": pnl_r,
        "high": highest_price,
        "low": lowest_price,
    }


def main():
    conn = sqlite3.connect(DB)

    rows = conn.execute("""
        SELECT
            id,
            timestamp,
            asset,
            side,
            setup_type,
            entry,
            stop,
            risk_usd,
            notional_usd,
            t1,
            t2,
            t3,
            tactical_score
        FROM leverage_signals
        ORDER BY timestamp
    """).fetchall()

    conn.close()

    if not rows:
        print("Nenhum sinal PAPER encontrado.")
        return

    print()
    print("=" * 78)
    print("PAPER PERFORMANCE — HISTÓRICO")
    print("=" * 78)

    results = []

    for row in rows:
        try:
            r = classify_trade(row)
            results.append(r)

            print(
                f"#{r['id']:02d} "
                f"{r['time']} UTC | "
                f"{r['asset']:<5} "
                f"{r['side']:<5} | "
                f"{r['result']:<8} | "
                f"{r['pnl']:+8.2f} USD | "
                f"{r['r']:+5.2f}R"
            )

        except Exception as e:
            print(
                f"#{row[0]:02d} {row[2]} "
                f"ERRO: {e}"
            )

    closed = [
        r for r in results
        if r["result"] in ("STOP", "T3")
    ]

    open_trades = [
        r for r in results
        if r["result"].startswith("OPEN")
    ]

    stops = sum(
        r["result"] == "STOP"
        for r in results
    )

    t1 = sum(
        r["result"] in ("OPEN_T1", "OPEN_T2", "T3")
        for r in results
    )

    t2 = sum(
        r["result"] in ("OPEN_T2", "T3")
        for r in results
    )

    t3 = sum(
        r["result"] == "T3"
        for r in results
    )

    total_pnl = sum(r["pnl"] for r in results)

    wins = sum(
        r["pnl"] > 0
        for r in closed
    )

    losses = sum(
        r["pnl"] < 0
        for r in closed
    )

    win_rate = (
        wins / len(closed) * 100
        if closed else 0
    )

    print()
    print("=" * 78)
    print("RESUMO")
    print("=" * 78)
    print(f"Sinais analisados : {len(results)}")
    print(f"Stops             : {stops}")
    print(f"Atingiram T1      : {t1}")
    print(f"Atingiram T2      : {t2}")
    print(f"Atingiram T3      : {t3}")
    print(f"Ainda abertos     : {len(open_trades)}")
    print(f"Trades fechados   : {len(closed)}")
    print(f"Positivos fechados: {wins}")
    print(f"Negativos fechados: {losses}")
    print(f"Win rate fechado  : {win_rate:.1f}%")
    print(f"PnL total/MTM     : {money(total_pnl)}")
    print("=" * 78)


if __name__ == "__main__":
    main()
