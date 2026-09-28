import json
import sqlite3
import time
from datetime import datetime, timezone

import requests

HL = "https://api.hyperliquid.xyz/info"
DB = "agent.db"


# =========================================================
# UTILIDADES
# =========================================================

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_config():
    with open("config.json", "r") as f:
        return json.load(f)


def api(payload):
    r = requests.post(HL, json=payload, timeout=20)
    r.raise_for_status()
    return r.json()


def init_db():
    db = sqlite3.connect(DB)

    db.execute("""
    CREATE TABLE IF NOT EXISTS scans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        asset TEXT,
        price REAL,
        funding REAL,
        oi REAL,
        oi_delta REAL,
        rsi_1h REAL,
        ema20_1h REAL,
        ema50_1h REAL,
        rsi_4h REAL,
        ema20_4h REAL,
        ema50_4h REAL,
        atr_1h REAL,
        regime TEXT,
        direction TEXT,
        trading_score REAL,
        accumulation_score REAL,
        confluence INTEGER,
        decision TEXT,
        thesis TEXT,
        counter_thesis TEXT
    )
    """)

    db.commit()
    return db


# =========================================================
# INDICADORES
# =========================================================

def ema(values, period):
    if not values:
        return 0

    k = 2 / (period + 1)
    result = values[0]

    for value in values[1:]:
        result = value * k + result * (1 - k)

    return result


def rsi(values, period=14):
    if len(values) < period + 1:
        return 50

    gains = []
    losses = []

    for i in range(-period, 0):
        change = values[i] - values[i - 1]

        if change >= 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(candles, period=14):
    if len(candles) < period + 1:
        return 0

    tr = []

    for i in range(-period, 0):
        high = float(candles[i]["h"])
        low = float(candles[i]["l"])
        previous_close = float(candles[i - 1]["c"])

        tr.append(max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close)
        ))

    return sum(tr) / len(tr)


# =========================================================
# DADOS DE MERCADO
# =========================================================

def market_context(asset):
    meta, contexts = api({"type": "metaAndAssetCtxs"})

    for i, coin in enumerate(meta["universe"]):
        if coin["name"] == asset:
            ctx = contexts[i]

            return {
                "price": float(ctx["markPx"]),
                "funding": float(ctx["funding"]),
                "oi": float(ctx["openInterest"]),
                "volume": float(ctx["dayNtlVlm"])
            }

    raise ValueError(f"{asset} não encontrado")


def candles(asset, interval, number=100):

    seconds = {
        "15m": 900,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400
    }

    end = int(time.time() * 1000)
    start = end - seconds[interval] * number * 1000

    return api({
        "type": "candleSnapshot",
        "req": {
            "coin": asset,
            "interval": interval,
            "startTime": start,
            "endTime": end
        }
    })


