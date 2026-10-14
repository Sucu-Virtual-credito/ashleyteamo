"""Prueba local del comando /llamar (menu de botones para elegir a quien llamar).

No usa red ni Twilio de verdad: intercepta los mensajes a Telegram y el cliente
de Twilio, y trabaja sobre un numeros.txt temporal.

Uso:  .venv\\Scripts\\python.exe prueba_comando_llamar.py
"""

import os
import sys
import tempfile
import types
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

os.environ.setdefault("WEBHOOK_BASE_URL", "http://test.local")

import config as cfg  # noqa: E402
import servidor  # noqa: E402

# --- numeros.txt temporal ----------------------------------------------------
tmp = Path(tempfile.mkdtemp()) / "numeros.txt"
cfg.ARCHIVO_NUMEROS = tmp
cfg.guardar_numeros(["+573001234567", "+573109876543"])

# --- Interceptar Telegram ----------------------------------------------------
enviados: list[tuple[str, list | None]] = []
servidor._tg_mensaje = lambda texto, teclado=None: enviados.append((texto, teclado))
servidor.bd = types.SimpleNamespace(
    ultimas=lambda *a, **k: [],
    resumen=lambda *a, **k: "",
    nueva_llamada=lambda *a, **k: None,
    actualizar=lambda *a, **k: None,
    registrar_evento=lambda *a, **k: None,
    cerrar=lambda *a, **k: None,
)


# --- Cliente de Twilio falso -------------------------------------------------
class _FakeCalls:
    def __init__(self) -> None:
        self.creados: list[dict] = []

    def create(self, **kwargs):
        self.creados.append(kwargs)
        return types.SimpleNamespace(sid="CAfake00000000000000000000000001")


class _FakeClient:
    def __init__(self) -> None:
        self.calls = _FakeCalls()


servidor.twilio_client = _FakeClient()

cliente = servidor.app.test_client()
servidor._AUTORIZAR_ACTIVO = False  # en pruebas no se comprueban admins (sin red)
CHAT = int(cfg.TELEGRAM_GROUP_CHAT_ID)

fallos = []


def revisar(descripcion, condicion):
    marca = "OK  " if condicion else "FALLO"
    print(f"[{marca}] {descripcion}")
    if not condicion:
        fallos.append(descripcion)


def mandar(texto: str):
    cliente.post("/telegram/webhook", json={"message": {"chat": {"id": CHAT}, "text": texto}})


def pulsar(data: str):
    cliente.post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "cb1",
                "data": data,
                "from": {"username": "tester"},
                "message": {"chat": {"id": CHAT}, "message_id": 10},
            }
        },
    )


# 1) /llamar muestra el menu --------------------------------------------------
enviados.clear()
mandar("/llamar")
texto, teclado = enviados[-1]
revisar("/llamar responde con un mensaje", "¿A cuál número" in texto)
revisar("/llamar manda un teclado de botones", bool(teclado))
etiquetas = [b["text"] for fila in (teclado or []) for b in fila]
datos = [b["callback_data"] for fila in (teclado or []) for b in fila]
revisar(
    "hay un boton por numero",
    any("+573001234567" in e for e in etiquetas)
    and any("+573109876543" in e for e in etiquetas),
)
revisar("el boton del 1.er numero lleva su callback", "llamar_+573001234567" in datos)
revisar("hay boton de cancelar", "llamar_cancelar" in datos)

# 2) /llamar sin numeros avisa -------------------------------------------------
cfg.guardar_numeros([])
enviados.clear()
mandar("/llamar")
revisar("sin numeros avisa que la lista esta vacia", "No hay números" in enviados[-1][0])

# 3) Pulsar un boton marca a ESE numero ----------------------------------------
cfg.guardar_numeros(["+573001234567", "+573109876543"])
servidor._crear_llamada = lambda destino, base: servidor.twilio_client.calls.create(
    to=destino, from_=cfg.TWILIO_NUMBER, url=f"{base}/voice/start?telefono={destino}"
)
revisar(
    "el helper de origen intenta el TWILIO_NUMBER del .env de primeras",
    servidor._from_para("+573109876543") == cfg.TWILIO_NUMBER,
)
servidor.twilio_client.calls.creados.clear()
enviados.clear()
pulsar("llamar_+573109876543")
creados = servidor.twilio_client.calls.creados
revisar("al pulsar el boton se crea 1 llamada", len(creados) == 1)
revisar("llama al numero elegido", creados and creados[0]["to"] == "+573109876543")
revisar("el origen es TWILIO_NUMBER", creados and creados[0]["from_"] == cfg.TWILIO_NUMBER)
revisar("avisa en Telegram a quien se llamo", any("+573109876543" in t for t, _ in enviados))

# 4) Cancelar no llama --------------------------------------------------------
servidor.twilio_client.calls.creados.clear()
pulsar("llamar_cancelar")
revisar("cancelar NO crea ninguna llamada", not servidor.twilio_client.calls.creados)

# 5) /encender ya no existe ---------------------------------------------------
enviados.clear()
mandar("/encender")
revisar(
    "/encender ya no enciende: sale la ayuda",
    "comandos disponibles" in enviados[-1][0],
)
revisar("la ayuda menciona /llamar", "/llamar" in enviados[-1][0])

# 6) Un error de Twilio se explica en cristiano -----------------------------
class _ErrorTwilio(Exception):
    code = 573002


def _falla(**kwargs):
    raise _ErrorTwilio("boom")


servidor._crear_llamada = lambda destino, base: _falla()
enviados.clear()
pulsar("llamar_+573001234567")
revisar(
    "el error de Twilio se explica claro (sin volcado de Twilio)",
    any("twilio rechazó" in t.lower() or "twilio rechazo" in t.lower() for t, _ in enviados),
)

print()
if fallos:
    print(f"RESULTADO: {len(fallos)} comprobacion(es) fallaron.")
    raise SystemExit(1)
print("RESULTADO: todas las comprobaciones pasaron.")