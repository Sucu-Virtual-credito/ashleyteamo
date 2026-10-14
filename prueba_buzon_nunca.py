"""Prueba: las llamadas NUNCA llegan al buzón de voz.

Objetivo: si Twilio identifica una máquina (buzón/fax), la llamada se cuelga de
inmediato y sin dejar mensaje. Cubre:

    1) el helper `_es_maquina` (solo 'machine*' y 'fax' cuentan como máquina
       SEGURA: con ellos se cuelga para no dejar mensaje en el buzón);
    1b) el helper `_amd_dudosa`: 'unknown' NO se cuelga (puede ser un humano que
       tarda en hablar), solo se marca como dudoso;
    2) el colgado en `/voice/start` (AMD síncrono: 'AnsweredBy' llega aquí);
    3) el colgado en `/voice/estado-llamada` (status callback);
    4) que un humano NO se cuelga (sigue el flujo normal);
    5) que la detección de buzón (AMD) está activada y que `_crear_llamada` la
       pide a Twilio.

No usa red: simula las peticiones de Twilio y el cliente de Twilio.

Uso:  .venv\\Scripts\\python.exe prueba_buzon_nunca.py
"""

import os
import sys
import types

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

os.environ["WEBHOOK_BASE_URL"] = "http://test.local"

import config as cfg  # noqa: E402
import servidor as s  # noqa: E402

DESTINO = "+573000000001"

paneles = []
editados = []
s._tg_panel = lambda call_sid, teclado=None: paneles.append(call_sid)
s._tg_editar_mensaje = lambda *a, **k: editados.append(a)
s._tg_mensaje = lambda *a, **k: 1
s._bd = lambda *a, **k: None
s._arrancar_reloj = lambda call_sid: None  # sin hilos en la prueba
s._EN_HILO = False  # todo sincrono en pruebas (sin hilos)
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


def abrir_panel(sid):
    """Simula que alguien CONTESTÓ: el panel ya está abierto en el grupo."""
    s._abrir_panel(sid, DESTINO, "🟢 en llamada")


# --- 1) Helper `_es_maquina` -----------------------------------------------
for valor, esperado in [
    ("machine_start", True),
    ("machine_end_beep", True),
    ("machine_end_silence", True),
    ("machine_end_other", True),
    ("MACHINE_START", True),
    ("fax", True),
    # 'unknown' = "la AMD no alcanzó a decidir" (se rindió tras el timeout). NO
    # se cuelga: es también el caso típico de un HUMANO que tarda en decir "aló"
    # (así se cortaban llamadas reales; bug de +573000000002, `Duró: 00:03`).
    ("unknown", False),
    ("human", False),
    ("", False),
]:
    revisar(
        f"_es_maquina('{valor}') == {esperado}",
        s._es_maquina(valor) is esperado,
    )

# --- 1b) Helper `_amd_dudosa` ('unknown' = sin veredicto, pero SIN colgar) ---
for valor, esperado in [
    ("unknown", True),
    ("UNKNOWN", True),
    ("human", False),
    ("machine_start", False),
    ("fax", False),
    # VACÍO no es dudoso: es "no hay veredicto de AMD" (detección desactivada).
    ("", False),
]:
    revisar(
        f"_amd_dudosa('{valor}') == {esperado}",
        s._amd_dudosa(valor) is esperado,
    )

# --- 2) Buzón en `/voice/start` (AMD síncrono) -----------------------------
SID1 = "CAtestBUZONSTART0000000000000000001"
abrir_panel(SID1)
antes = len(paneles)
r = c.post(
    "/voice/start",
    data={"CallSid": SID1, "To": DESTINO, "AnsweredBy": "machine_start"},
)
texto = r.get_data(as_text=True)
revisar("start+buzón -> cuelga (TwiML <Hangup>)", "<Hangup" in texto)
revisar("start+buzón -> NO se dice el saludo", "<Say" not in texto)
revisar("start+buzón -> la llamada se limpia", SID1 not in s.llamadas_activas)
revisar("start+buzón -> se avisó/refrescó el panel", len(paneles) > antes)

