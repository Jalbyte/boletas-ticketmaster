# Monitor de boletas BTS (Ticketmaster Colombia)

Avisa por Telegram si la página del evento pasa de "agotado" a "boletas
disponibles" (chequeo cada 5 min vía GitHub Actions, gratis), con un
comando `/estado` para preguntarle al bot en cualquier momento, y un
dashboard web (`dashboard/`) para ver el historial de chequeos. **No
compra boletas automáticamente** — solo detecta y avisa; la compra la
haces tú, manualmente, apenas llegue la alerta.

## 1. Crear el bot de Telegram

1. En Telegram, habla con **@BotFather** y crea un bot con `/newbot`.
2. Copia el token que te da (`TELEGRAM_BOT_TOKEN`).
3. Envíale cualquier mensaje a tu bot recién creado (para que tenga un chat contigo).
4. Abre en el navegador `https://api.telegram.org/bot<TOKEN>/getUpdates` y busca
   `"chat":{"id": ...}` — ese número es tu `TELEGRAM_CHAT_ID`.

## 2. Configurar variables de entorno

```bash
cp .env.example .env
# edita .env con tu TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID
```

## 3. Probar localmente (recomendado antes de desplegar)

```bash
pip install -r requirements.txt
playwright install chromium

python monitor.py --once
```

Esto corre **una sola verificación** y termina. Revisa la carpeta
`debug_snapshots/` si el estado detectado fue `blocked_unknown` o `unknown`:
ahí queda el HTML completo de esa corrida para poder afinar las frases de
detección en `monitor.py` (listas `SOLD_OUT_PATTERNS`, `AVAILABLE_PATTERNS`,
`BLOCKED_PATTERNS`) según lo que realmente muestre la página.

Para probar que la alerta de Telegram funciona, edita manualmente
`state.json` dejando `"last_status": "sold_out"` y vuelve a correr
`python monitor.py --once` en un momento en que la página muestre boletas
disponibles (o edita temporalmente `AVAILABLE_PATTERNS`/el HTML de prueba).

## 4. Desplegar 24/7 gratis con GitHub Actions

El monitor corre en `.github/workflows/monitor.yml`, con un cron cada 5
minutos (el mínimo que permite GitHub Actions) — 100% gratis, sin tarjeta.

1. Sube este repo a GitHub (privado, recomendado).
2. En **Settings → Secrets and variables → Actions → Secrets**, agrega:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID` (uno o varios separados por coma)
3. En **Settings → Secrets and variables → Actions → Variables**, agrega
   `EVENT_URL` con la URL del evento (opcional; si no la pones, usa el
   valor por defecto ya hardcodeado en `monitor.py`).
4. Listo — el workflow corre solo. Puedes dispararlo manualmente desde la
   pestaña **Actions** con "Run workflow" para probarlo de inmediato.

Cada corrida además publica su resultado como un
["commit status"](https://docs.github.com/rest/commits/statuses) de GitHub
(contexto `ticket-monitor`) — esto es lo que lee el dashboard (ver abajo).

## 5. Dashboard en vivo (Netlify o Vercel)

`dashboard/index.html` es una página estática (sin backend) que lee el
historial de chequeos directamente de la API de GitHub y lo muestra en una
tabla, con un login simple (correo/contraseña hardcodeados en el HTML —
**no es seguridad real**, es solo para que no cualquiera con el link entre).

Necesita un **token de GitHub de solo lectura**, con permiso **"Commit
statuses: Read-only"** sobre este repo únicamente
(créalo en https://github.com/settings/personal-access-tokens/new). El
token **no se sube a git** — `dashboard/index.html` tiene un placeholder
(`%%GH_TOKEN%%`) que se reemplaza en tiempo de build leyendo una variable
de entorno `GH_TOKEN` que configuras en el proveedor de despliegue.

**Desplegar en Netlify:**
1. "Add new site" → "Import an existing project" → conecta este repo.
2. En **Site configuration → Environment variables**, agrega `GH_TOKEN`
   con el valor del token.
3. Ya incluye `netlify.toml` (publica `dashboard/` y hace el reemplazo del
   token en el build) — no hace falta configurar nada más.

**Desplegar en Vercel:**
1. "Add New… → Project" → importa este repo.
2. En "Root Directory" selecciona `dashboard`.
3. Framework preset: "Other".
4. Build command: `sed -i "s#%%GH_TOKEN%%#$GH_TOKEN#" index.html`
5. En **Settings → Environment Variables**, agrega `GH_TOKEN` con el valor
   del token.

Si alguna vez regeneras el token, solo actualiza la variable de entorno en
Netlify/Vercel y vuelve a desplegar — no hay que tocar el código.

## Notas importantes

- El sitio de Ticketmaster tiene protección anti-bot activa. Es normal que
  algunos ciclos salgan como `blocked_unknown` — el monitor simplemente
  reintenta más tarde y **no** interpreta un bloqueo como "agotado" ni como
  "disponible".
- El intervalo de sondeo (por defecto ~4-5.5 min con jitter) es intencional:
  busca ser razonable y no generar tráfico agresivo contra el sitio.
- Este proyecto no incluye ni incluirá automatización de compra (saltarse
  colas, resolver CAPTCHAs, etc.) porque eso viola los términos de servicio
  de Ticketmaster.
