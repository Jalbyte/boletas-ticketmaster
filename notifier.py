"""Envío de alertas por Telegram."""
import logging
from typing import Iterable

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> bool:
    """Envía un mensaje de texto a un solo chat. Devuelve True si tuvo éxito."""
    if not bot_token or not chat_id:
        logger.error("TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no configurados; no se puede notificar.")
        return False

    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        response = requests.post(url, json=payload, timeout=15)
        response.raise_for_status()
        return True
    except requests.RequestException:
        logger.exception("Fallo al enviar mensaje de Telegram a chat_id=%s", chat_id)
        return False


def send_telegram_message_to_many(bot_token: str, chat_ids: Iterable[str], text: str) -> bool:
    """Envía el mismo mensaje a varios chats. Devuelve True si al menos uno tuvo éxito."""
    chat_ids = [c.strip() for c in chat_ids if c and c.strip()]
    if not bot_token or not chat_ids:
        logger.error("TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID(S) no configurados; no se puede notificar.")
        return False

    # OJO: se usa una lista (no un generador) para forzar el envío a TODOS los
    # chat_ids; any() sobre un generador cortaría en el primer éxito y nunca
    # llamaría a send_telegram_message para el resto de destinatarios.
    results = [send_telegram_message(bot_token, chat_id, text) for chat_id in chat_ids]
    return any(results)
