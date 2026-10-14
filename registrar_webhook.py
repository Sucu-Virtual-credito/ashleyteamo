"""Registra el webhook de Telegram en tu bot (una sola vez por túnel/URL).

Que hace:
    1) Detecta la URL publica del tunel de ngrok (o usa WEBHOOK_BASE_URL del .env).
    2) Le dice a Telegram: "manda los mensajes del grupo a esta URL".

Como usarlo (con ngrok y el servidor ya corriendo):

    .\\.venv\\Scripts\\python.exe registrar_webhook.py

Tambien sirve para ver el estado actual y para quitarlo:

    .\\.venv\\Scripts\\python.exe registrar_webhook.py --info
    .\\.venv\\Scripts\\python.exe registrar_webhook.py --borrar

El token del bot se lee del .env (TELEGRAM_BOT_TOKEN). Si aun no lo pusiste,
pasalo como primer argumento:

    .\\.venv\\Scripts\\python.exe registrar_webhook.py 123456:ABC-DEF...
"""

import sys

import requests

import config as cfg

API = cfg.TELEGRAM_API_URL
RUTA_WEBHOOK = cfg.RUTA_WEBHOOK


def _token() -> str:
    """Token del bot: primer argumento sin '--' o el del .env."""
    for arg in sys.argv[1:]:
        if not arg.startswith("--") and arg.strip():
            return arg.strip()
    return (cfg.TELEGRAM_BOT_TOKEN or "").strip()


def _url_publica() -> str:
    """URL base del tunel (ngrok o WEBHOOK_BASE_URL)."""
    try:
        return cfg.webhook_base_url()
    except cfg.SinUrlPublicaError as exc:
        print(f"{exc}\n")
        return ""


def _mostrar_info(token: str) -> None:
    r = requests.get(f"{API}/bot{token}/getWebhookInfo", timeout=10).json()
    info = r.get("result") or {}
    print("Estado actual del webhook:")
    print(f"  url                 : {info.get('url') or '(ninguna)'}")
    print(f"  pending_update_count: {info.get('pending_update_count')}")
    if info.get("last_error_message"):
        print(f"  ultimo error        : {info.get('last_error_message')}")


def _publicar_comandos(token: str) -> None:
    """Registra el menu de comandos que aparece al escribir '/' en Telegram."""
    comandos = [
        {"command": "estado", "description": "Muestra el estado actual"},
        {"command": "saldo", "description": "Saldo restante y gasto en Twilio"},
        {"command": "llamar", "description": "Elige a cuál número llamar"},
        {"command": "ciclos", "description": "Cadena: llama en grupos y no para (/ciclos 5)"},
        {"command": "apagar", "description": "Detiene la cadena de llamadas"},
        {"command": "numeros", "description": "Muestra la lista a marcar"},
        {"command": "agregar", "description": "Agrega números: /agregar +573001234567"},
        {"command": "quitar", "description": "Quita números: /quitar +573001234567"},
        {"command": "limpiar", "description": "Vacía la lista de números"},
        {"command": "historial", "description": "Últimas llamadas registradas"},
        {"command": "reporte", "description": "Historial detallado: /reporte <CallSid>"},
    ]
    r = requests.post(
        f"{API}/bot{token}/setMyCommands",
        json={"commands": comandos},
        timeout=10,
    ).json()
    if r.get("ok"):
        print("Menú de comandos publicado (aparece al escribir '/').")
    else:
        print(f"No se pudo publicar el menú de comandos: {r}")


def main() -> int:
    token = _token()
    if not token or ":" not in token:
        print("Falta el token del bot (TELEGRAM_BOT_TOKEN en .env o primer argumento).")
        return 1

    if "--borrar" in sys.argv:
        r = requests.post(f"{API}/bot{token}/deleteWebhook", timeout=10).json()
        print("Webhook eliminado." if r.get("ok") else f"Error: {r}")
        return 0

    if "--info" in sys.argv:
        _mostrar_info(token)
        return 0

    base = _url_publica()
    if not base:
        print("No hay URL publica. Levanta ngrok primero:  ngrok http 5000")
        return 1

    url = base + RUTA_WEBHOOK
    print(f"Registrando webhook -> {url}")
    # drop_pending_updates=True: al re-registrar se descarta la cola vieja. Si
    # el servidor estuvo caido, Telegram acumulo ahi los comandos que ya no
    # sirven; entregarlos en desorden al arrancar confunde mas que ayuda.
    # allowed_updates: se pide 'my_chat_member' para enterarnos cuando agregan a
    # Nova a un grupo (y salirnos si NO está autorizado) y 'message'/'callback_query'
    # para los comandos y botones. La lista vive en config.py.
    #
    # OJO: si el webhook YA apuntaba a esta misma URL, Telegram contesta
    # "Webhook is already set" y NO limpia el error anterior ni reanuda la
    # entrega. Por eso, si venia fallando, se borra primero.
    try:
        info = requests.get(f"{API}/bot{token}/getWebhookInfo", timeout=10).json()
        venia_fallando = bool(((info.get("result") or {}).get("last_error_message")))
    except Exception:  # noqa: BLE001
        venia_fallando = False
    if venia_fallando:
        print("El webhook venia fallando; se borra primero para reiniciar la entrega.")
        requests.post(f"{API}/bot{token}/deleteWebhook", timeout=10)

    r = requests.post(
        f"{API}/bot{token}/setWebhook",
        json={
            "url": url,
            "drop_pending_updates": True,
            "allowed_updates": list(cfg.ALLOWED_UPDATES),
        },
        timeout=10,
    ).json()

    if not r.get("ok"):
        print(f"Telegram rechazo el registro: {r}")
        return 1

    print("Webhook registrado correctamente.\n")
    _publicar_comandos(token)
    _mostrar_info(token)
    print("\nListo. Ahora escribe /estado en tu grupo de Telegram.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())