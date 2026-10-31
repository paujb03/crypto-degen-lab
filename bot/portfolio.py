"""Paper portfolio with realistic execution: AMM price impact, fees, taxes, rugs."""
from __future__ import annotations

from datetime import datetime, timezone


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def new_portfolio(persona: dict, cash: float, now: float) -> dict:
    return {"name": persona["name"], "kind": persona["kind"], "description": persona.get("description", ""),
            "cash": cash, "start_cash": cash, "created": now, "positions": {}, "trades": [],
            "equity_curve": [{"t": now, "equity": cash}], "realized_pnl": 0.0,
            "wins": 0, "losses": 0, "rugs": 0, "halted_until": 0}


def position_value(pos: dict) -> float:
    """Exit value: for DEX tokens, account for price impact against current liquidity
    (a 'pumped' token in a dead pool is worth almost nothing)."""
    gross = pos["qty"] * pos["last_price"]
    if pos["kind"] == "major":
        return gross
    reserve = max(pos.get("last_liquidity", 0.0) / 2, 1.0)
    return gross * reserve / (reserve + gross)


def equity(pf: dict) -> float:
    return pf["cash"] + sum(position_value(p) for p in pf["positions"].values())


def _record(pf: dict, trade: dict):
    pf["trades"].append(trade)
    pf["trades"] = pf["trades"][-400:]


def buy(pf: dict, key: str, meta: dict, usd: float, sim: dict, now: float, reason: str,
        buy_tax: float = 0.0, sell_tax: float = 0.0, thesis: str = "") -> dict | None:
    price, liq = meta["price"], meta.get("liquidity", 0.0)
    is_major = meta.get("kind") == "major"
    usd = min(usd, pf["cash"])
    if usd < 5 or price <= 0:
        return None
    if is_major:
        spend = usd * (1 - sim["major_fee"])
        avg_price = price
        cost_fees = usd - spend
    else:
        spend = usd - sim["network_fee_usd"]
        spend *= (1 - sim["dex_fee"] - sim["extra_slippage"]) * (1 - buy_tax)
        reserve = max(liq / 2, 1.0)
        avg_price = price * (1 + spend / reserve)
        cost_fees = usd - spend
    qty = spend / avg_price
    pf["cash"] -= usd
    pos = pf["positions"].get(key)
    if pos:
        total_cost = pos["cost_usd"] + usd
        pos["qty"] += qty
        pos["cost_usd"] = total_cost
        pos["entry_price"] = total_cost / pos["qty"]
    else:
        pf["positions"][key] = pos = {
            "key": key, "symbol": meta.get("symbol", key), "chain": meta.get("chain", "cex"),
            "kind": "major" if is_major else "dex", "qty": qty, "cost_usd": usd,
            "entry_price": usd / qty, "peak_price": price, "last_price": price,
            "entry_liquidity": liq, "last_liquidity": liq, "opened_at": now,
            "tp_done": False, "sell_tax": sell_tax, "thesis": thesis[:300], "url": meta.get("url")}
    trade = {"t": now, "time": iso(now), "side": "BUY", "key": key, "symbol": pos["symbol"],
             "chain": pos["chain"], "usd": round(usd, 2), "price": price,
             "impact_pct": round((avg_price / price - 1) * 100, 2), "fees_usd": round(cost_fees, 2),
             "reason": reason[:300]}
    _record(pf, trade)
    return trade


def sell(pf: dict, key: str, fraction: float, sim: dict, now: float, reason: str) -> dict | None:
    pos = pf["positions"].get(key)
    if not pos:
        return None
    fraction = max(0.0, min(1.0, fraction))
    qty = pos["qty"] * fraction
    price, liq = pos["last_price"], pos.get("last_liquidity", 0.0)
    gross = qty * price
    rugged = False
    if pos["kind"] == "major":
        proceeds = gross * (1 - sim["major_fee"])
    else:
        if liq < sim["rug_liquidity_usd"]:
            rugged = True
        reserve = max(liq / 2, 1.0)
        proceeds = gross * reserve / (reserve + gross)
        proceeds *= (1 - sim["dex_fee"] - sim["extra_slippage"]) * (1 - pos.get("sell_tax", 0.0))
        proceeds = max(0.0, proceeds - sim["network_fee_usd"])
    cost_part = pos["cost_usd"] * fraction
    pnl = proceeds - cost_part
    pf["cash"] += proceeds
    pf["realized_pnl"] += pnl
    pos["qty"] -= qty
    pos["cost_usd"] -= cost_part
    closed = fraction >= 0.999 or pos["qty"] * price < 0.5
    if closed:
        del pf["positions"][key]
        if rugged:
            pf["rugs"] += 1
        if pnl >= 0:
            pf["wins"] += 1
        else:
            pf["losses"] += 1
    trade = {"t": now, "time": iso(now), "side": "SELL", "key": key, "symbol": pos["symbol"],
             "chain": pos["chain"], "usd": round(proceeds, 2), "price": price,
             "fraction": round(fraction, 3), "pnl_usd": round(pnl, 2),
             "pnl_pct": round(pnl / cost_part * 100, 1) if cost_part else 0.0,
             "rug": rugged, "closed": closed, "reason": reason[:300]}
    _record(pf, trade)
    return trade


def mark(pf: dict, quotes: dict[str, dict]):
    """Update last price/liquidity. Missing DEX quote => treat as dead pool."""
    for key, pos in pf["positions"].items():
        q = quotes.get(key)
        if q is None:
            if pos["kind"] == "dex":
                pos["missing"] = pos.get("missing", 0) + 1
                if pos["missing"] >= 2:   # vanished twice in a row: pool gone
                    pos["last_liquidity"] = 0.0
                    pos["last_price"] *= 0.01
            continue
        pos["missing"] = 0
        pos["last_price"] = q["price"]
        pos["last_liquidity"] = q.get("liquidity", pos.get("last_liquidity", 0.0))
        pos["peak_price"] = max(pos["peak_price"], q["price"])
        pos["change"] = q.get("change")


def snapshot(pf: dict, now: float):
    pf["equity_curve"].append({"t": now, "equity": round(equity(pf), 2)})
    pf["equity_curve"] = pf["equity_curve"][-2000:]


def summary(pf: dict) -> dict:
    eq = equity(pf)
    closed = pf["wins"] + pf["losses"]
    curve = [p["equity"] for p in pf["equity_curve"]] or [pf["start_cash"]]
    peak, mdd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1 if peak else 0)
    return {"name": pf["name"], "kind": pf["kind"], "equity": round(eq, 2), "cash": round(pf["cash"], 2),
            "return_pct": round((eq / pf["start_cash"] - 1) * 100, 2),
            "realized_pnl": round(pf["realized_pnl"], 2), "open_positions": len(pf["positions"]),
            "closed_trades": closed, "win_rate": round(pf["wins"] / closed * 100, 1) if closed else None,
            "rugs": pf["rugs"], "max_drawdown_pct": round(mdd * 100, 2)}
