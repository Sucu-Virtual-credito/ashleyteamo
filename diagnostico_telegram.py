"""Diagnostico de Telegram para Nova.

Revisa, en orden, todo lo que hace falta para usar Nova desde tu grupo de
Telegram y te dice EXACTAMENTE que falta arreglar:

    1) Que el TELEGRAM_BOT_TOKEN del .env sea valido (pregunta a Telegram).
    2) Que el webhook este registrado y apuntando a tu URL publica.
    3) Que TELEGRAM_GROUP_CHAT_ID este puesto.
    4) Que el bot pueda escribir en el grupo (manda un mensaje de prueba).

Uso:

    .\\.venv\\Scripts\\python.exe diagnostico_telegram.py

Si aun no pusiste el token en el .env, pasalo como argumento:

    .\\.venv\\Scripts\\python.exe diagnostico_telegram.py 123456:ABC-DEF...
"""

import sys

import requests

import config as cfg

API = cfg.TELEGRAM_API_URL
RUTA_WEBHOOK = "/telegram/webhook"

VERDE = "[OK  ]"
ROJO = "[FALLA]"
AVISO = "[AVISO]"


def _token() -> str:
    """Token del bot: primer argumento sin '--' o el del .env."""
    for arg in sys.argv[1:]:
        if not arg.startswith("--") and arg.strip():
            return arg.strip()
    return (cfg.TELEGRAM_BOT_TOKEN or "").strip()


def _linea(estado: str, texto: str) -> None:
    print(f"{estado} {texto}")


def _comprobar_token(token: str) -> str | None:
    """Valida el token con getMe. Devuelve el @usuario del bot o None."""
    if not token or ":" not in token:
        _linea(ROJO, "Falta TELEGRAM_BOT_TOKEN en el .env (formato 123456:AAE...).")
        print("        Consiguelo: abre Telegram -> @BotFather -> /newbot")
        return None
    try:
        r = requests.get(f"{API}/bot{token}/getMe", timeout=10).json()
    except Exception as exc:  # noqa: BLE001
        _linea(ROJO, f"No se pudo hablar con Telegram: {exc}")
        return None
    if not r.get("ok"):
        _linea(ROJO, f"Telegram rechazo el token: {r.get('description')}")
        print("        Revisa que lo copiaste completo desde @BotFather.")
        return None
    bot = r["result"]
    _linea(VERDE, f"Token valido. Bot: @{bot.get('username')} ({bot.get('first_name')})")
    return bot.get("username")


def _comprobar_webhook(token: str) -> None:
    """Muestra el webhook actual y avisa si no coincide con la URL publica."""
    try:
        r = requests.get(f"{API}/bot{token}/getWebhookInfo", timeout=10).json()
    except Exception as exc:  # noqa: BLE001
        _linea(ROJO, f"No se pudo consultar el webhook: {exc}")
        return
    info = r.get("result") or {}
    url = info.get("url") or ""
    try:
        esperada = cfg.webhook_base_url() + RUTA_WEBHOOK
    except cfg.SinUrlPublicaError:
        esperada = "(sin WEBHOOK_BASE_URL ni ngrok activo)"

    if not url:
        _linea(ROJO, "No hay webhook registrado.")
        print("        Arreglalo:  .\\.venv\\Scripts\\python.exe registrar_webhook.py")
    elif url != esperada:
        _linea(AVISO, f"El webhook apunta a {url}")
        print(f"        Deberia ser {esperada}")
        print("        Re-registra:  .\\.venv\\Scripts\\python.exe registrar_webhook.py")
    else:
        _linea(VERDE, f"Webhook registrado y correcto: {url}")

    pendientes = info.get("pending_update_count")
    if pendientes:
        print(f"        (mensajes en cola: {pendientes})")
    if info.get("last_error_message"):
        _linea(AVISO, f"Ultimo error de Telegram: {info.get('last_error_message')}")


def _comprobar_chat_id() -> str:
    """Verifica que el chat del grupo este configurado."""
    chat_id = (cfg.TELEGRAM_GROUP_CHAT_ID or "").strip()
    if not chat_id:
        _linea(ROJO, "Falta TELEGRAM_GROUP_CHAT_ID en el .env.")
        print("        Sacalo:  .\\.venv\\Scripts\\python.exe obtener_chat_id.py")
    elif not chat_id.startswith("-"):
        _linea(AVISO, f"TELEGRAM_GROUP_CHAT_ID='{chat_id}' no empieza por '-'.")
        print("        Un grupo/canal siempre empieza por '-' (ej: -5565766651).")
    else:
        _linea(VERDE, f"TELEGRAM_GROUP_CHAT_ID configurado: {chat_id}")
    return chat_id


def _probar_envio(token: str, chat_id: str) -> None:
    """Manda un mensaje de prueba al grupo para confirmar que Nova puede hablar."""
    if not token or ":" not in token or not chat_id:
        return
    texto = "🤖 Nova conectada correctamente. Prueba de diagnostico."
    try:
        r = requests.post(
            f"{API}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": texto},
            timeout=10,
        ).json()
    except Exception as exc:  # noqa: BLE001
        _linea(ROJO, f"No se pudo enviar el mensaje de prueba: {exc}")
        return
    if r.get("ok"):
        _linea(VERDE, "Mensaje de prueba enviado. Revisa tu grupo de Telegram.")
    else:
        _linea(ROJO, f"El bot no pudo escribir en el grupo: {r.get('description')}")
        print("        Revisa que el bot este AGREGADO al grupo y sin restricciones.")


def main() -> int:
    print("=" * 68)
    print(" Diagnostico de Telegram para Nova")
    print("=" * 68)

    token = _token()
    usuario = _comprobar_token(token)
    if usuario:
        _comprobar_webhook(token)

    chat_id = _comprobar_chat_id()

    if usuario and chat_id and not sys.argv[1:2]:
        _probar_envio(token, chat_id)

    print("=" * 68)
    print("Resumen: si todo esta en [OK  ], escribe /estado en tu grupo.")
    print("Si algo esta en [FALLA], arreglalo y vuelve a correr este script.")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())