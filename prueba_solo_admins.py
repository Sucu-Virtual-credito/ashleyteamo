"""Prueba local del control de acceso: SOLO administradores y grupos autorizados.

Comprueba que:
    1) un grupo NO autorizado no recibe respuesta y Nova se SALE (leaveChat);
    2) si agregan a Nova a un grupo no autorizado (my_chat_member) se sale sola;
    3) en el grupo autorizado, un usuario que NO es admin queda bloqueado (🔒);
    4) un admin del grupo (lo dice getChatMember) SÍ puede usar los comandos;
    5) un ID de TELEGRAM_ADMIN_IDS pasa aunque getChatMember diga 'member';
    6) los botones (callback_query) también se filtran por admin;
    7) el resultado de getChatMember se cachea (no se pregunta en cada clic).

No usa red: intercepta Telegram (getChatMember/leaveChat) y Twilio.

Uso:  .venv\\Scripts\\python.exe prueba_solo_admins.py
"""

import os
import sys
import types

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

os.environ.setdefault("WEBHOOK_BASE_URL", "http://test.local")

import config as cfg  # noqa: E402
import servidor as s  # noqa: E402

CHAT_OK = int(cfg.TELEGRAM_GROUP_CHAT_ID)
CHAT_AJENO = "-1009999999999"

# --- Estado de los falsos y de las peticiones a Telegram ---------------------
ESTADOS: dict[str, str] = {}  # user_id -> status que devuelve getChatMember
peticiones: list[tuple] = []  # (metodo, url, datos) para inspeccionar
mensajes: list[str] = []
respuestas_boton: list[str] = []


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_get(url, params=None, timeout=None, **kw):
    peticiones.append(("GET", url, params))
    if url.endswith("/getChatMember"):
        estado = ESTADOS.get(str((params or {}).get("user_id")), "member")
        return _FakeResp({"ok": True, "result": {"status": estado}})
    return _FakeResp({"ok": True, "result": {}})


def _fake_post(url, json=None, **kw):
    peticiones.append(("POST", url, json))
    return _FakeResp({"ok": True, "result": {}})


s.requests = types.SimpleNamespace(get=_fake_get, post=_fake_post)
s._tg_mensaje = lambda texto, teclado=None: mensajes.append(texto)
s._tg_responder_boton = lambda cid, texto="": respuestas_boton.append(texto or "")
s._EN_HILO = False  # todo sincrono en pruebas (sin hilos)
s.bd = types.SimpleNamespace(
    ultimas=lambda *a, **k: [],
    resumen=lambda *a, **k: "",
    nueva_llamada=lambda *a, **k: None,
    actualizar=lambda *a, **k: None,
    registrar_evento=lambda *a, **k: None,
    cerrar=lambda *a, **k: None,
)

c = s.app.test_client()
fallos = []


def revisar(desc, cond):
    print(f"[{'OK  ' if cond else 'FALLO'}] {desc}")
    if not cond:
        fallos.append(desc)


def mandar(chat_id, texto, usuario):
    peticiones.clear()
    s._CACHE_ADMINS.clear()
    return c.post(
        "/telegram/webhook",
        json={
            "message": {
                "chat": {"id": chat_id, "type": "group"},
                "text": texto,
                "from": usuario,
            }
        },
    )


def pulsar(chat_id, usuario, data="colgar_CAx"):
    peticiones.clear()
    s._CACHE_ADMINS.clear()
    return c.post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "cb",
                "data": data,
                "from": usuario,
                "message": {"chat": {"id": chat_id, "type": "group"}, "message_id": 1},
            }
        },
    )


def hubo_salida():
    return any(u.endswith("/leaveChat") for _, u, _ in peticiones)


ADMIN = {"id": 555, "username": "jefe"}
RASO = {"id": 777, "username": "curioso"}

# --- 1) Un grupo NO autorizado no recibe nada y Nova se sale -----------------
mensajes.clear()
mandar(CHAT_AJENO, "/estado", ADMIN)
revisar("en un grupo NO autorizado Nova no manda ningún mensaje", mensajes == [])
revisar("en un grupo NO autorizado Nova se SALE (leaveChat)", hubo_salida())

# Un chat PRIVADO no autorizado solo se ignora (no se puede 'abandonar').
peticiones.clear()
c.post(
    "/telegram/webhook",
    json={
        "message": {
            "chat": {"id": 424242, "type": "private"},
            "text": "/estado",
            "from": ADMIN,
        }
    },
)
revisar("en un chat privado no autorizado Nova no se sale (solo ignora)",
        not hubo_salida())

