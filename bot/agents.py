"""Claude agents: market analyst, memecoin scout, risk officer, portfolio managers."""
from __future__ import annotations

import json
import random

from .llm import LLM

ANALYST = """Eres el ANALISTA MACRO de un fondo cripto experimental.
Con los datos de majors (BTC, ETH, SOL, BNB), el índice Fear & Greed, el mercado global y lo que es trending,
determina el RÉGIMEN de mercado y cuánto apetito por memecoins tiene sentido ahora.
Tu régimen anterior se incluye para que mantengas coherencia (cámbialo solo si los datos lo justifican).

Devuelve JSON:
{"regime": "risk_on" | "neutral" | "risk_off",
 "confidence": 0-100,
 "meme_appetite": 0.0-1.0,
 "summary": "2-3 frases sobre qué está pasando",
 "narratives": ["narrativas o sectores calientes que se intuyen"],
 "watch": ["riesgos a vigilar"]}

DATOS:
"""

SCOUT = """Eres el SCOUT DE MEMECOINS / TOKENS DE ALTO RIESGO de un fondo cripto experimental.
Tienes candidatos de varias cadenas (Solana, Base, BSC, Ethereum) que YA han pasado filtros de liquidez,
volumen y seguridad básica (honeypot, taxes, RugCheck/GoPlus). Analiza cada uno con ojo de trader on-chain:
- momentum real vs pump artificial (ratio compras/ventas, nº de transacciones, volumen/liquidez)
- fase del ciclo de vida (recién lanzado, primera subida, consolidación, distribución, muerto)
- relación market cap / liquidez (muy alta = fácil de hundir)
- señales de riesgo de los flags de seguridad
- si el pump de 24h ya pasó (perseguir velas verdes enormes suele acabar mal)

Elige como mucho 8 picks con convicción y explica la tesis. Marca también los que evitarías.

Devuelve JSON:
{"market_read": "lectura general del mercado de memes en 1-2 frases",
 "picks": [{"key": "...", "conviction": 0-100, "thesis": "...", "red_flags": "...", "horizon_hours": n}],
 "avoid": [{"key": "...", "why": "..."}]}

RÉGIMEN Y DATOS:
"""

RISK = """Eres el OFICIAL DE RIESGO de un fondo cripto experimental. Tu trabajo es ser el escéptico.
Revisa los picks del scout y las posiciones abiertas de todas las carteras. Puedes VETAR picks
(p. ej. patrón de rug, concentración, liquidez demasiado fina para el tamaño, pump ya agotado,
demasiada exposición a una misma cadena o narrativa) y emitir alertas sobre posiciones abiertas.
Las reglas duras (stop-loss, trailing, tamaño máximo) ya se aplican automáticamente: céntrate en el juicio.

Devuelve JSON:
{"vetoes": [{"key": "...", "why": "..."}],
 "position_alerts": [{"key": "...", "action": "exit" | "reduce" | "hold", "why": "..."}],
 "max_new_risk": 0.0-1.0,
 "notes": "1-2 frases"}

DATOS:
"""

RESEARCH = """Eres el EQUIPO DE RESEARCH (analista macro + scout de memecoins + oficial de riesgo) de un fondo
cripto experimental. En una sola respuesta:
1) determina el régimen de mercado con majors, Fear & Greed y contexto global;
2) analiza los candidatos (ya filtrados por liquidez/seguridad) y elige como mucho 8 picks con tesis;
3) actúa de escéptico: veta picks peligrosos y emite alertas sobre posiciones abiertas.

Devuelve JSON:
{"regime": "risk_on" | "neutral" | "risk_off", "confidence": 0-100, "meme_appetite": 0.0-1.0,
 "summary": "...", "narratives": ["..."], "watch": ["..."],
 "market_read": "...",
 "picks": [{"key": "...", "conviction": 0-100, "thesis": "...", "red_flags": "...", "horizon_hours": n}],
 "avoid": [{"key": "...", "why": "..."}],
 "vetoes": [{"key": "...", "why": "..."}],
 "position_alerts": [{"key": "...", "action": "exit" | "reduce" | "hold", "why": "..."}],
 "max_new_risk": 0.0-1.0, "notes": "..."}

DATOS:
"""

