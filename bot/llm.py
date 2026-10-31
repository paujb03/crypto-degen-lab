"""LLM backends.

claude_cli    -> `claude -p` (Claude Code, authenticated with your Pro/Max subscription
                 through CLAUDE_CODE_OAUTH_TOKEN, generated with `claude setup-token`)
anthropic_api -> Messages API with ANTHROPIC_API_KEY (pay per use)
mock          -> deterministic fake answers for offline tests
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import requests

API_MODELS = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}

SYSTEM = ("Eres un agente de un laboratorio de PAPER TRADING cripto (dinero ficticio, experimento). "
          "No uses herramientas ni busques en internet: trabaja solo con los datos que se te dan. "
          "Responde EXCLUSIVAMENTE con un objeto JSON válido, sin texto antes ni después, sin markdown.")


class LLMError(RuntimeError):
    pass


def extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    depth = 0
    for i in range(start, len(text)) if start >= 0 else []:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    break
    raise LLMError("respuesta sin JSON válido: " + text[:200])


class LLM:
    def __init__(self, cfg: dict, mock_fn=None):
        self.backend = cfg["backend"]
        self.timeout = cfg.get("timeout_seconds", 300)
        self.mock_fn = mock_fn
        self.calls = 0
        if self.backend == "claude_cli" and not shutil.which("claude"):
            raise LLMError("no se encuentra el comando `claude` (instala @anthropic-ai/claude-code)")

    def ask(self, prompt: str, model: str = "sonnet", agent: str = "", payload: dict | None = None) -> dict:
        self.calls += 1
        if self.backend == "mock":
            return self.mock_fn(agent, payload or {})
        if self.backend == "claude_cli":
            text = self._cli(prompt, model)
        elif self.backend == "anthropic_api":
            text = self._api(prompt, model)
        else:
            raise LLMError(f"backend desconocido: {self.backend}")
        return extract_json(text)

    def _cli(self, prompt: str, model: str) -> str:
        cmd = ["claude", "-p", "--output-format", "text", "--model", model,
               "--append-system-prompt", SYSTEM, "--tools", ""]
        try:
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                               timeout=self.timeout, env=os.environ.copy())
        except subprocess.TimeoutExpired as e:
            raise LLMError("timeout de claude") from e
        if r.returncode != 0:
            raise LLMError(f"claude salió con código {r.returncode}: {(r.stderr or r.stdout)[:300]}")
        return r.stdout

    def _api(self, prompt: str, model: str) -> str:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMError("falta ANTHROPIC_API_KEY")
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=self.timeout, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": API_MODELS.get(model, model), "max_tokens": 4000, "system": SYSTEM,
                  "messages": [{"role": "user", "content": prompt}]})
        if r.status_code != 200:
            raise LLMError(f"API {r.status_code}: {r.text[:300]}")
        return "".join(b.get("text", "") for b in r.json().get("content", []))
