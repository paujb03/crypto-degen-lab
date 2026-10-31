"""One trading cycle. Run with:  python -m bot.run   (add --offline to use the synthetic market)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tomllib
import traceback
from pathlib import Path

from . import http
from .agents import Team, mock_brain
from .dashboard import build_dashboard
from .llm import LLM, LLMError
from .portfolio import buy, equity, iso, mark, new_portfolio, position_value, sell, snapshot, summary
from .risk import automatic_exits, check_halt, size_buy
from .scanner import compact, scan
from .sources import LiveMarket

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
DOCS = ROOT / "docs"


def load_json(p: Path, default):
    return json.loads(p.read_text()) if p.exists() else default


def major_key(cid: str) -> str:
    return f"major:{cid}"


def pos_view(pos: dict, now: float) -> dict:
    v = position_value(pos)
    return {"key": pos["key"], "symbol": pos["symbol"], "chain": pos["chain"], "value_usd": round(v, 2),
            "cost_usd": round(pos["cost_usd"], 2),
            "pnl_pct": round((v / pos["cost_usd"] - 1) * 100, 1) if pos["cost_usd"] else 0,
            "held_hours": round((now - pos["opened_at"]) / 3600, 1),
            "from_peak_pct": round((pos["last_price"] / pos["peak_price"] - 1) * 100, 1),
            "liquidity_usd": round(pos.get("last_liquidity", 0)), "take_profit_done": pos["tp_done"],
            "price_change_pct": pos.get("change"), "thesis": pos.get("thesis", "")}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="mercado sintético + IA simulada")
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    args = ap.parse_args(argv)
    offline = args.offline or os.environ.get("OFFLINE") == "1"

    cfg = tomllib.loads(Path(args.config).read_text())
    if os.environ.get("LLM_MODE"):
        cfg["llm"]["mode"] = os.environ["LLM_MODE"]
    sim, gen = cfg["simulation"], cfg["general"]
    personas = cfg["persona"]
    STATE.mkdir(exist_ok=True)
    DOCS.mkdir(exist_ok=True)

    if offline:
        from .mock import MockMarket
        market = MockMarket(STATE)
        cfg["llm"]["backend"] = "mock"
    else:
        market = LiveMarket()

    state = load_json(STATE / "state.json", {"portfolios": {}, "meta": {}})
    journal = load_json(STATE / "journal.json", [])
    now = market.now()
    log: list[str] = []
    entry = {"t": now, "time": iso(now), "log": log, "personas": {}, "auto_trades": []}
    t0 = time.time()

    # ---------------------------------------------------------------- init portfolios
    for p in personas:
        if p["name"] not in state["portfolios"]:
            state["portfolios"][p["name"]] = new_portfolio(p, gen["starting_cash_usd"], now)
            log.append(f"nueva cartera '{p['name']}' con {gen['starting_cash_usd']:.0f}$")
        state["portfolios"][p["name"]]["description"] = p.get("description", "")
    pfs = state["portfolios"]

    # ---------------------------------------------------------------- market data
    majors = market.majors()
    context = market.context()
    major_meta = {major_key(k): {"key": major_key(k), "kind": "major", "symbol": v["symbol"], "chain": "cex",
                                 "price": v["price"], "liquidity": 1e12} for k, v in majors.items()}

    # quotes for held positions
    quotes: dict[str, dict] = {k: {"price": m["price"], "liquidity": m["liquidity"],
                                   "change": majors[k.split(":", 1)[1]]["change"]}
                               for k, m in major_meta.items()}
    held_by_chain: dict[str, set[str]] = {}
    for pf in pfs.values():
        for key, pos in pf["positions"].items():
            if pos["kind"] == "dex":
                chain, addr = key.split(":", 1)
                held_by_chain.setdefault(chain, set()).add(addr)
    for chain, addrs in held_by_chain.items():
        for addr, pair in market.pairs(chain, sorted(addrs)).items():
            quotes[f"{chain}:{addr}"] = pair
    for pf in pfs.values():
        mark(pf, quotes)

    # ---------------------------------------------------------------- hodl + automatic exits
    pmap = {p["name"]: p for p in personas}
    for name, pf in pfs.items():
        persona = pmap.get(name)
        if not persona:
            continue
        if persona["kind"] == "hodl":
            k = major_key(persona["asset"])
            if not pf["positions"] and k in major_meta and pf["cash"] > 10:
                buy(pf, k, major_meta[k], pf["cash"], sim, now, "compra inicial buy & hold")
            continue
        for t in automatic_exits(pf, persona, sim, now):
            t["portfolio"] = name
            entry["auto_trades"].append(t)

    # ---------------------------------------------------------------- scanner
    candidates, stats = scan(market, cfg, log)
    entry["scan"] = stats
    cand_by_key = {c["key"]: c for c in candidates}
    cand_compact = [compact(c) for c in candidates[: gen["max_candidates_for_ai"]]]
    meta = dict(major_meta)
    for c in candidates:
        p = c["pair"]
        meta[c["key"]] = {"key": c["key"], "kind": "dex", "symbol": p["symbol"], "chain": p["chain"],
                          "price": p["price"], "liquidity": p["liquidity"], "url": p.get("url"),
                          "buy_tax": c["security"].get("buy_tax", 0), "sell_tax": c["security"].get("sell_tax", 0)}
    entry["candidates"] = [{"key": c["key"], "symbol": c["pair"]["symbol"], "chain": c["pair"]["chain"],
                            "score": c["score"], "liquidity": round(c["pair"]["liquidity"]),
                            "change_h1": round(c["pair"]["change"]["h1"], 1),
                            "change_h24": round(c["pair"]["change"]["h24"], 1),
                            "url": c["pair"].get("url")} for c in candidates[:25]]

    # ---------------------------------------------------------------- research team
    research = None
    llm = None
    if any(p["kind"] == "llm" for p in personas):
        try:
            llm = LLM(cfg["llm"], mock_fn=mock_brain)
            team = Team(llm, cfg["llm"])
            all_positions = [dict(pos_view(pos, now), portfolio=n)
                             for n, pf in pfs.items() for pos in pf["positions"].values()]
            research = team.research(majors, context, cand_compact, all_positions,
                                     state["meta"].get("regime"))
            state["meta"]["regime"] = {k: research.get(k) for k in ("regime", "confidence", "summary")}
        except (LLMError, Exception) as e:  # noqa: BLE001 - never let the AI break the cycle
            log.append(f"research no disponible: {e}")
            traceback.print_exc()
    entry["research"] = research

    vetoed = {v.get("key") for v in (research or {}).get("vetoes", [])}

    # ---------------------------------------------------------------- personas decide
    for name, pf in pfs.items():
        persona = pmap.get(name)
        if not persona or persona["kind"] == "hodl":
            continue
        pe = {"decisions": [], "executed": [], "rejected": [], "commentary": None}
        entry["personas"][name] = pe
        halted = check_halt(pf, persona, now)
        if halted:
            pe["rejected"].append({"key": "*", "why": "circuit breaker: drawdown diario excedido, sin compras 24h"})

        universe = persona.get("universe", "memes")
        allowed = [k for k in cand_by_key if k not in vetoed
                   and meta[k]["liquidity"] >= persona.get("min_liquidity_usd", 0)]
        if universe in ("all", "majors"):
            allowed += list(major_meta)
        if universe == "majors":
            allowed = list(major_meta)

        decisions = []
        if persona["kind"] == "llm":
            if research is None:
                pe["commentary"] = "IA no disponible este ciclo: solo reglas automáticas."
                continue
            own_keys = set(pf["positions"])
            picks = [pk for pk in research.get("picks", []) if pk.get("key") not in vetoed]
            payload = {
                "portfolio": {"equity_usd": round(equity(pf), 2), "cash_usd": round(pf["cash"], 2),
                              "positions": [pos_view(p, now) for p in pf["positions"].values()],
                              "summary": summary(pf), "halted_for_new_buys": halted},
                "limits": {k: persona[k] for k in ("max_positions", "max_position_pct", "min_cash_pct",
                                                   "stop_loss", "trailing_stop", "take_profit")},
                "research": {k: research.get(k) for k in ("regime", "confidence", "meme_appetite", "summary",
                                                          "narratives", "watch", "market_read",
                                                          "max_new_risk", "notes")},
                "scout_picks": picks,
                "vetoed": list(vetoed),
                "risk_alerts_on_my_positions": [a for a in research.get("position_alerts", [])
                                                if a.get("key") in own_keys],
                "allowed_buys": [] if halted else allowed,
                "candidate_data": [c for c in cand_compact if c["key"] in allowed],
                "majors": majors if universe != "memes" else None,
                "my_recent_closed_trades": [t for t in pf["trades"] if t["side"] == "SELL"][-8:],
                "my_last_commentary": pf.get("last_commentary"),
            }
            try:
                out = Team(llm, cfg["llm"]).manage(persona, payload)
                decisions = out.get("decisions", []) or []
                pe["commentary"] = out.get("commentary")
                pf["last_commentary"] = pe["commentary"]
            except Exception as e:  # noqa: BLE001
                pe["commentary"] = f"error del gestor: {e}"
                log.append(f"{name}: {e}")
        elif persona["kind"] == "rules":
            free = persona["max_positions"] - len(pf["positions"])
            for k in sorted([k for k in allowed if k in cand_by_key],
                            key=lambda k: -cand_by_key[k]["score"]):
                if free <= 0 or halted:
                    break
                if k in pf["positions"] or cand_by_key[k]["score"] < 62:
                    continue
                decisions.append({"action": "buy", "key": k, "size_pct": persona["max_position_pct"],
                                  "reason": f"heurística {cand_by_key[k]['score']}"})
                free -= 1
            pe["commentary"] = f"Reglas fijas: {len(decisions)} compras por puntuación heurística ≥ 62."

        pe["decisions"] = decisions
        # sells first, then buys
        for d in sorted(decisions, key=lambda d: 0 if d.get("action") == "sell" else 1):
            k, action = d.get("key"), d.get("action")
            if action == "sell":
                if k not in pf["positions"]:
                    pe["rejected"].append({"key": k, "why": "no hay posición"})
                    continue
                t = sell(pf, k, float(d.get("fraction", 1.0) or 1.0), sim, now, d.get("reason", ""))
                if t:
                    pe["executed"].append(t)
            elif action == "buy":
                if halted:
                    pe["rejected"].append({"key": k, "why": "circuit breaker"})
                    continue
                if k not in allowed or k not in meta:
                    pe["rejected"].append({"key": k, "why": "no está en la lista permitida (o vetado)"})
                    continue
                usd, note = size_buy(pf, persona, meta[k], float(d.get("size_pct", 0) or 0))
                if usd <= 0:
                    pe["rejected"].append({"key": k, "why": note})
                    continue
                m = meta[k]
                t = buy(pf, k, m, usd, sim, now, d.get("reason", ""), m.get("buy_tax", 0),
                        m.get("sell_tax", 0), thesis=d.get("reason", ""))
                if t:
                    if note:
                        t["note"] = note
                    pe["executed"].append(t)

    # ---------------------------------------------------------------- persist
    for pf in pfs.values():
        snapshot(pf, now)
    entry["summaries"] = {n: summary(pf) for n, pf in pfs.items()}
    entry["llm_calls"] = llm.calls if llm else 0
    entry["http_errors"] = http.errors[-20:]
    entry["duration_s"] = round(time.time() - t0, 1)
    journal.append(entry)
    journal = journal[-gen["journal_keep"]:]
    state["meta"]["last_run"] = now
    state["meta"]["cycles"] = state["meta"].get("cycles", 0) + 1

    (STATE / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1, default=str))
    (STATE / "journal.json").write_text(json.dumps(journal, ensure_ascii=False, indent=1, default=str))
    if offline:
        market.save()
    build_dashboard(state, journal, cfg, DOCS)

    print(f"ciclo {state['meta']['cycles']} @ {iso(now)} | candidatos {stats['passed_security']}/"
          f"{stats['discovered']} | llamadas IA {entry['llm_calls']} | {entry['duration_s']}s")
    for n, s in entry["summaries"].items():
        print(f"  {n:10s} equity {s['equity']:>9.2f}$  ({s['return_pct']:+.1f}%)  posiciones {s['open_positions']}")
    for line in log[-10:]:
        print("  ·", line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
