"""Escucha mensajes de Telegram y responde /estado con el último estado conocido."""
import logging
from datetime import datetime, timezone
from typing import Optional

import requests

from notifier import send_telegram_message

logger = logging.getLogger("telegram_bot")

TELEGRAM_API_BASE = "https://api.telegram.org"

STATUS_LABELS = {
    "available": "🎟️ ¡Boletas posiblemente DISPONIBLES!",
    "sold_out": "😴 Agotado / sin boletas por ahora.",
    "blocked_unknown": "⚠️ Ticketmaster bloqueó la última verificación (estado real desconocido).",
    "unknown": "❓ La última verificación no reconoció el estado de la página.",
    None: "🤷 Todavía no se ha hecho ninguna verificación.",
}

STATUS_COMMAND_PREFIXES = ("/estado", "/status", "estado", "status")


def _humanize_elapsed(iso_ts: str) -> str:
    if not iso_ts:
        return "nunca"
    dt = datetime.fromisoformat(iso_ts)
    elapsed = datetime.now(timezone.utc) - dt
    minutes = int(elapsed.total_seconds() // 60)
    if minutes < 1:
        return "hace unos segundos"
    if minutes < 60:
        return f"hace {minutes} min"
    hours, mins = divmod(minutes, 60)
    return f"hace {hours} h {mins} min"


def build_status_message(state: dict, event_url: str) -> str:
    last_confirmed = state.get("last_confirmed_status")
    last_check_status = state.get("last_check_status")
    last_check_iso = state.get("last_check_iso")
    last_confirmed_iso = state.get("last_confirmed_iso")

    lines = [
        STATUS_LABELS.get(last_confirmed, STATUS_LABELS["unknown"]),
        f"Última verificación: {_humanize_elapsed(last_check_iso)} "
        f"(resultado crudo: {last_check_status or 'ninguno'})",
    ]
    if last_confirmed_iso:
        lines.append(f"Último estado confirmado ({last_confirmed}): {_humanize_elapsed(last_confirmed_iso)}")
    lines.append(event_url)
    return "\n".join(lines)


def _is_status_command(text: str) -> bool:
    text = text.strip().lower()
    return any(text == p or text.startswith(p + "@") or text.startswith(p + " ") for p in STATUS_COMMAND_PREFIXES)


def poll_and_respond_once(
    bot_token: str,
    offset: Optional[int],
    event_url: str,
    state: dict,
    timeout: int = 5,
) -> Optional[int]:
    """Hace una sola consulta a getUpdates, responde /estado pendientes y
    devuelve el nuevo offset a persistir (para no reprocesar los mismos
    mensajes en la próxima corrida)."""
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset

    try:
        resp = requests.get(
            f"{TELEGRAM_API_BASE}/bot{bot_token}/getUpdates", params=params, timeout=timeout + 10
        )
        resp.raise_for_status()
        updates = resp.json().get("result", [])
    except requests.RequestException:
        logger.exception("Error consultando getUpdates de Telegram")
        return offset

    for update in updates:
        offset = update["update_id"] + 1
        message = update.get("message") or {}
        text = message.get("text") or ""
        chat_id = message.get("chat", {}).get("id")
        if not chat_id or not _is_status_command(text):
            continue

        reply = build_status_message(state, event_url)
        send_telegram_message(bot_token, str(chat_id), reply)

    return offset


def run_command_listener(config, load_state_fn) -> None:
    """Loop de long-polling local (modo standalone, no usado en GitHub Actions)."""
    bot_token = config.telegram_bot_token
    if not bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN no configurado; el listener de comandos no arrancará.")
        return

    offset = None
    try:
        resp = requests.get(f"{TELEGRAM_API_BASE}/bot{bot_token}/getUpdates", timeout=15)
        resp.raise_for_status()
        pending = resp.json().get("result", [])
        if pending:
            # Saltamos mensajes viejos para no responderlos todos de golpe al arrancar.
            offset = pending[-1]["update_id"] + 1
    except requests.RequestException:
        logger.exception("No se pudo inicializar el offset de Telegram")

    logger.info("Listener de comandos de Telegram iniciado (responde /estado)")

    while True:
        offset = poll_and_respond_once(bot_token, offset, config.event_url, load_state_fn(), timeout=30)
