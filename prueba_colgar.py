"""Prueba local del botón 🛑 Colgar y del aviso de llamada contestada.

Comprueba que:
    1) el botón '🛑 Colgar' sale SIEMPRE en el panel (llamada activa y también
       en el panel de 'llamada finalizada');
    2) al pulsarlo desde Telegram se corta la llamada DE VERDAD en Twilio
       (REST: calls(sid).update(status="completed")) y se limpia el estado;
    3) cuando el usuario CONTESTA (status_callback 'in-progress'), el panel
       muestra '✅ Contestó: <hora>' y el estado '🟢 en llamada'.

No usa red ni Twilio de verdad: intercepta Telegram, la base de datos y el
cliente de Twilio.

Uso:  .venv\\Scripts\\python.exe prueba_colgar.py
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

# --- Cliente de Twilio falso: solo necesitamos calls(sid).update(...) --------
class _FakeCall:
    def __init__(self, sid: str) -> None:
        self.sid = sid
        self.updates: list[dict] = []

    def update(self, **kwargs):
        self.updates.append(kwargs)
        return self


class _FakeCalls:
    def __init__(self) -> None:
        self.por_sid: dict[str, _FakeCall] = {}

    def __call__(self, sid: str) -> _FakeCall:
        return self.por_sid.setdefault(sid, _FakeCall(sid))


class _FakeClient:
    def __init__(self) -> None:
        self.calls = _FakeCalls()


s.twilio_client = _FakeClient()

# --- Aislar Telegram y la base de datos --------------------------------------
editados: list = []  # (texto, teclado) de cada edición del panel


def _fake_editar(chat_id, message_id, texto, teclado=None):
    editados.append({"texto": texto, "teclado": teclado})


s._tg_mensaje = lambda texto, teclado=None: 1
s._tg_editar_mensaje = _fake_editar
# El reloj no se arranca en el test (evita refrescos asincronos).
s._arrancar_reloj = lambda call_sid: None
s._bd = lambda *a, **k: None
s._EN_HILO = False  # todo sincrono en pruebas (sin hilos)
s._AUTORIZAR_ACTIVO = False  # en pruebas no se comprueban admins (sin red)
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


def pulsar(data):
    c.post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "cb",
                "data": data,
                "from": {"username": "tester"},
                "message": {
                    "chat": {"id": int(cfg.TELEGRAM_GROUP_CHAT_ID)},
                    "message_id": 1,
                },
            }
        },
    )


# --- 0) _con_colgar: el botón siempre está y no se duplica -------------------
revisar(
    "_con_colgar agrega el botón cuando el teclado viene vacío",
    tiene_colgar(s._con_colgar("CAX", []), "CAX"),
)
revisar(
    "_con_colgar NO duplica el botón si ya viene",
    sum(
        1
        for fila in s._con_colgar(
            "CAX", [[{"text": "🛑 Colgar", "callback_data": "colgar_CAX"}]]
        )
        for boton in fila
        if (boton or {}).get("callback_data") == "colgar_CAX"
    )
    == 1,
)

# --- 1) Llamada activa: el panel trae el botón Colgar ------------------------
SID = "CAtestCOLGAR000000000000000000001"
c.post("/voice/start", data={"CallSid": SID, "To": "+573000000001"})
s._tg_panel(SID)
revisar("con la llamada activa el panel trae '🛑 Colgar'", tiene_colgar(editados[-1]["teclado"], SID))

# --- 2) Al CONTESTAR (in-progress) se avisa y se sigue pudiendo colgar -------
r = c.post(
    "/voice/estado-llamada",
    data={"CallSid": SID, "CallStatus": "in-progress"},
)
revisar("al contestar Twilio responde 204 (sin TwiML)", r.status_code == 204)
revisar("el panel avisa '✅ Contestó: <hora>'", "✅ Contestó:" in editados[-1]["texto"])
revisar(
    "el estado dice '🟢 en llamada'",
    "🟢 en llamada" in editados[-1]["texto"],
)
revisar("tras contestar SIGUE el botón '🛑 Colgar'", tiene_colgar(editados[-1]["teclado"], SID))
revisar(
    "la llamada queda marcada como contestada",
    s.llamadas_activas[SID].get("contestada") is True,
)

# --- 3) Pulsar '🛑 Colgar' corta la llamada en Twilio ------------------------
pulsar(f"colgar_{SID}")
revisar(
    "al pulsar Colgar se pide a Twilio terminar la llamada (status=completed)",
    {"status": "completed"} in s.twilio_client.calls.por_sid[SID].updates,
)
revisar("al colgar la llamada se limpia del estado", SID not in s.llamadas_activas)

# --- 4) El panel de 'llamada finalizada' conserva el botón Colgar -----------
SID2 = "CAtestCOLGAR200000000000000000002"
c.post("/voice/start", data={"CallSid": SID2, "To": "+573000000001"})
c.post(
    "/voice/estado-llamada",
    data={"CallSid": SID2, "CallStatus": "completed"},
)
revisar(
    "el estado final queda en rojo ('🔴 colgado')",
    "🔴 colgado" in editados[-1]["texto"],
)
revisar(
    "el panel explica que la llamada terminó (completed)",
    "la llamada terminó (completed)" in editados[-1]["texto"],
)
revisar(
    "el panel de 'llamada finalizada' AÚN trae el botón '🛑 Colgar'",
    tiene_colgar(editados[-1]["teclado"], SID2),
)

print()
if fallos:
    print(f"RESULTADO: {len(fallos)} comprobacion(es) fallaron.")
    raise SystemExit(1)
print("RESULTADO: todas las comprobaciones pasaron.")