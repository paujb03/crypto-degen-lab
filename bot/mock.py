"""Offline synthetic market for testing the full pipeline without internet.

Each run advances the simulated clock by 4h. Tokens follow a high-volatility random
walk; some get rugged (liquidity pulled), some are honeypots. State persists in
state/mock_market.json so consecutive offline runs evolve.
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

from .sources import MAJORS

STEP_SECONDS = 4 * 3600
CHAINS = ["solana", "base", "bsc", "ethereum"]
SYLL = ["pep", "bon", "wif", "moo", "dog", "cat", "frog", "zor", "kek", "gig", "chad", "nyan",
        "pump", "moon", "sol", "base", "rekt", "fomo", "yolo", "ape"]


class MockMarket:
    def __init__(self, state_dir: Path, seed: int = 7):
        self.path = state_dir / "mock_market.json"
        if self.path.exists():
            self.s = json.loads(self.path.read_text())
        else:
            self.s = {"t": 1_790_000_000, "step": 0, "tokens": {}, "majors": {
                "bitcoin": 68000.0, "ethereum": 2600.0, "solana": 150.0, "binancecoin": 580.0},
                "seed": seed}
        self.rng = random.Random(self.s["seed"] * 1000 + self.s["step"])
        self._advance()

    def _new_token(self, chain: str) -> dict:
        r = self.rng
        name = (r.choice(SYLL) + r.choice(SYLL)).upper()
        addr = f"{chain[:3]}{r.getrandbits(64):016x}"
        liq = math.exp(r.uniform(math.log(5000), math.log(900000)))
        return {"chain": chain, "address": addr, "symbol": name[:8], "name": name.title() + " Coin",
                "price": 10 ** r.uniform(-7, -2), "liquidity": liq, "supply": 1e9,
                "created": self.s["t"] - r.randint(600, 20 * 86400),
                "drift": r.gauss(0.0, 0.08), "vol": r.uniform(0.15, 0.6),
                "honeypot": r.random() < 0.06, "rug_hazard": r.uniform(0.0, 0.08),
                "danger": r.random() < 0.12, "sell_tax": r.choice([0, 0, 0, 0.02, 0.05, 0.2]),
                "hist": [], "dead": False, "boosted": r.random() < 0.2}

    def _advance(self):
        r = self.rng
        self.s["t"] += STEP_SECONDS
        self.s["step"] += 1
        for k in self.s["majors"]:
            self.s["majors"][k] *= math.exp(r.gauss(0.002, 0.025))
        for tok in self.s["tokens"].values():
            if tok["dead"]:
                continue
            ret = r.gauss(tok["drift"], tok["vol"])
            tok["hist"] = (tok["hist"] + [ret])[-6:]
            tok["price"] *= math.exp(ret)
            tok["liquidity"] *= math.exp(ret * 0.6)
            tok["drift"] = 0.7 * tok["drift"] + r.gauss(0, 0.05)
            if r.random() < tok["rug_hazard"]:
                tok["dead"] = True
                tok["liquidity"] = r.uniform(10, 400)
                tok["price"] *= 0.02
        for _ in range(r.randint(6, 12)):
            t = self._new_token(r.choice(CHAINS))
            self.s["tokens"][f"{t['chain']}:{t['address']}"] = t
        # forget very old dead tokens to keep the file small
        if len(self.s["tokens"]) > 400:
            items = sorted(self.s["tokens"].items(), key=lambda kv: kv[1]["created"])
            self.s["tokens"] = dict(items[-400:])

    def save(self):
        self.path.write_text(json.dumps(self.s))

    def now(self) -> float:
        return float(self.s["t"])

    # ---- same interface as LiveMarket ----
    def discover(self, chains):
        out = {}
        live = [t for t in self.s["tokens"].values() if not t["dead"] and t["chain"] in chains]
        self.rng.shuffle(live)
        for t in live[:60]:
            tags = {"gt_trending"} if t["hist"] and t["hist"][-1] > 0.1 else {"gt_new"}
            if t["boosted"]:
                tags.add("ds_boost")
            out[(t["chain"], t["address"])] = tags
        return out

    def _pair(self, t) -> dict:
        r = self.rng
        h = t["hist"] or [0.0]
        ch = lambda n: (math.exp(sum(h[-n:])) - 1) * 100
        vol24 = t["liquidity"] * r.uniform(0.3, 6.0) * (0.05 if t["dead"] else 1)
        buys = int(vol24 / 300 * r.uniform(0.6, 1.4))
        sells = int(buys * r.uniform(0.5, 1.3))
        mcap = t["price"] * t["supply"]
        return {"chain": t["chain"], "address": t["address"], "symbol": t["symbol"], "name": t["name"],
                "pair_address": "pair" + t["address"], "dex": "mockswap", "price": t["price"],
                "liquidity": t["liquidity"], "fdv": mcap, "mcap": mcap,
                "volume": {"m5": vol24 / 288, "h1": vol24 / 24, "h6": vol24 / 4, "h24": vol24},
                "change": {"m5": ch(1) / 48, "h1": ch(1) / 4, "h6": ch(2), "h24": ch(6)},
                "txns": {"m5": {"buys": buys // 288, "sells": sells // 288},
                         "h1": {"buys": buys // 24, "sells": sells // 24},
                         "h24": {"buys": buys, "sells": sells}},
                "created_ms": t["created"] * 1000, "url": None, "boosts": int(t["boosted"]),
                "has_socials": r.random() < 0.6, "has_website": r.random() < 0.4}

    def pairs(self, chain, addresses):
        out = {}
        for a in addresses:
            t = self.s["tokens"].get(f"{chain}:{a}")
            if t and t["liquidity"] > 50:
                out[a] = self._pair(t)
        return out

    def majors(self):
        out = {}
        for k in MAJORS:
            p = self.s["majors"][k]
            out[k] = {"symbol": {"bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL",
                                 "binancecoin": "BNB"}[k],
                      "price": p, "mcap": p * 1e7,
                      "change": {"h1": self.rng.gauss(0, 0.5), "h24": self.rng.gauss(0, 3),
                                 "d7": self.rng.gauss(0, 8)}}
        return out

    def context(self):
        v = self.rng.randint(15, 85)
        return {"fear_greed": [{"value": v, "label": "Greed" if v > 55 else "Fear" if v < 45 else "Neutral"}],
                "global": {"total_mcap_usd": 2.4e12, "mcap_change_24h_pct": self.rng.gauss(0, 2),
                           "btc_dominance": 56.0},
                "cg_trending": [{"symbol": "MOCK", "name": "Mock", "rank": 300}]}

    def security(self, chain, address):
        t = self.s["tokens"].get(f"{chain}:{address}")
        if not t:
            return {"checked": False, "honeypot": False, "buy_tax": 0, "sell_tax": 0,
                    "score": None, "flags": [], "danger": []}
        flags, danger = [], []
        if t["danger"]:
            flags.append("danger: Mint Authority still enabled")
            danger.append("Mint Authority still enabled")
        return {"checked": True, "honeypot": t["honeypot"], "buy_tax": 0.0,
                "sell_tax": t["sell_tax"], "score": self.rng.randint(1, 90) if chain == "solana" else None,
                "flags": flags, "danger": danger}
