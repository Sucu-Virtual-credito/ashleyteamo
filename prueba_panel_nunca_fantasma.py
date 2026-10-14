"""Prueba: una llamada que NADIE contesta NUNCA queda "en llamada" en el panel.

Bug real (llamada +573000000003, CallSid CAaa6bb...): Nova la creó a las
03:59:34.702 y Twilio avisó 'completed' a las 03:59:35.172, 0,47 s después. Nadie
contestó, pero en el grupo se quedó un panel VERDE ('✅ Contestó' / '🟢 en llamada')
para siempre.

Causa: el mensaje del panel se manda en un HILO (la red de Telegram no debe
demorar la respuesta de voz). Si el aviso de fin llega MIENTRAS el mensaje va en
camino, el cierre todavía NO tiene `mensaje_id` (no hay nada que editar) y, cuando
el hilo termina, la ficha ya se borró: el mensaje recién publicado se queda verde y
nadie lo pinta en rojo.

El arreglo: el cierre ANOTA su rojo (`_paneles_finales`) cuando el panel aún se
está publicando y el hilo que publica lo pinta sobre el mensaje que acaba de
mandar. Esta prueba fuerza ese cruce con un `sendMessage` LENTO y comprueba:

    1) el cierre mientras se publica el panel acaba en 🔴 (no en 🟢 "en llamada");
    2) el cruce 'in-progress' + /voice/start no duplica el panel y lo deja en rojo;
    3) un 'completed' que se adelanta a todo NO deja un panel verde al arrancar;
    4) un 'in-progress' tardío de una llamada ya cerrada NO recrea ficha ni panel;
    5) no se queda ningún cierre anotado pendiente (sin fugas).

No usa red ni Twilio de verdad: intercepta Telegram y la base de datos.

Uso:  .venv\\Scripts\\python.exe prueba_panel_nunca_fantasma.py
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

import servidor as s  # noqa: E402

# --- Telegram falso, con sendMessage LENTO ------------------------------------
# `PANEL` simula el ÚNICO mensaje que ve el operador: guarda el último texto y
# teclado que llegaron a Telegram (en el orden en que llegaron).
PANEL = {"texto": None, "teclado": None}
nuevos: list = []  # cada sendMessage (mensaje NUEVO): NO debe duplicarse
publicando = threading.Event()  # se enciende cuando un mensaje va en camino


def _fake_mensaje(texto, teclado=None):
    publicando.set()
    # La red de Telegram TARDA: aquí es donde el aviso de fin se cuela en
    # producción (el bug de +573000000003). Sin esta espera el cruce no ocurre.
    time.sleep(0.30)
    nuevos.append({"texto": texto, "teclado": teclado})
    PANEL["texto"], PANEL["teclado"] = texto, teclado
    return 700 + len(nuevos)


def _fake_editar(chat_id, message_id, texto, teclado=None):
    PANEL["texto"], PANEL["teclado"] = texto, teclado


s._tg_mensaje = _fake_mensaje
s._tg_editar_mensaje = _fake_editar
# Sin reloj en vivo (no hay refrescos periódicos) ni vigilante (que no cierre
# llamadas a mitad de la prueba).
s._arrancar_reloj = lambda call_sid="": None
s._arrancar_vigilante = lambda: None
s._AUTORIZAR_ACTIVO = False  # en pruebas no se comprueban admins (sin red)
s._colgar_en_twilio = lambda call_sid: True
s.bd = types.SimpleNamespace(
    nueva_llamada=lambda *a, **k: None,
    actualizar=lambda *a, **k: None,
    registrar_evento=lambda *a, **k: None,
    cerrar=lambda *a, **k: None,
)
# Como en producción: cada panel se publica/edita en su PROPIO hilo.
s._EN_HILO = True

c = s.app.test_client()
fallos = []
DESTINO = "+573000000003"


def revisar(desc, cond):
    print(f"[{'OK  ' if cond else 'FALLO'}] {desc}")
    if not cond:
        fallos.append(desc)


def tiene_colgar(teclado, sid):
    """¿El teclado (inline_keyboard) trae el botón de colgar de esa llamada?"""
    return any(
        (boton or {}).get("callback_data") == f"colgar_{sid}"
        for fila in (teclado or [])
        for boton in fila
    )


def texto_panel():
    return PANEL["texto"] or ""
# --- 1) El aviso de fin llega MIENTRAS se publica el panel --------------------
# Es EL caso de +573000000003: /voice/start dispara el envío y Twilio cierra la
# llamada 0,47 s después, con el mensaje todavía en camino.
SID1 = "CAtestFANTASMA00000000000000000000001"
c.post("/voice/start", data={"CallSid": SID1, "To": DESTINO})
revisar("el panel empezó a publicarse (envío en vuelo)", publicando.wait(2))
c.post(
    "/voice/estado-llamada",
    data={"CallSid": SID1, "CallStatus": "completed"},
)
revisar("el aviso de fin cierra y borra la ficha", SID1 not in s.llamadas_activas)
time.sleep(0.9)  # deja terminar el hilo que estaba publicando

revisar(
    "quien no contesta NO queda 'en llamada' (panel sin 🟢 en llamada)",
    "🟢 en llamada" not in texto_panel(),
)
revisar("el panel recién publicado queda en 🔴 colgado", "🔴 colgado" in texto_panel())
revisar(
    "el panel explica que la llamada terminó (completed)",
    "la llamada terminó (completed)" in texto_panel(),
)
revisar(
    "el panel conserva el botón '🛑 Colgar'",
    tiene_colgar(PANEL["teclado"], SID1),
)

# --- 2) Cruce 'in-progress' + /voice/start: ni se duplica ni queda verde ------
# Los dos caminos quieren el mismo panel; el segundo espera al envío en curso.
SID2 = "CAtestFANTASMA00000000000000000000002"
publicando.clear()
antes_nuevos = len(nuevos)
c.post(
    "/voice/estado-llamada",
    data={"CallSid": SID2, "CallStatus": "in-progress", "To": DESTINO},
)
revisar("'in-progress' publica el panel (envío en vuelo)", publicando.wait(2))
c.post("/voice/start", data={"CallSid": SID2, "To": DESTINO})
# El aviso de fin llega con el envío TODAVÍA en camino (el cruce de verdad).
c.post(
    "/voice/estado-llamada",
    data={"CallSid": SID2, "CallStatus": "completed"},
)
time.sleep(0.9)  # deja terminar el hilo que estaba publicando

revisar(
    "el cruce NO manda un segundo panel (ni uno verde)",
    len(nuevos) == antes_nuevos + 1,
)
revisar("tras el cruce el panel queda en 🔴 colgado", "🔴 colgado" in texto_panel())
revisar(
    "tras el cruce el panel NO dice '🟢 en llamada'",
    "🟢 en llamada" not in texto_panel(),
)

# --- 3) Un 'completed' que se ADELANTA a todo no deja panel verde -------------
# Aquí el aviso de fin llega antes que /voice/start: antes, /voice/start creaba la
# ficha con luz verde para una llamada que ya había terminado.
SID3 = "CAtestFANTASMA00000000000000000000003"
c.post(
    "/voice/estado-llamada",
    data={"CallSid": SID3, "CallStatus": "completed"},
)
revisar("el 'completed' adelantado deja el CallSid resuelto", s._resuelta(SID3))
antes_nuevos = len(nuevos)
c.post("/voice/start", data={"CallSid": SID3, "To": DESTINO})
time.sleep(0.2)
revisar(
    "/voice/start tardío NO abre un panel nuevo",
    len(nuevos) == antes_nuevos,
)
revisar(
    "/voice/start tardío NO crea la ficha (sin panel fantasma)",
    SID3 not in s.llamadas_activas,
)

# --- 4) 'in-progress' tardío de una llamada ya cerrada: no resucita nada ------
c.post(
    "/voice/estado-llamada",
    data={"CallSid": SID3, "CallStatus": "in-progress", "To": DESTINO},
)
time.sleep(0.2)
revisar(
    "'in-progress' tardío NO recrea la ficha",
    SID3 not in s.llamadas_activas,
)
revisar(
    "'in-progress' tardío NO manda un panel nuevo",
    len(nuevos) == antes_nuevos,
)

# --- 5) Sin fugas: los cierres anotados se consumen al publicar ---------------
revisar(
    "no queda ningún cierre anotado pendiente",
    not s._paneles_finales,
)

print()
if fallos:
    print(f"RESULTADO: {len(fallos)} comprobacion(es) fallaron.")
    raise SystemExit(1)
print("RESULTADO: todas las comprobaciones pasaron.")