def previous_oi(db, asset):
    row = db.execute(
        """
        SELECT oi
        FROM scans
        WHERE asset=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (asset,)
    ).fetchone()

    return row[0] if row else None


# =========================================================
# ANÁLISE
# =========================================================

def analyze(asset, db):

    market = market_context(asset)

    c1 = candles(asset, "1h")
    c4 = candles(asset, "4h")

    close1 = [float(x["c"]) for x in c1]
    close4 = [float(x["c"]) for x in c4]

    rsi1 = rsi(close1)
    rsi4 = rsi(close4)

    e20_1 = ema(close1[-60:], 20)
    e50_1 = ema(close1[-80:], 50)

    e20_4 = ema(close4[-60:], 20)
    e50_4 = ema(close4[-80:], 50)

    atr1 = atr(c1)

    price = market["price"]
    funding = market["funding"]
    oi = market["oi"]

    old_oi = previous_oi(db, asset)

    if old_oi:
        oi_delta = ((oi / old_oi) - 1) * 100
    else:
        oi_delta = 0


    # =====================================================
    # REGIME
    # =====================================================

    if e20_4 > e50_4 and price > e20_4:
        regime = "UPTREND"

    elif e20_4 < e50_4 and price < e20_4:
        regime = "DOWNTREND"

    else:
        regime = "TRANSITION/RANGE"


    # =====================================================
    # TRADING SCORE
    #
    # NÃO representa probabilidade de ganhar.
    # Mede apenas qualidade/confluência do setup.
    # =====================================================

    long_score = 0
    short_score = 0

    long_reasons = []
    short_reasons = []


    # Estrutura 4H
    if e20_4 > e50_4:
        long_score += 25
        long_reasons.append("estrutura 4H bullish")

    else:
        short_score += 25
        short_reasons.append("estrutura 4H bearish")


    # Estrutura 1H
    if e20_1 > e50_1:
        long_score += 15
        long_reasons.append("estrutura 1H bullish")

    else:
        short_score += 15
        short_reasons.append("estrutura 1H bearish")


    # RSI
    if rsi1 < 35:
        long_score += 15
        long_reasons.append("RSI 1H deprimido")

    elif rsi1 > 65:
        short_score += 15
        short_reasons.append("RSI 1H elevado")


    # Funding / crowding
    if funding < -0.00005:
        long_score += 15
        long_reasons.append("funding negativo")

    elif funding > 0.0001:
        short_score += 15
        short_reasons.append("funding positivo")


    # OI
    if oi_delta > 1:

        if price > e20_1:
            long_score += 15
            long_reasons.append("OI aumentando com força compradora")

        else:
            short_score += 15
            short_reasons.append("OI aumentando com pressão vendedora")


    elif oi_delta < -1:
        long_reasons.append("reset de open interest")
        short_reasons.append("reset de open interest")


    # Momentum 4H
    if len(close4) >= 2:

        ret4 = (close4[-1] / close4[-2] - 1) * 100

        if ret4 > 1:
            long_score += 10
            long_reasons.append("momentum 4H positivo")

        elif ret4 < -1:
            short_score += 10
            short_reasons.append("momentum 4H negativo")

    else:
        ret4 = 0


    # =====================================================
    # DIREÇÃO
    # =====================================================

    if long_score > short_score:
        direction = "LONG"
        trading_score = long_score
        reasons = long_reasons

    elif short_score > long_score:
        direction = "SHORT"
        trading_score = short_score
        reasons = short_reasons

    else:
        direction = "NEUTRAL"
        trading_score = long_score
        reasons = []


    # =====================================================
    # CONFLUÊNCIA
    # =====================================================

    confluence = 0

    if direction == "LONG":

        if e20_4 > e50_4:
            confluence += 1

        if e20_1 > e50_1:
            confluence += 1

        if rsi1 < 40:
            confluence += 1

        if funding <= 0:
            confluence += 1

        if oi_delta > 1:
            confluence += 1

        if ret4 > 0:
            confluence += 1


    elif direction == "SHORT":

        if e20_4 < e50_4:
            confluence += 1

        if e20_1 < e50_1:
            confluence += 1

        if rsi1 > 60:
            confluence += 1

        if funding > 0:
            confluence += 1

        if oi_delta > 1:
            confluence += 1

        if ret4 < 0:
            confluence += 1


    # =====================================================
    # ACCUMULATION SCORE
    # =====================================================

    accumulation = 0
    accumulation_reasons = []

    high20 = max(close4[-20:])

    correction = (price / high20 - 1) * 100

    if correction <= -5:
        accumulation += 20
        accumulation_reasons.append(
            f"correção de {abs(correction):.1f}%"
        )

    if correction <= -10:
        accumulation += 15

    if rsi1 < 35:
        accumulation += 20
        accumulation_reasons.append("RSI 1H deprimido")

    if rsi4 < 40:
        accumulation += 15
        accumulation_reasons.append("RSI 4H deprimido")

    if funding < 0:
        accumulation += 15
        accumulation_reasons.append("funding negativo")

    if oi_delta < -1:
        accumulation += 15
        accumulation_reasons.append("reset de OI")

    # Recuperação inicial depois da correção
    if correction < -5 and price > e20_1:
        accumulation += 15
        accumulation_reasons.append(
            "preço recuperando EMA20 1H"
        )

    accumulation = min(accumulation, 100)
    trading_score = min(trading_score, 100)


    # =====================================================
    # DECISÃO
    # =====================================================

    cfg = load_config()

    if (
        trading_score >= cfg["trading_threshold"]
        and confluence >= cfg["minimum_confluence"]
    ):
        decision = direction

    elif accumulation >= cfg["accumulation_threshold"]:
        decision = "ACCUMULATION_WATCH"

    elif trading_score >= 50:
        decision = "WATCH"

    else:
        decision = "NO_TRADE"


    thesis = (
        ", ".join(reasons[:5])
        if reasons
        else "sem confluência direcional suficiente"
    )

    if direction == "LONG":
        counter = (
            "A tese perde força se a estrutura 1H/4H deteriorar, "
            "o OI crescer contra o preço ou o funding indicar crowding."
        )

    elif direction == "SHORT":
        counter = (
            "A tese perde força se houver recuperação estrutural, "
            "short squeeze ou absorção da pressão vendedora."
        )

    else:
        counter = "Mercado sem direção suficientemente clara."


    return {
        "asset": asset,
        "price": price,
        "funding": funding,
        "oi": oi,
        "oi_delta": oi_delta,
        "rsi1": rsi1,
        "rsi4": rsi4,
        "ema20_1": e20_1,
        "ema50_1": e50_1,
        "ema20_4": e20_4,
        "ema50_4": e50_4,
        "atr1": atr1,
        "regime": regime,
        "direction": direction,
        "trading_score": trading_score,
        "accumulation_score": accumulation,
        "confluence": confluence,
        "decision": decision,
        "thesis": thesis,
        "counter": counter,
        "accumulation_reasons": accumulation_reasons
    }


# =========================================================
# BANCO
# =========================================================

def save(db, x):

    db.execute("""
    INSERT INTO scans (
        timestamp, asset, price, funding,
        oi, oi_delta,
        rsi_1h, ema20_1h, ema50_1h,
        rsi_4h, ema20_4h, ema50_4h,
        atr_1h, regime, direction,
        trading_score, accumulation_score,
        confluence, decision,
        thesis, counter_thesis
    )
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        now(),
        x["asset"],
        x["price"],
        x["funding"],
        x["oi"],
        x["oi_delta"],
        x["rsi1"],
        x["ema20_1"],
        x["ema50_1"],
        x["rsi4"],
        x["ema20_4"],
        x["ema50_4"],
        x["atr1"],
        x["regime"],
        x["direction"],
        x["trading_score"],
        x["accumulation_score"],
        x["confluence"],
        x["decision"],
        x["thesis"],
        x["counter"]
    ))

    db.commit()


