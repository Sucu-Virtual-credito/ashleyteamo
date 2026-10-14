"""Prueba: el panel de Telegram sale SOLO cuando el usuario CONTESTA.

Antes Nova manda el panel en cuanto marcaba (initiated/ringing). Ahora el menú
del grupo NO aparece mientras la llamada suena: solo se abre cuando alguien
coge el teléfono, para no llenar el grupo de llamadas que nadie contesta.

Comprueba que:
    - con 'initiated', 'queued' y 'ringing' NO se manda ningún mensaje;
    - al CONTESTAR ('in-progress') sale UN mensaje con el panel, el número de
      destino, la hora de contestación y el botón 🛑 Colgar;
    - al arrancar la conversación (/voice/start) se reutiliza ese MISMO
      mensaje (sin duplicar) y el estado pasa a '🟢 en llamada';
    - si 'in-progress' llega antes que /voice/start, el panel se abre igual
      (por cualquiera de los dos caminos) y NO se duplica;
    - un aviso tardío de 'ringing' NO pisa el estado ya avanzado.

No usa red ni Twilio de verdad: intercepta Telegram y la base de datos.

Uso:  .venv\\Scripts\\python.exe prueba_panel_al_contestar.py
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

# --- Aislar Telegram y la base de datos --------------------------------------
nuevos: list = []  # sendMessage    -> mensajes NUEVOS (cada llamada tiene UNO)
editados: list = []  # editMessageText -> ediciones del mismo mensaje


def _fake_mensaje(texto, teclado=None):
    nuevos.append({"texto": texto, "teclado": teclado})
    return 1000 + len(nuevos)


def _fake_editar(chat_id, message_id, texto, teclado=None):
    editados.append({"texto": texto, "teclado": teclado})


s._tg_mensaje = _fake_mensaje
s._tg_editar_mensaje = _fake_editar
# El reloj no se arranca en el test (evita refrescos asincronos).
s._arrancar_reloj = lambda call_sid: None
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


def tiene_colgar(teclado, sid):
    """¿El teclado (inline_keyboard) trae el botón de colgar de esa llamada?"""
    return any(
        (boton or {}).get("callback_data") == f"colgar_{sid}"
        for fila in (teclado or [])
        for boton in fila
    )


DESTINO = "+573000000001"
SID = "CAtestCONTESTA000000000000000000001"


# --- 1) Mientras suena NO hay panel: 'initiated', 'queued' y 'ringing' --------
for estado_aviso in ("initiated", "queued", "ringing"):
    r = c.post(
        "/voice/estado-llamada",
        data={"CallSid": SID, "CallStatus": estado_aviso, "To": DESTINO},
    )
    revisar(f"'{estado_aviso}' responde 204 (sin TwiML)", r.status_code == 204)
revisar("con 'initiated'/'queued'/'ringing' NO sale ningún mensaje", len(nuevos) == 0)
revisar("mientras suena la llamada NO queda registrada con panel", SID not in s.llamadas_activas)

# --- 2) Al CONTESTAR ('in-progress') recién aparece el panel -----------------
r = c.post(
    "/voice/estado-llamada",
    data={"CallSid": SID, "CallStatus": "in-progress", "To": DESTINO},
)
revisar("'in-progress' responde 204 (sin TwiML)", r.status_code == 204)
revisar("al contestar sale EXACTAMENTE 1 mensaje (el panel)", len(nuevos) == 1)
revisar(
    "el panel trae el número de destino",
    nuevos and f"📞 Llamada · {DESTINO}" in nuevos[0]["texto"],
)
revisar(
    "el panel marca la hora de contestación",
    nuevos and "✅ Contestó:" in nuevos[0]["texto"],
)
revisar(
    "el panel sale con el botón '🛑 Colgar'",
    nuevos and tiene_colgar(nuevos[0]["teclado"], SID),
)
revisar("la llamada queda registrada (viva)", SID in s.llamadas_activas)
revisar(
    "la llamada guarda el message_id del panel",
    s.llamadas_activas[SID].get("mensaje_id") is not None,
)

# --- 3) Al arrancar la conversación se reutiliza el MISMO panel ---------------
c.post("/voice/start", data={"CallSid": SID, "To": DESTINO})
revisar("al arrancar /voice/start NO manda otro mensaje", len(nuevos) == 1)
revisar(
    "el estado del panel pasa a '🟢 en llamada'",
    editados and "🟢 en llamada" in editados[-1]["texto"],
)
revisar(
    "tras arrancar SIGUE el botón '🛑 Colgar'",
    editados and tiene_colgar(editados[-1]["teclado"], SID),
)

# --- 4) Si 'in-progress' llega DESPUÉS de /voice/start no duplica ni pisa ----
SID3 = "CAtestCONTESTA000000000000000000003"
c.post("/voice/start", data={"CallSid": SID3, "To": DESTINO})
s.llamadas_activas[SID3]["estado"] = s.ESPERANDO_1_2
antes_nuevos, antes_editados = len(nuevos), len(editados)
c.post(
    "/voice/estado-llamada",
    data={"CallSid": SID3, "CallStatus": "in-progress", "To": DESTINO},
)
revisar("'in-progress' tardío NO manda un mensaje nuevo", len(nuevos) == antes_nuevos)
revisar("'in-progress' tardío solo refresca el panel existente", len(editados) > antes_editados)

# --- 5) Un aviso tardío de 'ringing' NO pisa el flujo ya avanzado ------------
antes_editados = len(editados)
c.post(
    "/voice/estado-llamada",
    data={"CallSid": SID3, "CallStatus": "ringing", "To": DESTINO},
)
revisar("un 'ringing' tardío NO pisa el flujo ya avanzado (Fase 1/2)", len(editados) == antes_editados)

print()
if fallos:
    print(f"RESULTADO: {len(fallos)} comprobacion(es) fallaron.")
    raise SystemExit(1)
print("RESULTADO: todas las comprobaciones pasaron.")
