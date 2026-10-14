"""Prueba: varias llamadas a la vez NO se enredan entre si.

Comprueba que cada llamada tiene su PROPIO panel, sus PROPIOS números y su
PROPIO estado, aunque se atiendan interleaved (como haría Twilio con dos
llamadas reales a la vez):

    1) dos llamadas contestadas crean DOS paneles distintos (message_id distinto);
    2) los números de una NO aparecen en la otra (estado aislado por CallSid);
    3) colgar una NO toca a la otra;
    4) el panel NO se duplica si 'in-progress' y /voice/start abren a la vez
       (bandera panel_enviando);
    5) cerrar una llamada no limpia la otra.

No usa red ni Twilio: intercepta Telegram, la base de datos y TwiML.

Uso:  .venv\\Scripts\\python.exe prueba_llamadas_independientes.py
"""

import os
import sys
import threading
import time
import types

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

os.environ.setdefault("WEBHOOK_BASE_URL", "http://test.local")

import config as cfg  # noqa: E402
import servidor as s  # noqa: E402

# Se guardan las funciones REALES antes de sustituirlas por dobles de prueba.
_editar_real = s._tg_editar_mensaje

# --- Aislar Telegram, BD y relojes -------------------------------------------
nuevos: list = []  # sendMessage    -> mensajes NUEVOS (uno por llamada)
editados: list = []  # editMessageText -> ediciones (texto, teclado)


def _fake_mensaje(texto, teclado=None):
    nuevos.append({"texto": texto, "teclado": teclado})
    return 1000 + len(nuevos)  # message_id unico por mensaje


def _fake_editar(chat_id, message_id, texto, teclado=None):
    editados.append({"chat_id": chat_id, "message_id": message_id, "texto": texto})


