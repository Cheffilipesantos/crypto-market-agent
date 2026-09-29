import os
import json
import time
import sqlite3
from datetime import datetime, timezone, timedelta

import requests
import leverage_engine
import paper_positions
import telegram_commands
import learning_engine
import shadow_engine
import market_structure


# ============================================================
# CRYPTO MARKET AGENT V0.2
# Market Intelligence + Paper Observation
# REAL TRADING: DESATIVADO
# ============================================================

HL_URL = "https://api.hyperliquid.xyz/info"
DB_FILE = "agent.db"
CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "assets": ["BTC", "ETH", "SOL", "AAVE", "BNB"],
    "scan_seconds": 300,
    "paper_balance": 10000,
    "risk_per_trade": 0.01,
    "minimum_confluence": 4,
    "trading_threshold": 70,
    "accumulation_threshold": 70,
    "real_trading": False,
    "telegram_summary_hours": 4
}


# ============================================================
# CONFIG
# ============================================================

def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
    except Exception:
        cfg = DEFAULT_CONFIG.copy()

    for key, value in DEFAULT_CONFIG.items():
        if key not in cfg:
            cfg[key] = value

    # Segurança absoluta nesta fase
    cfg["real_trading"] = False

    return cfg


CONFIG = load_config()

ASSETS = CONFIG["assets"]
SCAN_SECONDS = int(CONFIG["scan_seconds"])
TRADING_THRESHOLD = int(CONFIG["trading_threshold"])
ACCUMULATION_THRESHOLD = int(CONFIG["accumulation_threshold"])
MIN_CONFLUENCE = int(CONFIG["minimum_confluence"])

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ============================================================
# DATABASE
# ============================================================

def db():
    return sqlite3.connect(DB_FILE)


def column_exists(conn, table, column):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def init_db():
    conn = db()

    conn.execute("""
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
            trading_score INTEGER,
            accumulation_score INTEGER,
            confluence INTEGER,
            decision TEXT,
            thesis TEXT,
            counter_thesis TEXT
        )
    """)

    # Novas colunas V0.2.
    # ALTER TABLE somente se ainda não existirem.
    additions = {
        "setup_direction": "TEXT",
        "timing": "TEXT",
        "momentum_4h": "REAL"
    }

    for column, datatype in additions.items():
        if not column_exists(conn, "scans", column):
            conn.execute(
                f"ALTER TABLE scans ADD COLUMN {column} {datatype}"
            )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS alert_state (
            asset TEXT PRIMARY KEY,
            decision TEXT,
            setup_direction TEXT,
            timing TEXT,
            score_bucket INTEGER,
            accumulation_bucket INTEGER,
            updated_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# TELEGRAM
# ============================================================

def telegram_ready():
    return bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)


def send_telegram(text):
    if not telegram_ready():
        return False

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_TOKEN
        + "/sendMessage"
    )

    try:
        r = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text
            },
            timeout=20
        )

        if not r.ok:
            print("⚠️ Telegram HTTP:", r.status_code)
            return False

        return True

    except Exception as e:
        print("⚠️ Erro Telegram:", e)
        return False


# ============================================================
# HYPERLIQUID API
# ============================================================

def hl_post(payload, retries=3):
    last_error = None

    for attempt in range(retries):
        try:
            r = requests.post(
                HL_URL,
                json=payload,
                timeout=20
            )
            r.raise_for_status()
            return r.json()

        except Exception as e:
            last_error = e

            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))

    raise RuntimeError(
        f"Falha Hyperliquid após {retries} tentativas: "
        f"{last_error}"
    )


def get_market_contexts():
    data = hl_post({"type": "metaAndAssetCtxs"})

    meta = data[0]
    contexts = data[1]

    result = {}

    for i, coin in enumerate(meta["universe"]):
        name = coin["name"]

        if name in ASSETS:
            ctx = contexts[i]

            result[name] = {
                "price": float(ctx["markPx"]),
                "funding": float(ctx["funding"]),
                "oi": float(ctx["openInterest"]),
                "volume_24h": float(ctx["dayNtlVlm"])
            }

    return result


def get_candles(asset, interval, bars=220):
    interval_ms = {
        "1m": 60 * 1000,
        "5m": 5 * 60 * 1000,
        "15m": 15 * 60 * 1000,
        "1h": 60 * 60 * 1000,
        "2h": 2 * 60 * 60 * 1000,
        "4h": 4 * 60 * 60 * 1000,
        "1d": 24 * 60 * 60 * 1000
    }

    now_ms = int(time.time() * 1000)

    start_ms = now_ms - (
        interval_ms[interval] * bars
    )

    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": asset,
            "interval": interval,
            "startTime": start_ms,
            "endTime": now_ms
        }
    }

    raw = hl_post(payload)

    candles = []

    for c in raw:
        candles.append({
            "time": c["t"],
            "open": float(c["o"]),
            "high": float(c["h"]),
            "low": float(c["l"]),
            "close": float(c["c"]),
            "volume": float(c["v"])
        })

    return candles


# ============================================================
# INDICADORES
# ============================================================

