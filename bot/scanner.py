"""Candidate discovery, feature engineering, hard filters and heuristic pre-score."""
from __future__ import annotations

import math


def features(p: dict, now: float, tags: set[str]) -> dict:
    age_h = max(0.0, (now * 1000 - p["created_ms"]) / 3.6e6) if p["created_ms"] else None
    liq = p["liquidity"] or 1.0
    h1 = p["txns"]["h1"]
    h24 = p["txns"]["h24"]
    return {
        "age_hours": round(age_h, 2) if age_h is not None else None,
        "vol_liq_ratio_24h": round(p["volume"]["h24"] / liq, 2),
        "vol_liq_ratio_1h": round(p["volume"]["h1"] / liq, 3),
        "buy_sell_ratio_1h": round((h1["buys"] + 1) / (h1["sells"] + 1), 2),
        "buy_sell_ratio_24h": round((h24["buys"] + 1) / (h24["sells"] + 1), 2),
        "txns_24h": h24["buys"] + h24["sells"],
        "mcap_liq_ratio": round((p["mcap"] or p["fdv"]) / liq, 1) if (p["mcap"] or p["fdv"]) else None,
        "sources": sorted(tags),
    }


def heuristic_score(p: dict, f: dict) -> float:
    """Crude momentum/quality score in [0, 100]. Used to rank before AI and by the baseline."""
    s = 50.0
    s += max(-15, min(15, p["change"]["h1"] / 4))
    s += max(-10, min(15, p["change"]["h6"] / 8))
    s += max(-10, min(10, (f["buy_sell_ratio_1h"] - 1) * 12))
    s += max(0, min(12, math.log10(max(f["vol_liq_ratio_24h"], 0.01)) * 8 + 4))
    s += max(0, min(8, math.log10(max(p["liquidity"], 1)) * 2 - 6))
    if f["age_hours"] is not None and f["age_hours"] < 2:
        s -= 5   # extremely young: lottery
    if p["change"]["h24"] > 800:
        s -= 10  # probably topped
    if p.get("boosts"):
        s += 2
    if p.get("has_socials"):
        s += 2
    if len(f["sources"]) > 1:
        s += 3
    return round(max(0.0, min(100.0, s)), 1)


def hard_filter(p: dict, f: dict, cfg: dict) -> str | None:
    """Return a rejection reason or None."""
    if p["price"] <= 0:
        return "sin precio"
    if p["liquidity"] < cfg["min_liquidity_usd"]:
        return f"liquidez {p['liquidity']:.0f}$ < mínimo"
    if p["volume"]["h24"] < cfg["min_volume_h24_usd"]:
        return "volumen bajo"
    if f["age_hours"] is not None:
        if f["age_hours"] * 60 < cfg["min_age_minutes"]:
            return "demasiado nuevo"
        if f["age_hours"] > cfg["max_age_days"] * 24:
            return "demasiado viejo para memes"
    return None


def security_filter(sec: dict, cfg: dict) -> str | None:
    if sec.get("honeypot"):
        return "HONEYPOT"
    if sec.get("sell_tax", 0) > cfg["max_sell_tax"]:
        return f"sell tax {sec['sell_tax']:.0%}"
    if sec.get("buy_tax", 0) > cfg["max_buy_tax"]:
        return f"buy tax {sec['buy_tax']:.0%}"
    if sec.get("danger"):
        return "peligro: " + ", ".join(sec["danger"][:2])
    if sec.get("score") is not None and sec["score"] > cfg["max_rugcheck_score"]:
        return f"rugcheck score {sec['score']:.0f}"
    return None


def scan(market, cfg: dict, log: list[str]) -> tuple[list[dict], dict]:
    """Return (candidates sorted by heuristic score, stats)."""
    gen, flt = cfg["general"], cfg["filters"]
    now = market.now()
    found = market.discover(gen["chains"])
    stats = {"discovered": len(found), "rejected": {}, "passed_basic": 0,
             "security_checked": 0, "passed_security": 0}

    by_chain: dict[str, list[str]] = {}
    for chain, addr in found:
        by_chain.setdefault(chain, []).append(addr)

    pre: list[dict] = []
    for chain, addrs in by_chain.items():
        pairs = market.pairs(chain, addrs)
        for addr, p in pairs.items():
            f = features(p, now, found.get((chain, addr), set()))
            reason = hard_filter(p, f, flt)
            if reason:
                stats["rejected"][reason.split(" ")[0]] = stats["rejected"].get(reason.split(" ")[0], 0) + 1
                continue
            pre.append({"key": f"{chain}:{addr}", "pair": p, "features": f,
                        "score": heuristic_score(p, f)})
    stats["passed_basic"] = len(pre)
    pre.sort(key=lambda c: c["score"], reverse=True)

    out = []
    for c in pre[: gen["max_security_checks"]]:
        p = c["pair"]
        sec = market.security(p["chain"], p["address"])
        stats["security_checked"] += 1
        c["security"] = sec
        reason = security_filter(sec, flt)
        if reason:
            key = reason.split(":")[0].split(" ")[0]
            stats["rejected"][key] = stats["rejected"].get(key, 0) + 1
            log.append(f"rechazado {p['symbol']} ({p['chain']}): {reason}")
            continue
        out.append(c)
    stats["passed_security"] = len(out)
    return out, stats


def compact(c: dict) -> dict:
    """Compact representation for LLM prompts."""
    p, f, sec = c["pair"], c["features"], c.get("security", {})
    return {
        "key": c["key"], "symbol": p["symbol"], "name": p["name"], "chain": p["chain"], "dex": p["dex"],
        "price": p["price"], "liquidity_usd": round(p["liquidity"]), "mcap_usd": round(p["mcap"] or p["fdv"]),
        "volume_usd": {k: round(v) for k, v in p["volume"].items()},
        "price_change_pct": {k: round(v, 1) for k, v in p["change"].items()},
        "txns_h1": p["txns"]["h1"], "txns_h24": p["txns"]["h24"],
        **f, "heuristic_score": c["score"],
        "security_flags": sec.get("flags", [])[:6], "rugcheck_score": sec.get("score"),
        "socials": p.get("has_socials"), "website": p.get("has_website"),
    }
