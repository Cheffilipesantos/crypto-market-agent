from leverage_engine import build_plan

CONFIG = {
    "paper_balance": 10000,
    "leverage_risk_pct": 0.01,
    "leverage_safety_factor": 2.0,
    "leverage_choices": [3, 5, 10],
}


def base():
    return {
        "price": 100.0,
        "ema20_1h": 99.0,
        "ema50_1h": 97.0,
        "rsi_1h": 55.0,
        "rsi_4h": 55.0,
        "momentum_4h": 1.0,
        "oi_delta": 0.5,
        "funding": 0.00001,
        "atr_1h": 2.0,
        "regime": "UPTREND",
        "setup_direction": "BULLISH",
        "timing": "ENTRY_ZONE",
        "trading_score": 80,
        "confluence": 5,
    }


# 1 — LONG DE TENDÊNCIA
long = base()
p_long = build_plan(long, CONFIG)

assert p_long["status"] == "PAPER_READY"
assert p_long["side"] == "LONG"
assert p_long["setup_type"] == "TREND_LONG"
assert p_long["stop"] < p_long["entry"]
assert p_long["t1"] > p_long["entry"]
assert p_long["t2"] > p_long["t1"]
assert p_long["t3"] > p_long["t2"]


# 2 — SHORT DE TENDÊNCIA
short = base()
short.update({
    "ema20_1h": 101.0,
    "ema50_1h": 103.0,
    "rsi_1h": 45.0,
    "momentum_4h": -1.0,
    "regime": "DOWNTREND",
    "setup_direction": "BEARISH",
})

p_short = build_plan(short, CONFIG)

assert p_short["status"] == "PAPER_READY"
assert p_short["side"] == "SHORT"
assert p_short["setup_type"] == "TREND_SHORT"
assert p_short["stop"] > p_short["entry"]
assert p_short["t1"] < p_short["entry"]
assert p_short["t2"] < p_short["t1"]
assert p_short["t3"] < p_short["t2"]


# 3 — SHORT TÁTICO
tactical = base()
tactical.update({
    "price": 100.0,
    "ema20_1h": 101.0,
    "ema50_1h": 102.0,
    "rsi_1h": 44.0,
    "rsi_4h": 55.0,
    "momentum_4h": -1.2,
    "oi_delta": 1.0,
    "funding": 0.0001,
    "regime": "UPTREND",
    "setup_direction": "NEUTRAL",
    "timing": "WAIT_CONFIRMATION",
    "trading_score": 45,
    "confluence": 2,
})

p_tactical = build_plan(tactical, CONFIG)

assert p_tactical["status"] == "PAPER_READY"
assert p_tactical["side"] == "SHORT"
assert p_tactical["setup_type"] == "TACTICAL_SHORT"
assert p_tactical["tactical_score"] >= 70
assert p_tactical["leverage"] <= 5
expected_notional = 30.0 * p_tactical["leverage"]
expected_risk = expected_notional * (p_tactical["stop_pct"] / 100)

assert abs(p_tactical["notional_usd"] - expected_notional) < 1e-9
assert abs(p_tactical["risk_usd"] - expected_risk) < 1e-9
assert p_tactical["risk_usd"] > 0


# 4 — SEM OPERAÇÃO
nothing = base()
nothing.update({
    "setup_direction": "NEUTRAL",
    "timing": "NO_SETUP",
    "trading_score": 30,
    "confluence": 1,
    "momentum_4h": 0.1,
    "oi_delta": 0.0,
    "funding": 0.0,
})

p_none = build_plan(nothing, CONFIG)

assert p_none["status"] == "NO_LEVERAGE"
assert p_none["side"] == "NONE"


def show(name, p):
    print()
    print("==========", name, "==========")
    print("STATUS:", p["status"])
    print("TIPO:", p["setup_type"])
    print("LADO:", p["side"])
    print("ENTRADA:", round(p["entry"], 4))
    print("STOP:", round(p["stop"], 4))
    print("STOP %:", round(p["stop_pct"], 2))
    print("ALAVANCAGEM:", str(p["leverage"]) + "x")
    print("CAP MODELO:", str(p["model_leverage_cap"]) + "x")
    print("RISCO $:", round(p["risk_usd"], 2))
    print("NOTIONAL $:", round(p["notional_usd"], 2))
    print("T1:", round(p["t1"], 4))
    print("T2:", round(p["t2"], 4))
    print("T3:", round(p["t3"], 4))
    print("TACTICAL SCORE:", p["tactical_score"])
    print("MOTIVO:", p["reason"])


show("LONG TREND", p_long)
show("SHORT TREND", p_short)
show("TACTICAL SHORT", p_tactical)
show("NO TRADE", p_none)

print()
print("======================================")
print("TODOS OS TESTES PASSARAM")
print("LONG TREND .......... OK")
print("SHORT TREND ......... OK")
print("TACTICAL SHORT ...... OK")
print("NO TRADE ............ OK")
print("TRADING REAL ........ DESATIVADO")
print("======================================")
