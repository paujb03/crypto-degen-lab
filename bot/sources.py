"""Market data sources (all free, no API keys).

- DexScreener: token discovery (boosts / new profiles) + unified pair metrics on every chain
- GeckoTerminal: trending and brand-new pools per network
- CoinGecko: majors, global market, trending coins
- alternative.me: Fear & Greed index
- RugCheck (Solana) / GoPlus (EVM): token security
"""
from __future__ import annotations

import time

from .http import get_json

DS = "https://api.dexscreener.com"
GT = "https://api.geckoterminal.com/api/v2"
CG = "https://api.coingecko.com/api/v3"

GT_NETWORK = {"solana": "solana", "base": "base", "bsc": "bsc", "ethereum": "eth"}
GOPLUS_CHAIN = {"ethereum": "1", "bsc": "56", "base": "8453"}
MAJORS = ["bitcoin", "ethereum", "solana", "binancecoin"]


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def normalize_ds_pair(p: dict) -> dict:
    vol = p.get("volume") or {}
    chg = p.get("priceChange") or {}
    tx = p.get("txns") or {}
    base = p.get("baseToken") or {}
    return {
        "chain": p.get("chainId"),
        "address": base.get("address"),
        "symbol": base.get("symbol", "?"),
        "name": base.get("name", "?"),
        "pair_address": p.get("pairAddress"),
        "dex": p.get("dexId"),
        "price": _f(p.get("priceUsd")),
        "liquidity": _f((p.get("liquidity") or {}).get("usd")),
        "fdv": _f(p.get("fdv")),
        "mcap": _f(p.get("marketCap")),
        "volume": {k: _f(vol.get(k)) for k in ("m5", "h1", "h6", "h24")},
        "change": {k: _f(chg.get(k)) for k in ("m5", "h1", "h6", "h24")},
        "txns": {k: {"buys": int(_f((tx.get(k) or {}).get("buys"))),
                     "sells": int(_f((tx.get(k) or {}).get("sells")))} for k in ("m5", "h1", "h24")},
        "created_ms": int(_f(p.get("pairCreatedAt"))),
        "url": p.get("url"),
        "boosts": int(_f((p.get("boosts") or {}).get("active"))),
        "has_socials": bool((p.get("info") or {}).get("socials")),
        "has_website": bool((p.get("info") or {}).get("websites")),
    }