MANAGER = """Eres el GESTOR DE CARTERA "{name}" en una arena de paper trading cripto donde compites contra
otras estrategias (incluida una sin IA). Tu estilo: {description}

Recibes: tu cartera actual, el informe del equipo de research (régimen, picks del scout, vetos y alertas
del oficial de riesgo), los candidatos permitidos y tus últimas operaciones cerradas (aprende de ellas).
Reglas:
- Solo puedes comprar claves (key) que aparezcan en "allowed_buys". No compres nada vetado.
- size_pct es el % OBJETIVO del equity total para esa posición (máximo {max_pct:.0%}).
- Para vender usa fraction (0-1) sobre la posición actual.
- El gestor de riesgo recortará tamaños si violan límites (cash mínimo, % de liquidez del pool, nº posiciones).
- No estás obligado a operar: "hold" también es una decisión. Pero recuerda que compites por rentabilidad.

Devuelve JSON:
{{"decisions": [{{"action": "buy" | "sell", "key": "...", "size_pct": 0.0-{max_pct}, "fraction": 0.0-1.0,
                 "reason": "..."}}],
 "commentary": "2-3 frases sobre tu plan y cómo ves tu cartera"}}

DATOS:
"""


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


class Team:
    def __init__(self, llm: LLM, cfg: dict):
        self.llm = llm
        self.cfg = cfg

    def research(self, majors, context, candidates, positions, prev_regime) -> dict:
        c = self.cfg
        base = {"majors": majors, "context": context, "previous_regime": prev_regime}
        if c.get("mode") == "economy":
            payload = {**base, "candidates": candidates, "open_positions": positions}
            return self.llm.ask(RESEARCH + _dump(payload), c["model_scout"], "research", payload)

        regime = self.llm.ask(ANALYST + _dump(base), c["model_analyst"], "analyst", base)
        p2 = {"regime": regime, "candidates": candidates}
        scout = self.llm.ask(SCOUT + _dump(p2), c["model_scout"], "scout", p2)
        by_key = {x["key"]: x for x in candidates}
        picks_data = [dict(pk, data=by_key.get(pk.get("key"))) for pk in scout.get("picks", [])]
        p3 = {"regime": regime, "scout_picks": picks_data, "open_positions": positions}
        risk = self.llm.ask(RISK + _dump(p3), c["model_risk"], "risk", p3)
        return {**regime, **scout, **risk}

    def manage(self, persona: dict, payload: dict) -> dict:
        prompt = MANAGER.format(name=persona["name"], description=persona.get("description", ""),
                                max_pct=persona["max_position_pct"]) + _dump(payload)
        return self.llm.ask(prompt, self.cfg["model_manager"], "manager:" + persona["name"], payload)


# ---------------------------------------------------------------- mock brain (offline tests)
def mock_brain(agent: str, payload: dict) -> dict:
    rnd = random.Random(len(_dump(payload)))
    if agent == "analyst" or agent == "research":
        fg = (payload.get("context", {}).get("fear_greed") or [{"value": 50}])[0]["value"]
        regime = "risk_on" if fg > 60 else "risk_off" if fg < 35 else "neutral"
        out = {"regime": regime, "confidence": 60, "meme_appetite": fg / 100,
               "summary": f"[mock] Fear&Greed en {fg}, régimen {regime}.", "narratives": ["mock"], "watch": []}
        if agent == "analyst":
            return out
    if agent in ("scout", "research"):
        cands = sorted(payload.get("candidates", []), key=lambda c: -c["heuristic_score"])
        picks = [{"key": c["key"], "conviction": int(c["heuristic_score"]), "thesis": "[mock] momentum",
                  "red_flags": "", "horizon_hours": 24} for c in cands[:6]]
        sc = {"market_read": "[mock] mercado de memes activo", "picks": picks, "avoid": []}
        if agent == "scout":
            return sc
        out.update(sc)
    if agent in ("risk", "research"):
        picks = payload.get("scout_picks") or out.get("picks", [])
        vetoes = [{"key": p["key"], "why": "[mock] veto aleatorio"} for p in picks if rnd.random() < 0.2]
        rk = {"vetoes": vetoes, "position_alerts": [], "max_new_risk": 0.7, "notes": "[mock]"}
        if agent == "risk":
            return rk
        out.update(rk)
        return out
    if agent.startswith("manager:"):
        decisions = []
        for pos in payload.get("portfolio", {}).get("positions", []):
            if pos["pnl_pct"] < -15 and rnd.random() < 0.5:
                decisions.append({"action": "sell", "key": pos["key"], "fraction": 1.0,
                                  "reason": "[mock] no funciona"})
        for k in payload.get("allowed_buys", [])[:3]:
            decisions.append({"action": "buy", "key": k, "size_pct": 0.08, "reason": "[mock] pick del scout"})
        return {"decisions": decisions, "commentary": "[mock] siguiendo al scout."}
    return {}