s._tg_mensaje = _fake_mensaje
s._tg_editar_mensaje = _fake_editar
s._arrancar_reloj = lambda call_sid: None
s._EN_HILO = False  # todo sincrono en pruebas (sin hilos)
s._AUTORIZAR_ACTIVO = False  # en pruebas no se comprueban admins (sin red)
s._colgar_en_twilio = lambda call_sid: True
s.bd = types.SimpleNamespace(
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


def contestar(sid, telefono):
    """Simula que Twilio entrega la llamada a /voice/start (al contestar)."""
    return c.post(
        "/voice/start",
        data={"CallSid": sid, "To": telefono},
    ).get_data(as_text=True)


def texto_de(message_id):
    for e in reversed(editados):
        if e["message_id"] == message_id:
            return e["texto"]
    return ""


# --- 1) Dos llamadas contestadas => DOS paneles distintos ---------------------
SID_A = "CAtestINDEPA00000000000000000000001"
SID_B = "CAtestINDEPB00000000000000000000002"
TEL_A = "+573001111111"
TEL_B = "+573002222222"

contestar(SID_A, TEL_A)
contestar(SID_B, TEL_B)

revisar("cada llamada tiene su propia ficha viva",
        SID_A in s.llamadas_activas and SID_B in s.llamadas_activas)
msg_a = s.llamadas_activas[SID_A].get("mensaje_id")
msg_b = s.llamadas_activas[SID_B].get("mensaje_id")
revisar("cada llamada tiene su PROPIO panel (message_id distinto)",
        msg_a is not None and msg_b is not None and msg_a != msg_b)
revisar("el panel A muestra el telefono A", TEL_A in nuevos[0]["texto"])
revisar("el panel B muestra el telefono B", TEL_B in nuevos[1]["texto"])
revisar("no se cruzaron los telefonos entre paneles",
        TEL_B not in nuevos[0]["texto"] and TEL_A not in nuevos[1]["texto"])

# --- 2) Los numeros de una NO aparecen en la otra -----------------------------
c.post("/voice/recibir-primero", data={"CallSid": SID_A, "Digits": "111111"})
c.post("/voice/recibir-segundo", data={"CallSid": SID_A, "Digits": "22"})
c.post("/voice/recibir-primero", data={"CallSid": SID_B, "Digits": "999999"})

ficha_a = s.llamadas_activas[SID_A]
ficha_b = s.llamadas_activas[SID_B]
revisar("la llamada A guardo su primer y segundo numero",
        ficha_a.get("primero") == "111111" and ficha_a.get("segundo") == "22")
revisar("la llamada B guardo SU primer numero (no el de A)",
        ficha_b.get("primero") == "999999")
revisar("la llamada B NO tiene segundo numero (nadie lo marco)",
        ficha_b.get("segundo") is None)
texto_a = texto_de(msg_a)
texto_b = texto_de(msg_b)
revisar("el panel A muestra 111111 y NO el 999999 de B",
        "111111" in texto_a and "999999" not in texto_a)
revisar("el panel B muestra 999999 y NO el 111111 de A",
        "999999" in texto_b and "111111" not in texto_b)
revisar("la llamada A quedo esperando validacion (Fase 1)",
        ficha_a.get("estado") == s.ESPERANDO_1_2)
revisar("la llamada B sigue pidiendo el segundo numero",
        ficha_b.get("estado") != s.ESPERANDO_1_2)

# --- 3) Colgar una NO toca a la otra ------------------------------------------
c.post(
    "/telegram/webhook",
    json={
        "callback_query": {
            "id": "cb",
            "data": f"colgar_{SID_A}",
            "from": {"username": "tester"},
            "message": {
                "chat": {"id": int(cfg.TELEGRAM_GROUP_CHAT_ID)},
                "message_id": 1,
            },
        }
    },
)
revisar("al colgar la A, esta desaparece del estado", SID_A not in s.llamadas_activas)
revisar("la llamada B SIGUE viva (no se la llevo el colgado de A)",
        SID_B in s.llamadas_activas)

# --- 4) El panel NO se duplica si dos caminos abren a la vez ------------------
SID_C = "CAtestINDEPC00000000000000000000003"
antes = len(nuevos)
# Simula el cruce real: in-progress y /voice/start abren el panel a la vez.
s.llamadas_activas[SID_C] = s._ficha_nueva("+573003333333", "🟢 en llamada")
s.llamadas_activas[SID_C]["panel_enviando"] = True  # primer camino "en curso"
s._abrir_panel(SID_C, "+573003333333", "🟢 en llamada")  # segundo camino
revisar("con panel_enviando en curso NO se manda un segundo mensaje",
        len(nuevos) == antes)
s.llamadas_activas[SID_C]["panel_enviando"] = False
s.llamadas_activas[SID_C]["mensaje_id"] = 5000
s._abrir_panel(SID_C, "+573003333333", "🟢 en llamada")
revisar("con el panel YA publicado tampoco se duplica", len(nuevos) == antes)

# --- 5) Cerrar una llamada no limpia la otra ----------------------------------
SID_D = "CAtestINDEPD00000000000000000000004"
contestar(SID_D, "+573004444444")
s._limpiar(SID_D)
revisar("limpiar la D la quita del estado", SID_D not in s.llamadas_activas)
revisar("la B sigue viva tras limpiar la D", SID_B in s.llamadas_activas)

# --- 6) El vigilante cierra una llamada MUDA ('completed' de Twilio perdido) --
# Escenario real: la llamada cuelga pero el aviso de Twilio nunca llega. Sin el
# vigilante, la ficha viviría para siempre y el panel quedaría 'en llamada' con
# el cronómetro congelado.
SID_E = "CAtestINDEPE00000000000000000000005"
contestar(SID_E, "+573005555555")
msg_e = s.llamadas_activas[SID_E]["mensaje_id"]
mudo = time.time() - (cfg.TIEMPO_SIN_ACTIVIDAD + 5)
s.llamadas_activas[SID_E]["latido"] = mudo
s.llamadas_activas[SID_E]["inicio"] = mudo
cerradas = s._cerrar_abandonadas()
revisar("el vigilante cierra la llamada muda (sin 'completed' de Twilio)",
        SID_E in cerradas and SID_E not in s.llamadas_activas)
revisar("su panel queda 'colgada' (no un 'en llamada' eterno)",
        "colgada" in texto_de(msg_e))
revisar("el vigilante NO toca una llamada que sigue dando señales de vida",
        SID_B not in cerradas and SID_B in s.llamadas_activas)

# --- 7) Agotar los intentos NO deja un panel 'en llamada' huérfano ------------
# Antes, _reintentar borraba la ficha SIN cerrar el panel: el botón 🛑 Colgar
# seguía ahí y el panel decía 'en llamada' para siempre.
SID_F = "CAtestINDEPF00000000000000000000006"
contestar(SID_F, "+573006666666")
msg_f = s.llamadas_activas[SID_F]["mensaje_id"]
for _ in range(cfg.MAX_INTENTOS_ENTRADA + 1):
    c.post("/voice/recibir-primero", data={"CallSid": SID_F, "Digits": ""})
revisar("al agotar los intentos (buzón/nadie marca) la llamada se cierra",
        SID_F not in s.llamadas_activas)
revisar("su panel queda 'colgada' (no huérfano)",
        "colgada" in texto_de(msg_f))

# --- 8) Varias llamadas SIMULTÁNEAS (hilos reales) no se pisan ---------------
SIDS_G = [f"CAtestINDG0000000000000000000000{i}" for i in range(5)]
TELS_G = [f"+57307000000{i}" for i in range(5)]
hilos = [
    threading.Thread(target=lambda sid=SIDS_G[i], tel=TELS_G[i]: contestar(sid, tel))
    for i in range(5)
]
for hilo in hilos:
    hilo.start()
for hilo in hilos:
    hilo.join()
msg_ids = [s.llamadas_activas.get(sid, {}).get("mensaje_id") for sid in SIDS_G]
revisar("5 llamadas simultáneas -> las 5 quedan vivas",
        all(sid in s.llamadas_activas for sid in SIDS_G))
revisar("5 llamadas simultáneas -> 5 paneles DISTINTOS (sin solaparse)",
        len(set(msg_ids)) == 5 and None not in msg_ids)
for i, sid in enumerate(SIDS_G):
    c.post("/voice/recibir-primero", data={"CallSid": sid, "Digits": f"5550{i}"})
for i, sid in enumerate(SIDS_G):
    revisar(f"la llamada simultánea {i} guarda SU número (sin fugas)",
            s.llamadas_activas[sid].get("primero") == f"5550{i}")
for i, sid in enumerate(SIDS_G):
    mid = msg_ids[i]
    texto = texto_de(mid)
    ajenos = [j for j in range(5) if j != i and f"5550{j}" in texto]
    revisar(f"el panel {i} NO muestra números de las otras", not ajenos)

# --- 9) Si Telegram responde 429, el panel se reintenta (no se congela) ------
class _Respuesta:
    def __init__(self, codigo: int) -> None:
        self.status_code = codigo

    def json(self):
        return {"ok": False, "parameters": {"retry_after": 0}}


peticiones: list = []


def _fake_post(url, **_kw):
    peticiones.append(url)
    # La primera vez: 429 (límite de Telegram). La segunda: todo bien.
    return _Respuesta(429 if len(peticiones) == 1 else 200)


class _RequestsFalso:
    post = staticmethod(_fake_post)


requests_reales = s.requests
s.requests = _RequestsFalso
try:
    _editar_real(1, 2, "duración actualizada")
finally:
    s.requests = requests_reales
revisar("con un 429 de Telegram la edición se reintenta (el panel no se congela)",
        len(peticiones) == 2)

print()
if fallos:
    print(f"RESULTADO: {len(fallos)} comprobacion(es) fallaron.")
    raise SystemExit(1)
print("RESULTADO: todas las comprobaciones pasaron.")
