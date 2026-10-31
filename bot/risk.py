"""Deterministic risk manager. The AI proposes; this module disposes.

Nothing the LLM says can bypass these rules.
"""
from __future__ import annotations

from .portfolio import equity, sell


def automatic_exits(pf: dict, persona: dict, sim: dict, now: float) -> list[dict]:
    trades = []
    for key, pos in list(pf["positions"].items()):
        if pos["kind"] == "dex" and pos.get("last_liquidity", 0) < sim["rug_liquidity_usd"]:
            t = sell(pf, key, 1.0, sim, now, "RUG: liquidez retirada del pool, venta forzada")
            if t:
                trades.append(t)
            continue
        ret = pos["last_price"] / pos["entry_price"] - 1 if pos["entry_price"] else 0
        from_peak = pos["last_price"] / pos["peak_price"] - 1 if pos["peak_price"] else 0
        held_h = (now - pos["opened_at"]) / 3600
        reason, frac = None, 1.0
        if ret <= persona["stop_loss"]:
            reason = f"STOP-LOSS {ret:+.0%}"
        elif from_peak <= persona["trailing_stop"] and pos["peak_price"] > pos["entry_price"] * 1.15:
            reason = f"TRAILING STOP {from_peak:+.0%} desde máximo"
        elif not pos["tp_done"] and ret >= persona["take_profit"]:
            reason, frac = f"TAKE-PROFIT parcial {ret:+.0%}", persona["take_profit_fraction"]
            pos["tp_done"] = True
        elif held_h >= persona["max_hold_hours"]:
            reason = f"TIEMPO MÁXIMO ({held_h:.0f}h)"
        if reason:
            t = sell(pf, key, frac, sim, now, reason)
            if t:
                trades.append(t)
    return trades


def check_halt(pf: dict, persona: dict, now: float) -> bool:
    """Circuit breaker: if equity fell more than X% in the last 24h, no new buys for 24h."""
    if pf.get("halted_until", 0) > now:
        return True
    curve = pf["equity_curve"]
    past = [p for p in curve if now - p["t"] >= 20 * 3600]
    ref = past[-1]["equity"] if past else curve[0]["equity"]
    if ref and equity(pf) / ref - 1 <= persona["daily_drawdown_halt"]:
        pf["halted_until"] = now + 24 * 3600
        return True
    return False


def size_buy(pf: dict, persona: dict, meta: dict, requested_pct: float) -> tuple[float, str]:
    """Return (usd, note). usd == 0 means rejected."""
    eq = equity(pf)
    key = meta["key"]
    universe = persona.get("universe", "memes")
    is_major = meta.get("kind") == "major"
    if universe == "memes" and is_major:
        return 0, "esta persona no opera majors"
    if universe == "majors" and not is_major:
        return 0, "esta persona solo opera majors"
    if key not in pf["positions"] and len(pf["positions"]) >= persona["max_positions"]:
        return 0, "máximo de posiciones alcanzado"
    if not is_major and meta.get("liquidity", 0) < persona.get("min_liquidity_usd", 0):
        return 0, "liquidez por debajo del mínimo de la persona"
    pct = max(0.0, min(requested_pct, persona["max_position_pct"]))
    current = 0.0
    if key in pf["positions"]:
        p = pf["positions"][key]
        current = p["qty"] * p["last_price"]
    usd = pct * eq - current
    notes = []
    available = pf["cash"] - persona["min_cash_pct"] * eq
    if usd > available:
        usd = available
        notes.append("limitado por reserva de cash")
    if not is_major:
        cap = persona["max_pool_share"] * meta.get("liquidity", 0)
        if usd > cap:
            usd = cap
            notes.append(f"limitado al {persona['max_pool_share']:.0%} de la liquidez")
    if usd < 10:
        return 0, "tamaño demasiado pequeño tras límites (" + ", ".join(notes or ["sin cash"]) + ")"
    return usd, ", ".join(notes)