class LiveMarket:
    """Real data from public APIs."""

    def now(self) -> float:
        return time.time()

    # ---------- discovery ----------
    def discover(self, chains: list[str]) -> dict[tuple[str, str], set[str]]:
        found: dict[tuple[str, str], set[str]] = {}

        def add(chain, addr, tag):
            if chain in chains and addr:
                found.setdefault((chain, addr), set()).add(tag)

        for path, tag in (("/token-boosts/top/v1", "ds_top_boost"),
                          ("/token-boosts/latest/v1", "ds_boost"),
                          ("/token-profiles/latest/v1", "ds_new_profile")):
            data = get_json(DS + path)
            for item in data or []:
                add(item.get("chainId"), item.get("tokenAddress"), tag)

        for chain in chains:
            net = GT_NETWORK.get(chain)
            if not net:
                continue
            for kind in ("trending_pools", "new_pools"):
                data = get_json(f"{GT}/networks/{net}/{kind}", params={"page": 1})
                for pool in (data or {}).get("data", []):
                    rel = ((pool.get("relationships") or {}).get("base_token") or {}).get("data") or {}
                    tid = rel.get("id", "")
                    addr = tid.split("_", 1)[1] if "_" in tid else None
                    add(chain, addr, "gt_" + kind.replace("_pools", ""))
        return found

    # ---------- pair metrics ----------
    def pairs(self, chain: str, addresses: list[str]) -> dict[str, dict]:
        """Best (highest-liquidity) pair per token address."""
        out: dict[str, dict] = {}
        for i in range(0, len(addresses), 30):
            chunk = addresses[i:i + 30]
            data = get_json(f"{DS}/tokens/v1/{chain}/{','.join(chunk)}")
            for p in data or []:
                n = normalize_ds_pair(p)
                addr = n["address"]
                if not addr:
                    continue
                # DexScreener may return pairs where our token is quote; key by base only
                key = next((a for a in chunk if a.lower() == addr.lower()), None)
                if key is None:
                    continue
                if key not in out or n["liquidity"] > out[key]["liquidity"]:
                    out[key] = n
        return out

    # ---------- majors & context ----------
    def majors(self) -> dict[str, dict]:
        data = get_json(f"{CG}/coins/markets", params={
            "vs_currency": "usd", "ids": ",".join(MAJORS),
            "price_change_percentage": "1h,24h,7d"})
        out = {}
        for c in data or []:
            out[c["id"]] = {
                "symbol": c.get("symbol", "").upper(),
                "price": _f(c.get("current_price")),
                "mcap": _f(c.get("market_cap")),
                "change": {
                    "h1": _f(c.get("price_change_percentage_1h_in_currency")),
                    "h24": _f(c.get("price_change_percentage_24h_in_currency")),
                    "d7": _f(c.get("price_change_percentage_7d_in_currency")),
                },
            }
        return out

    def context(self) -> dict:
        ctx: dict = {}
        fng = get_json("https://api.alternative.me/fng/", params={"limit": 7})
        if fng and fng.get("data"):
            ctx["fear_greed"] = [{"value": int(d["value"]), "label": d["value_classification"]}
                                 for d in fng["data"]]
        glob = get_json(f"{CG}/global")
        if glob and glob.get("data"):
            g = glob["data"]
            ctx["global"] = {
                "total_mcap_usd": _f((g.get("total_market_cap") or {}).get("usd")),
                "mcap_change_24h_pct": _f(g.get("market_cap_change_percentage_24h_usd")),
                "btc_dominance": _f((g.get("market_cap_percentage") or {}).get("btc")),
            }
        tr = get_json(f"{CG}/search/trending")
        if tr:
            ctx["cg_trending"] = [
                {"symbol": c["item"].get("symbol"), "name": c["item"].get("name"),
                 "rank": c["item"].get("market_cap_rank")}
                for c in tr.get("coins", [])[:12]]
        return ctx

    # ---------- security ----------
    def security(self, chain: str, address: str) -> dict:
        """Normalized: {checked, honeypot, buy_tax, sell_tax, score, flags[]}"""
        res = {"checked": False, "honeypot": False, "buy_tax": 0.0, "sell_tax": 0.0,
               "score": None, "flags": [], "danger": []}
        if chain == "solana":
            d = get_json(f"https://api.rugcheck.xyz/v1/tokens/{address}/report/summary")
            if d:
                res["checked"] = True
                res["score"] = _f(d.get("score_normalised"), None)
                for r in d.get("risks") or []:
                    label = r.get("name", "?")
                    res["flags"].append(f"{r.get('level', '?')}: {label}")
                    if r.get("level") == "danger":
                        res["danger"].append(label)
        elif chain in GOPLUS_CHAIN:
            d = get_json(f"https://api.gopluslabs.io/api/v1/token_security/{GOPLUS_CHAIN[chain]}",
                         params={"contract_addresses": address})
            info = ((d or {}).get("result") or {}).get(address.lower())
            if info:
                res["checked"] = True
                res["honeypot"] = info.get("is_honeypot") == "1"
                res["buy_tax"] = _f(info.get("buy_tax"))
                res["sell_tax"] = _f(info.get("sell_tax"))
                for k, label in (("cannot_sell_all", "no se puede vender todo"),
                                 ("is_mintable", "mint activo"),
                                 ("owner_change_balance", "owner puede cambiar balances"),
                                 ("hidden_owner", "owner oculto"),
                                 ("is_blacklisted", "tiene blacklist"),
                                 ("transfer_pausable", "transferencias pausables"),
                                 ("is_proxy", "contrato proxy")):
                    if info.get(k) == "1":
                        res["flags"].append(label)
                if info.get("cannot_sell_all") == "1" or info.get("owner_change_balance") == "1":
                    res["danger"].append("venta restringida")
                if info.get("hidden_owner") == "1":
                    res["danger"].append("owner oculto")
                res["holders"] = int(_f(info.get("holder_count")))
        return res
