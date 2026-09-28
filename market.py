import requests

HL_URL = "https://api.hyperliquid.xyz/info"

def hl_post(payload):
    r = requests.post(HL_URL, json=payload, timeout=15)
    r.raise_for_status()
    return r.json()

def get_market(asset):
    meta, contexts = hl_post({"type": "metaAndAssetCtxs"})

    for i, coin in enumerate(meta["universe"]):
        if coin["name"] == asset:
            ctx = contexts[i]

            return {
                "asset": asset,
                "mark_price": float(ctx["markPx"]),
                "funding": float(ctx["funding"]),
                "open_interest": float(ctx["openInterest"]),
                "volume_24h": float(ctx["dayNtlVlm"])
            }

    raise ValueError(f"{asset} não encontrado")

if __name__ == "__main__":
    for asset in ["BTC", "ETH", "SOL"]:
        try:
            data = get_market(asset)

            print("\n------------------------------")
            print(f"ATIVO: {data['asset']}")
            print(f"PREÇO: ${data['mark_price']:,.2f}")
            print(f"FUNDING: {data['funding']:.6%}")
            print(f"OPEN INTEREST: {data['open_interest']:,.2f}")
            print(f"VOLUME 24H: ${data['volume_24h']:,.2f}")

        except Exception as e:
            print(f"\nERRO {asset}: {e}")
