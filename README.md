# 🧪 crypto-degen-lab

Una arena de **paper trading** (dinero ficticio) donde varios agentes de Claude cazan memecoins y tokens de alto riesgo en **Solana, Base, BSC y Ethereum**, y compiten entre sí y contra una estrategia sin IA.

> ⚠️ Experimento educativo. No hay claves privadas ni dinero real. Nada de esto es asesoramiento financiero.

## Cómo funciona

Cada 4 horas, GitHub Actions ejecuta un ciclo completo:

1. **Radar**: descubre tokens nuevos y en tendencia en todas las cadenas (DexScreener: boosts y perfiles nuevos; GeckoTerminal: pools trending y recién creados).
2. **Filtros duros**: liquidez y volumen mínimos y edad del token. Después, seguridad: **RugCheck** en Solana y **GoPlus** en EVM, que detectan honeypots, taxes altas, mint o freeze activos, owner oculto…
3. **Equipo de research (Claude)**:
   - 🌍 **Analista macro**: régimen de mercado a partir de BTC/ETH/SOL/BNB, Fear & Greed, el mercado global y lo que es trending.
   - 🔭 **Scout de memecoins**: analiza los candidatos y elige picks con su tesis.
   - 🛡️ **Oficial de riesgo**: el escéptico. Veta picks y avisa sobre las posiciones abiertas.
4. **Gestores de cartera (Claude)**: cada persona decide qué comprar o vender, con memoria de sus últimas operaciones.
5. **Gestor de riesgo determinista**: stop-loss, trailing stop, take-profit parcial, tiempo máximo, tamaño máximo, % máximo de la liquidez del pool y circuit breaker diario. **La IA propone, el código dispone.**
6. **Simulación realista**: impacto de precio AMM según la liquidez, comisiones, slippage/MEV, taxes del token y **rugs** (si retiran la liquidez, la posición vale casi cero).
7. **Panel**: se publica en GitHub Pages (`docs/index.html`).

### La arena

| Cartera | Quién decide | Estilo |
|---|---|---|
| `degen` | Claude | Memecoins agresivas, hasta 8 posiciones |
| `momentum` | Claude | Más selectivo: liquidez seria, memes consolidadas + majors |
| `baseline` | Reglas fijas, **sin IA** | Mismo riesgo que degen. Sirve para ver si Claude aporta algo de verdad |
| `hodl_sol` | Nadie | 100 % SOL, comprar y mantener |

Todas empiezan con 1000 $ ficticios. Puedes cambiar las personas o crear otras en `config.toml`.

## Puesta en marcha (una sola vez)

### 1. Saca el token de tu suscripción de Claude

En tu ordenador (necesitas [Node.js](https://nodejs.org) 18 o superior):

```bash
npm install -g @anthropic-ai/claude-code
claude setup-token
```

Se abre el navegador, inicias sesión con tu cuenta Pro y te da un token largo. **Cópialo y no se lo pases a nadie**: da acceso a tu suscripción.

### 2. Guárdalo como secreto en el repo

En GitHub: **Settings → Secrets and variables → Actions → New repository secret**
- Name: `CLAUDE_CODE_OAUTH_TOKEN`
- Secret: el token

Los secretos están cifrados: nadie los ve, ni siquiera siendo el repo público.

### 3. Activa el panel

**Settings → Pages → Build and deployment → Source: Deploy from a branch → Branch: `main` / carpeta `/docs` → Save.**

En un par de minutos tendrás el panel en `https://<tu-usuario>.github.io/crypto-degen-lab/`.

### 4. Lánzalo

Ve a **Actions → trading-cycle → Run workflow**. A partir de ahí se ejecuta solo cada 4 horas.

## Consumo de tu plan Pro

En modo `full`, cada ciclo hace 3 llamadas de research y 1 por cada persona con IA, o sea **5 llamadas cada 4 horas** (30 al día). Todo cuenta para el mismo límite que usas en claude.ai. Si notas que te quedas sin margen:
- en `config.toml`, pon `mode = "economy"` (3 llamadas por ciclo), o
- usa `model_* = "haiku"` en algunos agentes, o
- en `.github/workflows/trading.yml`, cambia el cron a `17 */8 * * *` (cada 8 horas).

Si llegas al límite, el ciclo no falla: aplica solo las reglas automáticas (stops, etc.) y lo apunta en el diario.

## Ejecutar en local

```bash
pip install -r requirements.txt
python -m bot.run --offline   # mercado sintético + IA simulada, para probar sin internet ni gastar límite
python -m bot.run             # mercado real + Claude (necesitas `claude` con sesión iniciada)
```

Para empezar de cero: borra `state/state.json` y `state/journal.json`.

## Estructura

```
bot/
  sources.py    APIs de mercado y seguridad
  scanner.py    descubrimiento, features, filtros y score heurístico
  agents.py     prompts de los agentes de Claude
  llm.py        backends: Claude Code (suscripción), API o mock
  risk.py       gestor de riesgo determinista
  portfolio.py  cartera simulada con AMM, fees, taxes y rugs
  dashboard.py  panel HTML
  mock.py       mercado sintético para pruebas
  run.py        un ciclo completo
config.toml     toda la configuración
state/          estado de las carteras y diario (lo actualiza el bot)
docs/           panel publicado en GitHub Pages
```

## Ideas para seguir

- Más fuentes: datos de pump.fun, flujos de wallets "smart money", sentimiento en X.
- Backtesting con históricos de GeckoTerminal.
- Una persona que aprenda: que reescriba su estrategia cada semana según sus resultados.
- Alertas en Telegram cuando haya operaciones.
