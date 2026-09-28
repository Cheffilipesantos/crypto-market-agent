import os
import json
import time
import sqlite3
from datetime import datetime, timezone

import requests


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
        "1h": 60 * 60 * 1000,
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


def should_alert(data):
    previous = get_alert_state(data["asset"])

    # Primeiro scan apenas cria estado.
    # Não manda 5 mensagens de uma vez.
    if previous is None:
        return False

    (
        old_decision,
        old_direction,
        old_timing,
        old_score_bucket,
        old_acc_bucket
    ) = previous

    new_score_bucket = score_bucket(
        data["trading_score"]
    )

    new_acc_bucket = accumulation_bucket(
        data["accumulation_score"]
    )

    # Mudança para setup acionável
    if (
        data["decision"]
        in (
            "LONG_SETUP",
            "SHORT_SETUP",
            "ACCUMULATION_WATCH"
        )
        and data["decision"] != old_decision
    ):
        return True

    # Entrada ficou pronta
    if (
        data["timing"] == "ENTRY_ZONE"
        and old_timing != "ENTRY_ZONE"
    ):
        return True

    # Score cruza 70
    if (
        new_score_bucket >= 70
        and old_score_bucket < 70
    ):
        return True

    # Acumulação cruza 70
    if (
        new_acc_bucket >= 70
        and old_acc_bucket < 70
    ):
        return True

    # Mudança de direção relevante
    if (
        data["setup_direction"]
        != old_direction
        and data["setup_direction"]
        != "NEUTRAL"
        and data["trading_score"] >= 50
    ):
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
        "accumulation_reasons": accumulation_reasons
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
# CICLO
# ============================================================

def run_cycle():
    print()
    print(
        datetime.now(timezone.utc).isoformat()
    )

    print(
        "Obtendo dados de mercado..."
    )

    markets = get_market_contexts()

    results = []

    for asset in ASSETS:
        try:
            if asset not in markets:
                print(
                    f"⚠️ {asset}: "
                    "não encontrado na Hyperliquid"
                )
                continue

            data = analyze_asset(
                asset,
                markets[asset]
            )

            # IMPORTANTE:
            # alertamos comparando com estado anterior
            # ANTES de atualizar o estado.
            alert = should_alert(data)

            save_scan(data)

            print_analysis(data)

            if alert:
                ok = send_telegram(
                    format_telegram(data)
                )

                if ok:
                    print(
                        f"📨 Telegram: "
                        f"alerta enviado para {asset}"
                    )

            update_alert_state(data)

            results.append(data)

        except Exception as e:
            print(
                f"\n❌ ERRO {asset}: {e}"
            )

    if summary_due():
        send_summary(results)

    return results


# ============================================================
# MAIN
# ============================================================

def main():
    init_db()

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
            "🤖 Crypto Market Agent V0.2 iniciado\n"
            "📊 BTC • ETH • SOL • AAVE • BNB\n"
            "⏱ Scan a cada 5 minutos\n"
            "🔔 Alertas somente em mudanças relevantes\n"
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

        try:
            time.sleep(wait)

        except KeyboardInterrupt:
            print()
            print("Agente encerrado.")
            break


if __name__ == "__main__":
    main()