# --- 2) Si la agregan a un grupo no autorizado, se sale sola -----------------
peticiones.clear()
c.post(
    "/telegram/webhook",
    json={
        "my_chat_member": {
            "chat": {"id": CHAT_AJENO, "type": "group"},
            "new_chat_member": {"status": "member"},
        }
    },
)
revisar("al agregarla a un grupo ajeno, Nova se SALE (my_chat_member)", hubo_salida())

peticiones.clear()
c.post(
    "/telegram/webhook",
    json={
        "my_chat_member": {
            "chat": {"id": CHAT_OK, "type": "group"},
            "new_chat_member": {"status": "member"},
        }
    },
)
revisar("al agregarla al grupo AUTORIZADO, Nova NO se sale", not hubo_salida())

# --- 3) En el grupo autorizado, un usuario que NO es admin queda bloqueado ---
ESTADOS.clear()  # getChatMember devolverá 'member' por defecto
mensajes.clear()
mandar(CHAT_OK, "/estado", RASO)
# El texto exacto puede cambiar; lo que NO puede cambiar es que se avise con 🔒
# y que el comando NO se ejecute (se comprueba justo abajo).
revisar("un usuario que no es admin recibe el aviso 🔒",
        any("🔒" in m for m in mensajes))
revisar("el aviso de bloqueo dice que no puede usar el bot",
        any("No puedes usar el bot" in m for m in mensajes))
revisar("el comando del no-admin NO se ejecuta (no hay 'Estado de Nova')",
        not any("Estado de Nova" in m for m in mensajes))
revisar("se preguntó a Telegram con getChatMember",
        any(u.endswith("/getChatMember") for _, u, _ in peticiones))

# --- 4) Un admin del grupo SÍ puede usar los comandos ------------------------
ESTADOS[str(ADMIN["id"])] = "administrator"
mensajes.clear()
mandar(CHAT_OK, "/estado", ADMIN)
revisar("un admin del grupo SÍ ejecuta el comando", any("Estado de Nova" in m for m in mensajes))

# --- 5) Un ID de TELEGRAM_ADMIN_IDS pasa aunque sea 'member' -----------------
admin_previos = list(cfg.TELEGRAM_ADMIN_IDS)
cfg.TELEGRAM_ADMIN_IDS = [str(RASO["id"])]
ESTADOS.clear()  # el grupo lo ve como 'member'
mensajes.clear()
mandar(CHAT_OK, "/estado", RASO)
revisar("un ID en TELEGRAM_ADMIN_IDS pasa aunque getChatMember diga 'member'",
        any("Estado de Nova" in m for m in mensajes))
revisar("a un ID explícito NO se le pregunta a Telegram (no hay getChatMember)",
        not any(u.endswith("/getChatMember") for _, u, _ in peticiones))
cfg.TELEGRAM_ADMIN_IDS = admin_previos

# --- 6) Los botones (callback_query) también se filtran por admin ------------
ESTADOS.clear()
respuestas_boton.clear()
pulsar(CHAT_OK, RASO)
revisar("un no-admin no puede pulsar botones (aviso 🔒)",
        any("administradores" in t for t in respuestas_boton))
respuestas_boton.clear()
pulsar(CHAT_AJENO, ADMIN)
revisar("un botón pulsado en un grupo ajeno se rechaza",
        any("no autorizado" in t.lower() for t in respuestas_boton))

# --- 7) getChatMember se cachea (no se pregunta en cada clic) ----------------
ESTADOS[str(ADMIN["id"])] = "administrator"
conversacion = {"chat": {"id": CHAT_OK}, "text": "/estado", "from": ADMIN}
s._CACHE_ADMINS.clear()
peticiones.clear()
c.post("/telegram/webhook", json={"message": dict(conversacion)})
primeras = sum(1 for _, u, _ in peticiones if u.endswith("/getChatMember"))
peticiones.clear()
c.post("/telegram/webhook", json={"message": dict(conversacion)})
segundas = sum(1 for _, u, _ in peticiones if u.endswith("/getChatMember"))
revisar("la 1.a vez sí pregunta por el admin", primeras == 1)
revisar("la 2.a vez usa el cache (no vuelve a preguntar)", segundas == 0)

# --- 8) chat_autorizado / chats_autorizados ----------------------------------
revisar("cfg.chat_autorizado con el grupo del .env", cfg.chat_autorizado(CHAT_OK))
revisar("cfg.chat_autorizado con un grupo ajeno", not cfg.chat_autorizado(CHAT_AJENO))

print()
if fallos:
    print(f"RESULTADO: {len(fallos)} comprobacion(es) fallaron.")
    raise SystemExit(1)
print("RESULTADO: todas las comprobaciones pasaron.")