def ema(values, period):
    if not values:
        return 0.0

    k = 2 / (period + 1)
    result = values[0]

    for value in values[1:]:
        result = value * k + result * (1 - k)

    return result


def rsi(values, period=14):
    if len(values) < period + 1:
        return 50.0

    changes = []

    for i in range(1, len(values)):
        changes.append(values[i] - values[i - 1])

    recent = changes[-period:]

    gains = [
        x if x > 0 else 0
        for x in recent
    ]

    losses = [
        abs(x) if x < 0 else 0
        for x in recent
    ]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def atr(candles, period=14):
    if len(candles) < period + 1:
        return 0.0

    trs = []

    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        previous_close = candles[i - 1]["close"]

        tr = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close)
        )

        trs.append(tr)

    recent = trs[-period:]

    return sum(recent) / len(recent)


# ============================================================
# HISTÓRICO / OI
# ============================================================

def previous_oi(asset):
    conn = db()

    row = conn.execute(
        """
        SELECT oi
        FROM scans
        WHERE asset = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (asset,)
    ).fetchone()

    conn.close()

    if not row:
        return None

    return float(row[0])


def calculate_oi_delta(asset, current_oi):
    previous = previous_oi(asset)

    if previous is None or previous == 0:
        return 0.0

    return ((current_oi - previous) / previous) * 100


# ============================================================
# REGIME
# ============================================================

def detect_regime(price, ema20_4h, ema50_4h):
    if (
        ema20_4h > ema50_4h
        and price > ema20_4h
    ):
        return "UPTREND"

    if (
        ema20_4h < ema50_4h
        and price < ema20_4h
    ):
        return "DOWNTREND"

    return "TRANSITION/RANGE"


# ============================================================
# SETUP
# ============================================================

def calculate_setup(
    price,
    regime,
    ema20_1h,
    ema50_1h,
    rsi_1h,
    funding,
    oi_delta,
    momentum_4h
):
    long_score = 0
    short_score = 0

    long_reasons = []
    short_reasons = []

    # Estrutura 4H
    if regime == "UPTREND":
        long_score += 25
        long_reasons.append("estrutura 4H bullish")

    elif regime == "DOWNTREND":
        short_score += 25
        short_reasons.append("estrutura 4H bearish")

    # Estrutura 1H
    if price > ema20_1h > ema50_1h:
        long_score += 15
        long_reasons.append("estrutura 1H bullish")

    elif price < ema20_1h < ema50_1h:
        short_score += 15
        short_reasons.append("estrutura 1H bearish")

    # RSI
    if rsi_1h < 35:
        long_score += 15
        long_reasons.append("RSI 1H descontado")

    elif rsi_1h > 65:
        short_score += 15
        short_reasons.append("RSI 1H esticado")

    # Funding
    if funding < -0.00005:
        long_score += 15
        long_reasons.append("funding negativo")

    elif funding > 0.0001:
        short_score += 15
        short_reasons.append("funding positivo elevado")

    # OI + preço
    if oi_delta > 1.0:
        if price > ema20_1h:
            long_score += 15
            long_reasons.append("OI crescendo com preço forte")
        elif price < ema20_1h:
            short_score += 15
            short_reasons.append("OI crescendo com preço fraco")

    # Momentum 4H
    if momentum_4h > 1:
        long_score += 10
        long_reasons.append("momentum 4H positivo")

    elif momentum_4h < -1:
        short_score += 10
        short_reasons.append("momentum 4H negativo")

    if long_score > short_score:
        direction = "BULLISH"
        score = long_score
        reasons = long_reasons

    elif short_score > long_score:
        direction = "BEARISH"
        score = short_score
        reasons = short_reasons

    else:
        direction = "NEUTRAL"
        score = max(long_score, short_score)
        reasons = long_reasons + short_reasons

    return (
        direction,
        min(score, 100),
        reasons,
        long_score,
        short_score
    )


# ============================================================
# CONFLUÊNCIA
# ============================================================

def calculate_confluence(
    setup_direction,
    regime,
    price,
    ema20_1h,
    ema50_1h,
    rsi_1h,
    funding,
    oi_delta,
    momentum_4h
):
    count = 0

    if setup_direction == "BULLISH":

        if regime == "UPTREND":
            count += 1

        if price > ema20_1h > ema50_1h:
            count += 1

        if rsi_1h < 65:
            count += 1

        if funding <= 0.0001:
            count += 1

        if oi_delta > 0:
            count += 1

        if momentum_4h > 0:
            count += 1

    elif setup_direction == "BEARISH":

        if regime == "DOWNTREND":
            count += 1

        if price < ema20_1h < ema50_1h:
            count += 1

        if rsi_1h > 35:
            count += 1

        if funding >= -0.00005:
            count += 1

        if oi_delta > 0:
            count += 1

        if momentum_4h < 0:
            count += 1

    return count


# ============================================================
# TIMING
# ============================================================

def determine_timing(
    setup_direction,
    regime,
    price,
    ema20_1h,
    rsi_1h,
    rsi_4h,
    trading_score,
    confluence
):
    # Aqui está uma das principais correções da V0.2:
    # direção estrutural NÃO significa entrada imediata.

    if setup_direction == "BULLISH":

        if rsi_1h >= 75 or rsi_4h >= 75:
            return "WAIT_PULLBACK"

        if rsi_1h >= 68:
            return "EXTENDED"

        distance = (
            ((price - ema20_1h) / ema20_1h) * 100
            if ema20_1h
            else 0
        )

        if distance > 3:
            return "WAIT_PULLBACK"

        if (
            trading_score >= TRADING_THRESHOLD
            and confluence >= MIN_CONFLUENCE
            and regime == "UPTREND"
        ):
            return "ENTRY_ZONE"

        return "WAIT_CONFIRMATION"

    if setup_direction == "BEARISH":

        if rsi_1h <= 25 or rsi_4h <= 25:
            return "WAIT_BOUNCE"

        if rsi_1h <= 32:
            return "EXTENDED"

        distance = (
            ((ema20_1h - price) / ema20_1h) * 100
            if ema20_1h
            else 0
        )

        if distance > 3:
            return "WAIT_BOUNCE"

        if (
            trading_score >= TRADING_THRESHOLD
            and confluence >= MIN_CONFLUENCE
            and regime == "DOWNTREND"
        ):
            return "ENTRY_ZONE"

        return "WAIT_CONFIRMATION"

    return "NO_SETUP"


# ============================================================
# ACCUMULATION ENGINE
# ============================================================

def calculate_accumulation(
    price,
    candles_4h,
    rsi_1h,
    rsi_4h,
    funding,
    oi_delta,
    ema20_1h
):
    score = 0
    reasons = []

    highs = [
        c["high"]
        for c in candles_4h[-20:]
    ]

    high_20 = max(highs) if highs else price

    correction = (
        ((price - high_20) / high_20) * 100
        if high_20
        else 0
    )

    if correction <= -5:
        score += 20
        reasons.append(
            f"correção 4H de {correction:.1f}%"
        )

    if correction <= -10:
        score += 20

    if rsi_1h < 35:
        score += 15
        reasons.append("RSI 1H deprimido")

    if rsi_4h < 40:
        score += 15
        reasons.append("RSI 4H deprimido")

    if funding < 0:
        score += 10
        reasons.append("funding negativo")

    if oi_delta < -1:
        score += 10
        reasons.append("OI em reset")

    if price > ema20_1h and correction <= -5:
        score += 10
        reasons.append("recuperação da EMA20 1H")

    return min(score, 100), correction, reasons


# ============================================================
# DECISÃO
# ============================================================

def make_decision(
    setup_direction,
    timing,
    trading_score,
    accumulation_score,
    confluence
):
    if (
        trading_score >= TRADING_THRESHOLD
        and confluence >= MIN_CONFLUENCE
        and timing == "ENTRY_ZONE"
    ):
        if setup_direction == "BULLISH":
            return "LONG_SETUP"

        if setup_direction == "BEARISH":
            return "SHORT_SETUP"

    if accumulation_score >= ACCUMULATION_THRESHOLD:
        return "ACCUMULATION_WATCH"

    if (
        trading_score >= 50
        or timing in (
            "WAIT_PULLBACK",
            "WAIT_BOUNCE",
            "EXTENDED"
        )
    ):
        return "WATCH"

    return "NO_TRADE"


# ============================================================
# TESE / CONTRATESE
# ============================================================

def build_thesis(
    setup_direction,
    regime,
    reasons,
    rsi_1h,
    rsi_4h,
    oi_delta
):
    thesis_parts = []

    if regime == "UPTREND":
        thesis_parts.append("estrutura 4H bullish")

    elif regime == "DOWNTREND":
        thesis_parts.append("estrutura 4H bearish")

    if reasons:
        thesis_parts.extend(reasons[:3])

    if not thesis_parts:
        thesis_parts.append("sem confluência estrutural suficiente")

    thesis = ", ".join(
        dict.fromkeys(thesis_parts)
    )

    counter = []

    if setup_direction == "BULLISH":

        if rsi_1h >= 70:
            counter.append("RSI 1H elevado")

        if rsi_4h >= 70:
            counter.append("RSI 4H elevado")

        if oi_delta < 0:
            counter.append("OI não confirma expansão")

        counter.append(
            "a tese perde força se a estrutura 1H/4H deteriorar"
        )

    elif setup_direction == "BEARISH":

        if rsi_1h <= 30:
            counter.append("RSI 1H muito vendido")

        if rsi_4h <= 30:
            counter.append("RSI 4H muito vendido")

        if oi_delta < 0:
            counter.append("OI não confirma expansão")

        counter.append(
            "a tese perde força se a estrutura 1H/4H recuperar"
        )

    else:
        counter.append(
            "não existe vantagem direcional clara"
        )

    return thesis, ", ".join(counter)


# ============================================================
# SALVAR SCAN
# ============================================================

def save_scan(data):
    conn = db()

    conn.execute("""
        INSERT INTO scans (
            timestamp,
            asset,
            price,
            funding,
            oi,
            oi_delta,
            rsi_1h,
            ema20_1h,
            ema50_1h,
            rsi_4h,
            ema20_4h,
            ema50_4h,
            atr_1h,
            regime,
            direction,
            trading_score,
            accumulation_score,
            confluence,
            decision,
            thesis,
            counter_thesis,
            setup_direction,
            timing,
            momentum_4h
        )
        VALUES (
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
    """, (
        data["timestamp"],
        data["asset"],
        data["price"],
        data["funding"],
        data["oi"],
        data["oi_delta"],
        data["rsi_1h"],
        data["ema20_1h"],
        data["ema50_1h"],
        data["rsi_4h"],
        data["ema20_4h"],
        data["ema50_4h"],
        data["atr_1h"],
        data["regime"],
        data["setup_direction"],
        data["trading_score"],
        data["accumulation_score"],
        data["confluence"],
        data["decision"],
        data["thesis"],
        data["counter_thesis"],
        data["setup_direction"],
        data["timing"],
        data["momentum_4h"]
    ))

    conn.commit()
    conn.close()


# ============================================================
# ALERT STATE
# ============================================================

def score_bucket(score):
    if score >= 70:
        return 70

    if score >= 50:
        return 50

    return 0


def accumulation_bucket(score):
    if score >= 70:
        return 70

    if score >= 50:
        return 50

    return 0


def get_alert_state(asset):
    conn = db()

    row = conn.execute(
        """
        SELECT
            decision,
            setup_direction,
            timing,
            score_bucket,
            accumulation_bucket
        FROM alert_state
        WHERE asset = ?
        """,
        (asset,)
    ).fetchone()

    conn.close()

    return row


def update_alert_state(data):
    conn = db()

    conn.execute("""
        INSERT OR REPLACE INTO alert_state (
            asset,
            decision,
            setup_direction,
            timing,
            score_bucket,
            accumulation_bucket,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        data["asset"],
        data["decision"],
        data["setup_direction"],
        data["timing"],
        score_bucket(data["trading_score"]),
        accumulation_bucket(
            data["accumulation_score"]
        ),
        data["timestamp"]
    ))

    conn.commit()
    conn.close()


def previous_scan_data(asset):
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


# ============================================================
# FORMATAÇÃO
# ============================================================

def regime_pt(regime):
    return {
        "UPTREND": "ALTA",
        "DOWNTREND": "BAIXA",
        "TRANSITION/RANGE": "LATERAL / TRANSIÇÃO"
    }.get(regime, regime)


def setup_pt(direction):
    return {
        "BULLISH": "BULLISH",
        "BEARISH": "BEARISH",
        "NEUTRAL": "NEUTRO"
    }.get(direction, direction)


def timing_pt(timing):
    return {
        "WAIT_PULLBACK": "AGUARDAR PULLBACK",
        "WAIT_BOUNCE": "AGUARDAR REPique",
        "EXTENDED": "PREÇO ESTICADO",
        "ENTRY_ZONE": "ZONA DE ENTRADA",
        "WAIT_CONFIRMATION": "AGUARDAR CONFIRMAÇÃO",
        "NO_SETUP": "SEM SETUP"
    }.get(timing, timing)


def action_pt(decision):
    return {
        "LONG_SETUP": "SETUP LONG",
        "SHORT_SETUP": "SETUP SHORT",
        "ACCUMULATION_WATCH": "OBSERVAR ACUMULAÇÃO",
        "WATCH": "OBSERVAR / NÃO PERSEGUIR PREÇO",
        "NO_TRADE": "NÃO OPERAR"
    }.get(decision, decision)


def visual_score(score):
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


def status_icon(data):
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


# ============================================================
# RESUMO PERIÓDICO
# ============================================================

def get_system_state(key):
    conn = db()

    row = conn.execute(
        "SELECT value FROM system_state WHERE key = ?",
        (key,)
    ).fetchone()

    conn.close()

    return row[0] if row else None


def set_system_state(key, value):
    conn = db()

    conn.execute(
        """
        INSERT OR REPLACE INTO system_state (key, value)
        VALUES (?, ?)
        """,
        (key, value)
    )

    conn.commit()
    conn.close()


def summary_due():
    last = get_system_state("last_summary")

    if not last:
        return True

    try:
        previous = datetime.fromisoformat(last)

        now = datetime.now(timezone.utc)

        elapsed = (
            now - previous
        ).total_seconds()

        hours = CONFIG.get(
            "telegram_summary_hours",
            4
        )

        return elapsed >= hours * 3600

    except Exception:
        return True


def send_summary(results):
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


# ============================================================
# ANALISAR UM ATIVO
# ============================================================

def analyze_asset(asset, market):
    candles_1h = get_candles(
        asset,
        "1h",
        220
    )

    candles_4h = get_candles(
        asset,
        "4h",
        220
    )

    if len(candles_1h) < 60:
        raise RuntimeError(
            f"{asset}: candles 1H insuficientes"
        )

    if len(candles_4h) < 60:
        raise RuntimeError(
            f"{asset}: candles 4H insuficientes"
        )

    closes_1h = [
        c["close"]
        for c in candles_1h
    ]

    closes_4h = [
        c["close"]
        for c in candles_4h
    ]

    price = market["price"]
    funding = market["funding"]
    current_oi = market["oi"]

    ema20_1h = ema(closes_1h[-100:], 20)
    ema50_1h = ema(closes_1h[-150:], 50)

    ema20_4h = ema(closes_4h[-100:], 20)
    ema50_4h = ema(closes_4h[-150:], 50)

    rsi_1h = rsi(closes_1h, 14)
    rsi_4h = rsi(closes_4h, 14)

    atr_1h = atr(candles_1h, 14)

    oi_delta = calculate_oi_delta(
        asset,
        current_oi
    )

    if len(closes_4h) >= 2:
        momentum_4h = (
            (
                closes_4h[-1]
                - closes_4h[-2]
            )
            / closes_4h[-2]
        ) * 100
    else:
        momentum_4h = 0.0

    regime = detect_regime(
        price,
        ema20_4h,
        ema50_4h
    )

    (
        setup_direction,
        trading_score,
        reasons,
        long_score,
        short_score
    ) = calculate_setup(
        price,
        regime,
        ema20_1h,
        ema50_1h,
        rsi_1h,
        funding,
        oi_delta,
        momentum_4h
    )

    confluence = calculate_confluence(
        setup_direction,
        regime,
        price,
        ema20_1h,
        ema50_1h,
        rsi_1h,
        funding,
        oi_delta,
        momentum_4h
    )

    timing = determine_timing(
        setup_direction,
        regime,
        price,
        ema20_1h,
        rsi_1h,
        rsi_4h,
        trading_score,
        confluence
    )

    (
        accumulation_score,
        correction,
        accumulation_reasons
    ) = calculate_accumulation(
        price,
        candles_4h,
        rsi_1h,
        rsi_4h,
        funding,
        oi_delta,
        ema20_1h
    )

    decision = make_decision(
        setup_direction,
        timing,
        trading_score,
        accumulation_score,
        confluence
    )

    thesis, counter_thesis = build_thesis(
        setup_direction,
        regime,
        reasons,
        rsi_1h,
        rsi_4h,
        oi_delta
    )

    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    return {
        "timestamp": timestamp,
        "asset": asset,
        "price": price,
        "funding": funding,
        "oi": current_oi,
        "oi_delta": oi_delta,
        "rsi_1h": rsi_1h,
        "ema20_1h": ema20_1h,
        "ema50_1h": ema50_1h,
        "rsi_4h": rsi_4h,
        "ema20_4h": ema20_4h,
        "ema50_4h": ema50_4h,
        "atr_1h": atr_1h,
        "regime": regime,
        "setup_direction": setup_direction,
        "timing": timing,
        "trading_score": trading_score,
        "accumulation_score": accumulation_score,
        "confluence": confluence,
        "decision": decision,
        "thesis": thesis,
        "counter_thesis": counter_thesis,
        "momentum_4h": momentum_4h,
        "correction": correction,
        "long_score": long_score,
        "short_score": short_score,
        "accumulation_reasons": accumulation_reasons,
        "_candles_1h": candles_1h,
        "_candles_4h": candles_4h
    }


# ============================================================
# TERMINAL
# ============================================================

def print_analysis(data):
    print()
    print("=" * 55)
    print(
        f"{data['asset']} | "
        f"${data['price']:,.2f}"
    )
    print("=" * 55)

    print(
        "TENDÊNCIA:",
        regime_pt(data["regime"])
    )

    print(
        "SETUP:",
        setup_pt(data["setup_direction"])
    )

    print(
        "TIMING:",
        timing_pt(data["timing"])
    )

    print()

    print(
        f"Trading Score: "
        f"{data['trading_score']}/100"
    )

    print(
        f"Accumulation: "
        f"{data['accumulation_score']}/100"
    )

    print(
        f"Confluência: "
        f"{data['confluence']}/6"
    )

    print()

    print(
        f"RSI 1H: {data['rsi_1h']:.1f}"
    )

    print(
        f"RSI 4H: {data['rsi_4h']:.1f}"
    )

    print(
        f"Funding: "
        f"{data['funding'] * 100:+.5f}%"
    )

    print(
        f"OI Δ 5m: "
        f"{data['oi_delta']:+.2f}%"
    )

    print(
        f"Momentum 4H: "
        f"{data['momentum_4h']:+.2f}%"
    )

    print()

    print(
        "DECISÃO: >>>",
        data["decision"],
        "<<<"
    )

    print()

    print("TESE:")
    print(data["thesis"])

    print()

    print("POR QUE NÃO:")
    print(data["counter_thesis"])


# ============================================================
# PAPER POSITION TRACKER 1M
# ============================================================

def update_paper_position_1m(asset):
    position = paper_positions.get_open_position(DB_FILE, asset)
    if not position:
        return []

    # Busca até 24h de candles de 1 minuto.
    # Isso permite recuperar o acompanhamento após suspensão do Android.
    candles = get_candles(asset, "1m", bars=1440)

    opened_ms = int(
        datetime.fromisoformat(position["opened_at"]).timestamp() * 1000
    )
    last_candle_ms = int(position.get("last_candle_ms") or 0)

    minute_ms = 60 * 1000
    now_ms = int(time.time() * 1000)

    results = []

    for candle in sorted(candles, key=lambda x: x["time"]):
        candle_ms = int(candle["time"])

        # Nunca processa candle ainda em formação.
        if candle_ms + minute_ms > now_ms:
            continue

        # A primeira vela parcial da entrada não é utilizada,
        # pois não sabemos se high/low ocorreu antes ou depois da entrada.
        if candle_ms < opened_ms:
            continue

        # Nunca processa duas vezes o mesmo candle.
        if candle_ms <= last_candle_ms:
            continue

        candle_ts = datetime.fromtimestamp(
            candle_ms / 1000,
            tz=timezone.utc
        ).isoformat()

        result = paper_positions.update_position(
            DB_FILE,
            asset,
            candle["close"],
            candle_ts,
            candle_high=candle["high"],
            candle_low=candle["low"],
        )

        conn = sqlite3.connect(DB_FILE)
        conn.execute(
            "UPDATE paper_positions SET last_candle_ms = ? WHERE id = ?",
            (candle_ms, position["id"]),
        )
        conn.commit()
        conn.close()

        if result and result.get("events"):
            results.append(result)

        if result and result.get("status") != "OPEN":
            break

    return results

def update_post_trade_tracking_1m(asset):
    trackers = paper_positions.get_active_post_trade_trackers(
        DB_FILE,
        asset
    )

    if not trackers:
        return

    candles = get_candles(
        asset,
        "1m",
        bars=1440
    )

    minute_ms = 60 * 1000
    now_ms = int(time.time() * 1000)

    for tracker in trackers:
        closed_ms = int(
            datetime.fromisoformat(
                tracker["closed_at"]
            ).timestamp() * 1000
        )

        last_candle_ms = int(
            tracker.get("last_candle_ms") or 0
        )

        for candle in sorted(
            candles,
            key=lambda x: x["time"]
        ):
            candle_ms = int(candle["time"])

            # Nunca usa candle ainda em formação.
            if candle_ms + minute_ms > now_ms:
                continue

            # O candle que causou a saída é ignorado.
            # O acompanhamento começa no candle seguinte.
            if candle_ms < closed_ms + minute_ms:
                continue

            if candle_ms <= last_candle_ms:
                continue

            candle_ts = datetime.fromtimestamp(
                candle_ms / 1000,
                tz=timezone.utc
            ).isoformat()

            paper_positions.update_post_trade_candle(
                DB_FILE,
                tracker["id"],
                candle_ms,
                candle["high"],
                candle["low"],
                candle["close"],
                candle_ts,
            )

            last_candle_ms = candle_ms

            if (
                datetime.fromtimestamp(
                    candle_ms / 1000,
                    tz=timezone.utc
                )
                >=
                datetime.fromisoformat(
                    tracker["closed_at"]
                ) + timedelta(hours=24)
            ):
                break


def run_cycle():
    learning_engine.init_storage(DB_FILE)

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

            # MARKET STRUCTURE — mesmos dados para Oficial/Shadow.
            structure_candles = {
                "4h": market_structure.closed_candles(
                    data.pop("_candles_4h"), "4h"
                ),
                "2h": market_structure.closed_candles(
                    get_candles(asset, "2h", 120), "2h"
                ),
                "1h": market_structure.closed_candles(
                    data.pop("_candles_1h"), "1h"
                ),
                "15m": market_structure.closed_candles(
                    get_candles(asset, "15m", 120), "15m"
                ),
                "5m": market_structure.closed_candles(
                    get_candles(asset, "5m", 120), "5m"
                ),
                "1m": market_structure.closed_candles(
                    get_candles(asset, "1m", 120), "1m"
                ),
            }

            structure_mtf = (
                market_structure.analyze_multitimeframe(
                    structure_candles
                )
            )
            structure_context = (
                market_structure.interpret_multitimeframe(
                    structure_mtf
                )
            )

            data["market_structure"] = {
                "mtf": structure_mtf,
                "interpretation": structure_context,
            }

            # Decide antes de salvar o scan atual.
            alert = should_alert(
                data,
                previous
            )

            # TODO histórico continua completo,
            # mesmo quando Telegram fica silencioso.
            # LONG/SHORT PAPER ENGINE
            leverage_plan = leverage_engine.build_plan(
                data,
                CONFIG,
                TRADING_THRESHOLD,
                MIN_CONFLUENCE
            )

            data["leverage_plan"] = leverage_plan

            # Memória imediata:
            # consulta o último trade comparável ANTES
            # de registrar a decisão atual.
            learning_memory = (
                learning_engine.analyze_recent_memory(
                    DB_FILE,
                    paper_positions.TEST_ERA,
                    data,
                    leverage_plan
                )
            )

            data["learning_memory"] = learning_memory

            # LEARNING ENGINE — snapshot imutável da decisão.
            # Registra o que o agente sabia ANTES do resultado.
            decision_snapshot_id = (
                learning_engine.save_decision_snapshot(
                    DB_FILE,
                    paper_positions.TEST_ERA,
                    data,
                    leverage_plan
                )
            )

            data["decision_snapshot_id"] = (
                decision_snapshot_id
            )

            # STORAGE LONG/SHORT PAPER
            leverage_engine.init_storage(DB_FILE)
            paper_positions.init_storage(DB_FILE)

            # Atualiza primeiro qualquer posição PAPER já aberta
            # usando candles de 1 minuto desde o último acompanhamento.
            position_events = update_paper_position_1m(
                data["asset"]
            )

            update_post_trade_tracking_1m(
                data["asset"]
            )

            # LEARNING — transforma trackers de 24h concluídos
            # em reflexões persistentes e idempotentes.
            completed_reflections = (
                learning_engine.process_completed_post_trade_reflections(
                    DB_FILE,
                    paper_positions.TEST_ERA
                )
            )

            for reflection in completed_reflections:
                print(
                    "🧠 LEARNING 24H:",
                    reflection["asset"],
                    f"posição #{reflection['position_id']}",
                    reflection["observations"]
                )

            for position_event in position_events:
                event_message = paper_positions.format_event(
                    position_event
                )

                if event_message:
                    send_telegram(event_message)
                    print(event_message)

                # LEARNING — fechamento factual da posição.
                # AMBIGUOUS não entra como vitória/derrota normal.
                if position_event.get("status") == "CLOSED":
                    learning_engine.record_learning_event(
                        DB_FILE,
                        paper_positions.TEST_ERA,
                        data["timestamp"],
                        data["asset"],
                        "OFFICIAL_POSITION_CLOSED",
                        position_id=position_event.get("id"),
                        snapshot_id=decision_snapshot_id,
                        details={
                            "side": position_event.get("side"),
                            "exit_reason": position_event.get(
                                "exit_reason"
                            ),
                            "pnl_usd": position_event.get("pnl_usd"),
                            "pnl_r": position_event.get("pnl_r"),
                            "events": position_event.get("events", []),
                        },
                    )

            # SHADOW ENGINE
            # Executa depois da atualização PAPER para enxergar
            # inclusive um STOP ocorrido neste mesmo ciclo.
            shadow_engine.init_storage(DB_FILE)

            shadow_position_event = shadow_engine.update_shadow_position(
                DB_FILE,
                paper_positions.TEST_ERA,
                data["asset"],
                structure_candles["1m"],
                data["timestamp"],
            )

            if (
                shadow_position_event
                and shadow_position_event.get("status") == "CLOSED"
            ):
                learning_engine.record_learning_event(
                    DB_FILE,
                    paper_positions.TEST_ERA,
                    data["timestamp"],
                    data["asset"],
                    "SHADOW_POSITION_CLOSED",
                    position_id=shadow_position_event.get("id"),
                    snapshot_id=decision_snapshot_id,
                    details=shadow_position_event,
                )

            shadow_memory = learning_engine.analyze_recent_memory(
                DB_FILE,
                paper_positions.TEST_ERA,
                data,
                leverage_plan
            )

            previous_entry_snapshot = None

            if shadow_memory.get("previous_position_id") is not None:
                previous_entry_snapshot = (
                    learning_engine.get_position_entry_snapshot(
                        DB_FILE,
                        paper_positions.TEST_ERA,
                        shadow_memory["previous_position_id"]
                    )
                )

            shadow_result = shadow_engine.evaluate_shadow(
                DB_FILE,
                paper_positions.TEST_ERA,
                data,
                leverage_plan,
                decision_snapshot_id,
                shadow_memory,
                previous_entry_snapshot,
                data["market_structure"],
                structure_candles["1m"]
            )

            data["shadow_result"] = shadow_result
            data["shadow_position_event"] = shadow_position_event

            if shadow_result.get("opened_position_id") is not None:
                learning_engine.record_learning_event(
                    DB_FILE,
                    paper_positions.TEST_ERA,
                    data["timestamp"],
                    data["asset"],
                    "SHADOW_POSITION_OPENED",
                    position_id=shadow_result["opened_position_id"],
                    snapshot_id=decision_snapshot_id,
                    details=shadow_result.get("plan"),
                )

            # Registra histórico do motor de sinais.
            new_leverage_signal = leverage_engine.save_cycle(
                DB_FILE,
                data,
                leverage_plan
            )

            # Uma nova posição PAPER só nasce se ainda
            # não existir posição OPEN do mesmo ativo/lado/setup.
            new_position_id = None

            if new_leverage_signal:
                new_position_id = paper_positions.open_position(
                    DB_FILE,
                    data,
                    leverage_plan
                )

                if new_position_id is not None:
                    learning_engine.record_learning_event(
                        DB_FILE,
                        paper_positions.TEST_ERA,
                        data["timestamp"],
                        data["asset"],
                        "OFFICIAL_POSITION_OPENED",
                        position_id=new_position_id,
                        snapshot_id=decision_snapshot_id,
                        details={
                            "side": leverage_plan.get("side"),
                            "setup_type": leverage_plan.get("setup_type"),
                            "entry": leverage_plan.get("entry"),
                            "stop": leverage_plan.get("stop"),
                        },
                    )

            shadow_comparison_id = shadow_engine.record_opening_comparison(
                DB_FILE,
                paper_positions.TEST_ERA,
                data,
                decision_snapshot_id,
                new_position_id,
                shadow_result.get("opened_position_id"),
            )
            data["shadow_comparison_id"] = shadow_comparison_id

            # Telegram de entrada somente quando uma posição
            # PAPER foi realmente aberta.
            new_leverage_signal = (
                new_leverage_signal
                and new_position_id is not None
            )

            data["new_leverage_signal"] = new_leverage_signal

            # TELEGRAM LONG/SHORT PAPER
            if new_leverage_signal:
                leverage_message = (
                    leverage_engine.format_telegram_signal(
                        data,
                        leverage_plan
                    )
                )

                if leverage_message:
                    sent = send_telegram(leverage_message)

                    if sent:
                        print(
                            f"📨 {data['asset']}: sinal "
                            "LONG/SHORT enviado ao Telegram"
                        )
                    else:
                        print(
                            f"⚠️ {data['asset']}: falha ao "
                            "enviar sinal LONG/SHORT"
                        )

            # VISUAL LONG/SHORT PAPER
            print()
            print("========== LONG/SHORT PAPER ==========")

            if leverage_plan["status"] == "PAPER_READY":
                print(
                    f"SINAL: {leverage_plan['side']} "
                    f"— {leverage_plan['setup_type']}"
                )
                print(
                    f"Entrada: ${leverage_plan['entry']:,.2f}"
                )
                print(
                    f"Stop: ${leverage_plan['stop']:,.2f} "
                    f"({leverage_plan['stop_pct']:.2f}%)"
                )
                print(
                    f"Alavancagem PAPER: "
                    f"{leverage_plan['leverage']}x"
                )
                print(
                    f"Risco: ${leverage_plan['risk_usd']:,.2f}"
                )
                print(
                    f"Notional: "
                    f"${leverage_plan['notional_usd']:,.2f}"
                )
                print(
                    f"T1: ${leverage_plan['t1']:,.2f}"
                )
                print(
                    f"T2: ${leverage_plan['t2']:,.2f}"
                )
                print(
                    f"T3: ${leverage_plan['t3']:,.2f}"
                )

            else:
                print("Status: NO_LEVERAGE")

            print(
                f"Tactical Short: "
                f"{leverage_plan['tactical_score']}/100"
            )
            print(
                f"Motivo: {leverage_plan['reason']}"
            )
            print("Trading real: DESATIVADO")
            print("======================================")


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


# ============================================================
# MAIN
# ============================================================

def main():
    global telegram_command_state
    telegram_command_state = {"offset": None}

    init_db()
    leverage_engine.init_storage(DB_FILE)
    paper_positions.init_storage(DB_FILE)

    print()
    print("🤖 CRYPTO MARKET AGENT V0.2")
    print("--------------------------------")
    print(
        "ATIVOS:",
        ", ".join(ASSETS)
    )
    print(
        "SCAN:",
        f"{SCAN_SECONDS}s"
    )
    print(
        "TELEGRAM:",
        "OK"
        if telegram_ready()
        else "NÃO CONFIGURADO"
    )
    print(
        "TRADING REAL: DESATIVADO"
    )
    print("--------------------------------")

    if telegram_ready():
        send_telegram(
            "🤖 Crypto Market Agent — EVENT MODE\n"
            "📊 BTC • ETH • SOL • AAVE • BNB\n"
            "🔎 Varredura contínua a cada 5 minutos\n"
            "🔕 Sem mudança relevante = silêncio\n"
            "🔔 Telegram somente por mudança de cenário\n"
            "🧠 Alertas explicam o motivo da decisão\n"
            "🛡️ Trading real: DESATIVADO"
        )

    while True:
        cycle_start = time.time()

        try:
            run_cycle()

        except KeyboardInterrupt:
            raise

        except Exception as e:
            print(
                "\n❌ ERRO NO CICLO:",
                e
            )

        elapsed = time.time() - cycle_start

        wait = max(
            1,
            SCAN_SECONDS - elapsed
        )

        print()
        print(
            "Próximo scan em aproximadamente "
            f"{int(wait)}s..."
        )

        # Durante a espera, continua ouvindo comandos do Telegram
        # sem alterar a frequência do scanner de mercado.
        wait_until = time.time() + wait

        try:
            while time.time() < wait_until:
                if telegram_ready():
                    telegram_commands.poll_commands(
                        DB_FILE,
                        TELEGRAM_TOKEN,
                        TELEGRAM_CHAT_ID,
                        telegram_command_state,
                    )

                remaining = wait_until - time.time()

                if remaining > 0:
                    time.sleep(min(5, remaining))

        except KeyboardInterrupt:
            print()
            print("Agente encerrado.")
            break


if __name__ == "__main__":
    main()
