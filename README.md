# Monitor de boletas BTS (Ticketmaster Colombia)

Avisa por Telegram si la página del evento pasa de "agotado" a "boletas
disponibles". **No compra boletas automáticamente** — solo detecta y avisa;
la compra la haces tú, manualmente, apenas llegue la alerta.

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

## 4. Desplegar 24/7 en la nube (capa gratuita)

Recomendado: **Railway** o **Fly.io** (soportan un worker Docker
persistente corriendo todo el tiempo, a diferencia de un cron con ventanas
de 5+ minutos).

1. Sube este proyecto a un repositorio Git (privado si prefieres).
2. Crea un nuevo servicio en Railway/Fly.io apuntando al repo (usarán el
   `Dockerfile` automáticamente).
3. Configura las variables de entorno del servicio (las mismas de `.env`).
4. Despliega. El proceso queda corriendo el loop de `monitor.py` de forma
   continua.

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
