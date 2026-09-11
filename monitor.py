"""
Monitor de disponibilidad de boletas en Ticketmaster.

Carga la página del evento con un navegador real (Playwright), clasifica su
estado (agotado / disponible / bloqueado por anti-bot / desconocido) y avisa
por Telegram únicamente cuando detecta una transición hacia "disponible".

No automatiza la compra ni intenta evadir la protección anti-bot de
Ticketmaster (sin proxies, sin resolución de CAPTCHA); si la página bloquea
la visita, simplemente se reintenta en el siguiente ciclo.
"""
import argparse
import json
import logging
import os
import random
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from notifier import send_telegram_message_to_many
from telegram_bot import run_command_listener, poll_and_respond_once

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("monitor")

STATUS_AVAILABLE = "available"
STATUS_SOLD_OUT = "sold_out"
STATUS_BLOCKED = "blocked_unknown"
STATUS_UNKNOWN = "unknown"

# Heurísticas de texto (minúsculas, sin acentos ya normalizados abajo).
BLOCKED_PATTERNS = [
    "actividad sospechosa",
    "hemos detectado actividad",
    "verifica que eres humano",
    "verificacion adicional",
    "acceso denegado",
    "captcha",
    "access denied",
]
SOLD_OUT_PATTERNS = [
    "agotado",
    "no hay boletas disponibles",
    "sin boletas disponibles",
    "boletas no disponibles",
    "evento agotado",
    "proximamente",
    "no tickets available",
    "sold out",
    "soldout",  # clase CSS "status-soldout" usada por el widget #picker-bar
]
AVAILABLE_PATTERNS = [
    "ver entradas",  # texto real del botón #buyButton en ticketmaster.co cuando hay boletas
    "comprar boletas",
    "comprar ahora",
    "seleccionar boletas",
    "agregar al carrito",
    "elige tus boletas",
    "encontrar boletas",
    "buy tickets",
    "find tickets",
]


def _normalize(text: str) -> str:
    import unicodedata

    text = text.lower()
    text = "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )
    return text


# Selectores de los widgets reales de estado en ticketmaster.co, en orden de
# preferencia. "#picker-bar .event-status" es el usado por BTS (clase
# "status-soldout" + texto "Agotado"); "#buyButton" es el usado por otros
# eventos como Maná (texto "Ver entradas" cuando hay boletas). Si ninguno
# aparece, se cae a escanear el texto completo de la página como respaldo.
STATUS_WIDGET_SELECTORS = [
    "#picker-bar .event-status",
    ".event-status",
    ".action-container #buyButton",  # botón real de compra dentro de su contenedor
    "#buyButton",  # respaldo por si el marcado cambia y no está en .action-container
]


def extract_status_text(page) -> str:
    """Extrae el texto+clase del widget de estado real de la página, si existe."""
    for selector in STATUS_WIDGET_SELECTORS:
        try:
            element = page.query_selector(selector)
        except Exception:
            element = None
        if not element:
            continue

        if "buyButton" in selector:
            # El botón puede existir en el DOM pero seguir deshabilitado
            # (ej. mientras cuenta regresiva antes de la apertura). Su sola
            # presencia con texto "Ver entradas" no basta: debe poder
            # hacerse clic para que realmente signifique "disponible".
            try:
                if element.is_disabled():
                    return "buyButton presente pero deshabilitado (todavía no se puede comprar)"
            except Exception:
                pass

        class_attr = element.get_attribute("class") or ""
        text = element.inner_text() or ""
        return f"{class_attr} {text}".strip()
    return page.inner_text("body")


def classify_page_text(raw_text: str) -> str:
    text = _normalize(raw_text)

    if any(p in text for p in BLOCKED_PATTERNS):
        return STATUS_BLOCKED
    if any(p in text for p in SOLD_OUT_PATTERNS):
        return STATUS_SOLD_OUT
    if any(p in text for p in AVAILABLE_PATTERNS):
        return STATUS_AVAILABLE
    return STATUS_UNKNOWN