# --- 3) Buzón en `/voice/estado-llamada` (status callback) ------------------
SID2 = "CAtestBUZONCALLBACK0000000000000000002"
abrir_panel(SID2)
antes = len(paneles)
r = c.post(
    "/voice/estado-llamada",
    data={"CallSid": SID2, "CallStatus": "in-progress", "AnsweredBy": "machine_end_silence"},
)
texto = r.get_data(as_text=True)
revisar("callback+buzón -> cuelga (TwiML <Hangup>)", "<Hangup" in texto)
revisar("callback+buzón -> la llamada se limpia", SID2 not in s.llamadas_activas)
revisar("callback+buzón -> se avisó el panel", len(paneles) > antes)

# --- 4) Un humano NO se cuelga ---------------------------------------------
SID3 = "CAtestHUMANO0000000000000000000000003"
abrir_panel(SID3)
r = c.post(
    "/voice/start",
    data={"CallSid": SID3, "To": DESTINO, "AnsweredBy": "human"},
)
texto = r.get_data(as_text=True)
revisar("start+humano -> NO cuelga", "<Hangup" not in texto)
revisar("start+humano -> sigue el flujo (saludo + primer dato)", "pedir-primero" in texto)
revisar("start+humano -> la llamada sigue activa", SID3 in s.llamadas_activas)

# --- 5) La detección de buzón (AMD) está activada ---------------------------
revisar(
    "MAQUINA_DETECCION está activada (Enable)",
    cfg.MAQUINA_DETECCION.strip().lower() == "enable",
)

capturado = {}


class _Calls:
    def create(self, **kw):
        capturado.update(kw)
        return types.SimpleNamespace(sid="CAfakecrear000000000000000000000000")


s.twilio_client = types.SimpleNamespace(calls=_Calls())
s._from_para = lambda destino: "+15705550123"
s._origenes_disponibles = lambda: []
s._recordar_origen = lambda *a, **k: None

s._crear_llamada(DESTINO, "http://test.local")
revisar(
    "al marcar se pide machine_detection a Twilio",
    capturado.get("machine_detection") == cfg.MAQUINA_DETECCION,
)
revisar(
    "al marcar se fija cuántos segundos insiste la AMD (en SEGUNDOS)",
    capturado.get("machine_detection_timeout") == cfg.MAQUINA_DETECCION_TIMEOUT,
)
revisar(
    "al marcar la AMD va ASINCRONA (el saludo no espera el análisis)",
    capturado.get("async_amd") == "true",
)
revisar(
    "la AMD asíncrona avisa a /voice/amd (por si contesta un buzón)",
    str(capturado.get("async_amd_status_callback", "")).endswith("/voice/amd"),
)
revisar(
    "se pide el aviso 'answered' en el status callback",
    "answered" in capturado.get("status_callback_event", []),
)
revisar(
    "se pone un tope DURO de duración en Twilio (red de seguridad)",
    capturado.get("time_limit") == cfg.LIMITE_LLAMADA,
)

# --- 6) /voice/amd (AMD asíncrona): cuelga la llamada VIVA si es buzón --------
SID4 = "CAtestAMDMAQUINA00000000000000000000004"
abrir_panel(SID4)
s.llamadas_activas[SID4] = s._ficha_nueva(DESTINO, "🟢 en llamada")
colgadas_api = []
s._colgar_en_twilio = lambda call_sid: colgadas_api.append(call_sid) or True
r = c.post("/voice/amd", data={"CallSid": SID4, "AnsweredBy": "machine_start"})
revisar("/voice/amd + buzón -> se cuelga la llamada viva por la API", SID4 in colgadas_api)
revisar("/voice/amd + buzón -> la llamada se limpia", SID4 not in s.llamadas_activas)

SID5 = "CAtestAMDHUMANO000000000000000000000005"
abrir_panel(SID5)
s.llamadas_activas[SID5] = s._ficha_nueva(DESTINO, "🟢 en llamada")
colgadas_api.clear()
r = c.post("/voice/amd", data={"CallSid": SID5, "AnsweredBy": "human"})
revisar("/voice/amd + humano -> NO cuelga", colgadas_api == [])
revisar("/voice/amd + humano -> la llamada sigue activa", SID5 in s.llamadas_activas)

