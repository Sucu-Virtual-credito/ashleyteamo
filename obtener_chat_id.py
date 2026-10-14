"""Detecta el TELEGRAM_GROUP_CHAT_ID de tu grupo automaticamente.

Como usarlo:
    1) Crea el bot con @BotFather y copia su token.
    2) Agrega el bot al grupo de Telegram.
    3) Escribe cualquier mensaje en el grupo (por ejemplo: hola).
    4) Corre:

         .\\.venv\\Scripts\\python.exe obtener_chat_id.py

       Si aun no pusiste el token en el .env, pasalo como argumento:

         .\\.venv\\Scripts\\python.exe obtener_chat_id.py 123456:ABC-DEF...

El script lee los mensajes recientes del bot (getUpdates) y muestra el id de
cada grupo/canal. Copia ese numero (CON el signo menos) en tu .env:

    TELEGRAM_GROUP_CHAT_ID=-1001234567890
"""

import sys

import requests

import config as cfg

API = cfg.TELEGRAM_API_URL


def _token() -> str:
    """Token del bot: primer argumento de la linea de comandos o del .env."""
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return sys.argv[1].strip()
    return (cfg.TELEGRAM_BOT_TOKEN or "").strip()


def _quitar_webhook(token: str) -> None:
    """getUpdates no devuelve nada si hay un webhook activo; lo quitamos."""
    try:
        r = requests.get(f"{API}/bot{token}/getWebhookInfo", timeout=10).json()
    except Exception as exc:  # noqa: BLE001
        print(f"No se pudo consultar el webhook: {exc}")
        return
    if (r.get("result") or {}).get("url"):
        print("Hay un webhook activo; lo desactivo para poder ver los mensajes...")
        requests.post(f"{API}/bot{token}/deleteWebhook", timeout=10)
        print("Webhook desactivado. (Vuelve a registrarlo cuando termines.)")


def main() -> int:
    token = _token()
    if not token or ":" not in token:
        print("Falta el token del bot.")
        print("Ponlo en .env (TELEGRAM_BOT_TOKEN=...) o pasalo como argumento:")
        print("    .\\.venv\\Scripts\\python.exe obtener_chat_id.py 123456:ABC-DEF...")
        return 1

    _quitar_webhook(token)

    try:
        respuesta = requests.get(f"{API}/bot{token}/getUpdates", timeout=10).json()
    except Exception as exc:  # noqa: BLE001
        print(f"No se pudo hablar con Telegram: {exc}")
        return 1

    if not respuesta.get("ok"):
        print(f"Telegram rechazo la peticion: {respuesta}")
        print("Revisa que el token sea correcto (lo da @BotFather).")
        return 1

    chats: dict[str, str] = {}
    for update in respuesta.get("result", []):
        mensaje = update.get("message") or update.get("edited_message") or {}
        chat = mensaje.get("chat")
        if chat and chat.get("type") in ("group", "supergroup", "channel"):
            chats[str(chat["id"])] = chat.get("title", "(sin titulo)")

    print("=" * 64)
    if not chats:
        print("No se encontro ningun grupo todavia.")
        print("Recuerda: agrega el bot al grupo Y escribe un mensaje dentro de el")
        print("(con el bot mencionado, ej: /start@TuBot). Luego corre esto otra vez.")
        print("=" * 64)
        return 0

    print("Grupos encontrados:")
    for chat_id, titulo in chats.items():
        print(f"  {chat_id}   ->  {titulo}")
    print("=" * 64)
    print("Copia el id (CON el signo menos) en tu .env:")
    for chat_id in chats:
        print(f"    TELEGRAM_GROUP_CHAT_ID={chat_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())