@dataclass
class Config:
    event_url: str
    telegram_bot_token: str
    telegram_chat_ids: List[str]
    poll_interval_seconds: int
    poll_jitter_seconds: int
    heartbeat_hours: float
    state_file: Path
    browser_state_dir: Path
    snapshot_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            event_url=os.environ.get(
                "EVENT_URL",
                "https://www.ticketmaster.co/event/bts-world-tour-venta-general-viernes-2-octubre",
            ),
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_ids=[
                c.strip() for c in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()
            ],
            poll_interval_seconds=int(os.environ.get("POLL_INTERVAL_SECONDS", "240")),
            poll_jitter_seconds=int(os.environ.get("POLL_JITTER_SECONDS", "90")),
            heartbeat_hours=float(os.environ.get("HEARTBEAT_HOURS", "6")),
            state_file=Path(os.environ.get("STATE_FILE", "state.json")),
            browser_state_dir=Path(os.environ.get("BROWSER_STATE_DIR", "browser_state")),
            snapshot_dir=Path(os.environ.get("SNAPSHOT_DIR", "debug_snapshots")),
        )


DEFAULT_STATE = {
    "last_confirmed_status": None,  # último estado "real" (available/sold_out)
    "last_confirmed_iso": None,
    "last_check_status": None,  # resultado crudo de la última corrida (incluye blocked/unknown)
    "last_check_iso": None,
    "last_check_detail": None,  # texto/clase del widget detectado en la última corrida
    "last_heartbeat_iso": None,
    "telegram_update_offset": None,  # offset de getUpdates, para no reprocesar mensajes viejos
}


def load_state(state_file: Path) -> dict:
    if state_file.exists():
        try:
            return {**DEFAULT_STATE, **json.loads(state_file.read_text(encoding="utf-8"))}
        except json.JSONDecodeError:
            logger.warning("state.json corrupto, se reinicia el estado")
    return dict(DEFAULT_STATE)


def save_state(state_file: Path, state: dict) -> None:
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")


def save_snapshot(snapshot_dir: Path, status: str, html: str) -> None:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = snapshot_dir / f"{ts}_{status}.html"
    path.write_text(html, encoding="utf-8")
    logger.info("Snapshot guardado en %s (revisa el HTML para ajustar los patrones de detección)", path)