# --- 7) /voice/amd con veredicto DUDOSO ('unknown') -> NO cuelga ---------------
# 'unknown' = la AMD se rindió sin decidir. Antes se colgaba y cortaba a humanos
# reales que tardaban unos segundos en hablar (bug de +573000000002: el panel
# mostraba `✅ Contestó` y `📵 contestó un buzón` con `⏱️ Duró: 00:03`). Ahora NO
# se cuelga: solo se marca como dudoso y la llamada sigue.
SID6 = "CAtestAMDUNKNOWN00000000000000000000006"
abrir_panel(SID6)
s.llamadas_activas[SID6] = s._ficha_nueva(DESTINO, "🟢 en llamada")
colgadas_api.clear()
r = c.post("/voice/amd", data={"CallSid": SID6, "AnsweredBy": "unknown"})
revisar(
    "/voice/amd + 'unknown' -> NO cuelga (podría ser un humano que tardó)",
    SID6 not in colgadas_api,
)
revisar("/voice/amd + 'unknown' -> la llamada SIGUE viva", SID6 in s.llamadas_activas)
revisar(
    "/voice/amd + 'unknown' -> queda marcada como dudosa",
    s.llamadas_activas[SID6].get("amd_dudosa") is True,
)

# --- 7b) 'unknown' en /voice/start -> tampoco cuelga --------------------------
SID7 = "CAtestSTARTUNKNOWN000000000000000000007"
abrir_panel(SID7)
r = c.post(
    "/voice/start",
    data={"CallSid": SID7, "To": DESTINO, "AnsweredBy": "unknown"},
)
texto = r.get_data(as_text=True)
revisar("start+'unknown' -> NO cuelga (TwiML sin <Hangup>)", "<Hangup" not in texto)
revisar("start+'unknown' -> dice el saludo y sigue el flujo", "pedir-primero" in texto)
revisar(
    "start+'unknown' -> la llamada sigue activa y marcada como dudosa",
    SID7 in s.llamadas_activas
    and s.llamadas_activas[SID7].get("amd_dudosa") is True,
)

# --- 7c) 'unknown' en el status callback -> tampoco cuelga -------------------
SID8 = "CAtestSTATUSUNKNOWN00000000000000000008"
abrir_panel(SID8)
r = c.post(
    "/voice/estado-llamada",
    data={"CallSid": SID8, "CallStatus": "in-progress", "AnsweredBy": "unknown"},
)
texto = r.get_data(as_text=True)
revisar("estado+'unknown' -> NO cuelga", "<Hangup" not in texto)
revisar("estado+'unknown' -> la llamada sigue viva", SID8 in s.llamadas_activas)

# --- 7d) La red de seguridad SÍ cuelga al buzón que no marca -----------------
# Con 'unknown' el flujo sigue, pero si nadie marca nada en el primer dato, la
# red de seguridad (MAX_INTENTOS_ENTRADA) lo cuelga SIN dejar mensaje, y el aviso
# lo dice claro ("sin confirmar humano") en vez de acusar de buzón a ciegas.
SID9 = "CAtestSEGURIDADBUZON00000000000000000009"
abrir_panel(SID9)
s.llamadas_activas[SID9] = s._ficha_nueva(DESTINO, "🟢 en llamada")
s._marcar_dudosa(SID9)
motivos = []
_cerrar_real = s._cerrar_llamada
s._cerrar_llamada = lambda call_sid, motivo: motivos.append(motivo) or _cerrar_real(
    call_sid, motivo
)
for _ in range(cfg.MAX_INTENTOS_ENTRADA + 1):
    c.post("/voice/recibir-primero", data={"CallSid": SID9, "Digits": ""})
s._cerrar_llamada = _cerrar_real
revisar(
    "tras agotar intentos con AMD dudosa -> aviso 'sin confirmar humano'",
    any("sin confirmar humano" in (m or "") for m in motivos),
)
revisar(
    "el primer reintento del dato 1 pregunta '¿aló?' (da turno al humano)",
    s.ESPERANDO_HUMANO_TXT == "¿aló?",
)

print()
if fallos:
    print(f"RESULTADO: {len(fallos)} comprobacion(es) fallaron.")
    raise SystemExit(1)
print("RESULTADO: todas las comprobaciones pasaron.")