# =========================================================
# DISPLAY
# =========================================================

def display(x):

    print("\n" + "=" * 55)

    print(f"{x['asset']}  ${x['price']:,.2f}")

    print("-" * 55)

    print(f"REGIME:             {x['regime']}")
    print(f"DIREÇÃO:            {x['direction']}")

    print(
        f"TRADING SCORE:      "
        f"{x['trading_score']:.0f}/100"
    )

    print(
        f"ACCUMULATION SCORE: "
        f"{x['accumulation_score']:.0f}/100"
    )

    print(
        f"CONFLUÊNCIA:        "
        f"{x['confluence']}/6"
    )

    print()

    print(f"RSI 1H:             {x['rsi1']:.1f}")
    print(f"RSI 4H:             {x['rsi4']:.1f}")

    print(
        f"FUNDING:            "
        f"{x['funding'] * 100:.5f}%"
    )

    print(
        f"OI DELTA:           "
        f"{x['oi_delta']:+.2f}%"
    )

    print()

    print(f"DECISÃO: >>> {x['decision']} <<<")

    print()
    print("TESE:")
    print(x["thesis"])

    print()
    print("POR QUE NÃO:")
    print(x["counter"])

    if x["accumulation_reasons"]:

        print()
        print("ACUMULAÇÃO:")

        for reason in x["accumulation_reasons"]:
            print(" +", reason)


# =========================================================
# CICLO
# =========================================================

def run_cycle():

    cfg = load_config()
    db = init_db()

    print("\n")
    print("=" * 55)
    print(" CRYPTO MARKET AGENT V0")
    print(" PAPER / OBSERVATION MODE")
    print(" REAL TRADING: DISABLED")
    print("=" * 55)

    print("UTC:", now())

    for asset in cfg["assets"]:

        try:

            x = analyze(asset, db)

            save(db, x)

            display(x)

        except Exception as e:

            print()
            print(
                f"ERRO EM {asset}: "
                f"{type(e).__name__}: {e}"
            )

    db.close()


if __name__ == "__main__":

    cfg = load_config()

    print(
        f"\nAgente iniciado. "
        f"Scan a cada {cfg['scan_seconds']} segundos."
    )

    while True:

        started = time.time()

        run_cycle()

        elapsed = time.time() - started

        wait = max(
            5,
            cfg["scan_seconds"] - elapsed
        )

        print()
        print(
            f"Próximo scan em aproximadamente "
            f"{wait:.0f}s..."
        )

        time.sleep(wait)