def check_event_page(config: Config):
    """Carga la página una vez y devuelve (estado, texto_detectado)."""
    config.browser_state_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(config.browser_state_dir),
            headless=True,
            locale="es-CO",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
        )
        try:
            page = context.new_page()
            page.goto(config.event_url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except PlaywrightTimeoutError:
                pass  # algunas páginas nunca llegan a "networkidle"; seguimos con lo cargado

            status_text = extract_status_text(page)
            status = classify_page_text(status_text)

            if status == STATUS_UNKNOWN:
                # El widget específico (si apareció) no coincidió con ningún
                # patrón conocido; probamos con el texto completo de la
                # página como último recurso antes de darnos por vencidos.
                status = classify_page_text(page.inner_text("body"))

            logger.info("Texto de estado detectado: %r -> %s", status_text[:200], status)

            if status in (STATUS_BLOCKED, STATUS_UNKNOWN):
                save_snapshot(config.snapshot_dir, status, page.content())

            return status, status_text
        finally:
            context.close()


def maybe_send_heartbeat(config: Config, state: dict) -> None:
    if config.heartbeat_hours <= 0:
        return

    last_iso = state.get("last_heartbeat_iso")
    now = datetime.now(timezone.utc)
    if last_iso:
        last = datetime.fromisoformat(last_iso)
        elapsed_hours = (now - last).total_seconds() / 3600
        if elapsed_hours < config.heartbeat_hours:
            return

    sent = send_telegram_message_to_many(
        config.telegram_bot_token,
        config.telegram_chat_ids,
        "🤖 Monitor de boletas sigue activo.\n"
        f"Evento: {config.event_url}\n"
        f"Último estado confirmado: {state.get('last_confirmed_status', 'desconocido')}",
    )
    if sent:
        state["last_heartbeat_iso"] = now.isoformat()


def publish_github_status(status: str, detail: str) -> None:
    """Publica el resultado como un "commit status" de GitHub, para que un
    dashboard externo pueda leer el historial vía la API pública de GitHub
    (sin depender de descargar logs, que tienen problemas de CORS en el
    navegador). No hace nada si no corremos dentro de GitHub Actions."""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    sha = os.environ.get("GITHUB_SHA")
    if not (token and repo and sha):
        return

    state_map = {STATUS_AVAILABLE: "success", STATUS_SOLD_OUT: "pending"}
    gh_state = state_map.get(status, "error")

    run_id = os.environ.get("GITHUB_RUN_ID", "")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    payload = {
        "state": gh_state,
        "context": "ticket-monitor",
        "description": f"{status}: {detail}".strip()[:140],
    }
    if run_id:
        payload["target_url"] = f"{server}/{repo}/actions/runs/{run_id}"

    try:
        requests.post(
            f"https://api.github.com/repos/{repo}/statuses/{sha}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            json=payload,
            timeout=10,
        )
    except requests.RequestException:
        logger.exception("No se pudo publicar el commit status en GitHub")


def run_once(config: Config, check_telegram: bool = True) -> str:
    state = load_state(config.state_file)
    previous_confirmed = state.get("last_confirmed_status")

    try:
        status, status_text = check_event_page(config)
    except Exception:
        logger.exception("Error al revisar la página; se reintentará en el siguiente ciclo")
        status, status_text = STATUS_UNKNOWN, ""

    now_iso = datetime.now(timezone.utc).isoformat()
    state["last_check_status"] = status
    state["last_check_iso"] = now_iso
    state["last_check_detail"] = status_text

    logger.info("Estado detectado: %s (último confirmado: %s)", status, previous_confirmed)

    publish_github_status(status, status_text)

    alert_sent = False
    if status == STATUS_AVAILABLE and previous_confirmed != STATUS_AVAILABLE:
        logger.info("¡Transición a disponible detectada! Enviando alerta de Telegram.")
        send_telegram_message_to_many(
            config.telegram_bot_token,
            config.telegram_chat_ids,
            "🚨 ¡Boletas posiblemente disponibles!\n"
            f"{config.event_url}\n\nEntra YA a comprar manualmente.",
        )
        alert_sent = True

    # Solo persistimos transiciones "reales" (available/sold_out); los estados
    # bloqueado/desconocido no sobreescriben el último estado confirmado.
    if status in (STATUS_AVAILABLE, STATUS_SOLD_OUT):
        state["last_confirmed_status"] = status
        state["last_confirmed_iso"] = now_iso

    if alert_sent:
        # La alerta ya deja claro que el monitor está vivo; evitamos mandar
        # también el heartbeat en el mismo ciclo (sería redundante/confuso).
        state["last_heartbeat_iso"] = now_iso
    else:
        maybe_send_heartbeat(config, state)

    # check_telegram=False en modo loop local: ahí un hilo aparte con
    # long-polling ya atiende /estado; hacerlo también aquí competiría por
    # el mismo offset de getUpdates y duplicaría/perdería respuestas.
    if check_telegram and config.telegram_bot_token:
        state["telegram_update_offset"] = poll_and_respond_once(
            config.telegram_bot_token,
            state.get("telegram_update_offset"),
            config.event_url,
            state,
            timeout=5,
        )

    save_state(config.state_file, state)
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor de boletas Ticketmaster")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Corre una sola verificación y termina (útil para probar el detector).",
    )
    args = parser.parse_args()

    config = Config.from_env()

    if not config.telegram_bot_token or not config.telegram_chat_ids:
        logger.warning(
            "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID no configurados. "
            "El monitor seguirá corriendo pero no podrá enviar alertas."
        )

    if args.once:
        run_once(config)
        return

    if config.telegram_bot_token:
        listener = threading.Thread(
            target=run_command_listener,
            args=(config, lambda: load_state(config.state_file)),
            daemon=True,
        )
        listener.start()

    logger.info("Iniciando monitor para %s", config.event_url)
    while True:
        run_once(config, check_telegram=False)
        sleep_for = config.poll_interval_seconds + random.uniform(0, config.poll_jitter_seconds)
        logger.info("Esperando %.0f segundos hasta el próximo chequeo", sleep_for)
        time.sleep(sleep_for)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
