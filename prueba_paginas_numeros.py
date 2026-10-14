"""Prueba de las páginas de números y del comando '/llamar <tel>'.

Comprueba que:
    1) /numeros muestra SOLO 10 números por página, con su número de página y
       botones ⬅️/➡️ para moverse (con muchos números un solo mensaje no cabe);
    2) los números conservan su posición REAL en la lista (página 2 empieza en 11);
    3) pasando la página al comando ('/numeros 2') se abre esa página;
    4) el menú de /llamar también va por páginas de 10 botones;
    5) el botón 📄 manda la lista COMPLETA en un .txt (así se ven todos de golpe);
    6) '/llamar +573000000004' llama DE UNA VEZ, sin pasar por el menú;
    7) '/llamar' con un número que NO está en la lista avisa y no llama.

No usa red ni Twilio: intercepta Telegram, Twilio y la base de datos, y trabaja
sobre un numeros.txt temporal.

Uso:  .venv\\Scripts\\python.exe prueba_paginas_numeros.py
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
import servidor as s  # noqa: E402

# --- 25 números de prueba en un numeros.txt temporal -------------------------
tmp = Path(tempfile.mkdtemp()) / "numeros.txt"
cfg.ARCHIVO_NUMEROS = tmp
NUMEROS = [f"+5730000{i:04d}" for i in range(1, 26)]
cfg.guardar_numeros(NUMEROS)

# --- Interceptar Telegram, Twilio y la base de datos -------------------------
enviados: list = []  # mensajes NUEVOS (texto, teclado)
editados: list = []  # ediciones del mismo mensaje (texto, teclado)
documentos: list = []  # archivos (nombre, contenido, pie)


def _fake_mensaje(texto, teclado=None):
    enviados.append({"texto": texto, "teclado": teclado})
    return 1000 + len(enviados)


def _fake_editar(chat_id, message_id, texto, teclado=None):
    editados.append({"texto": texto, "teclado": teclado})


def _fake_documento(nombre, contenido, pie=""):
    documentos.append({"nombre": nombre, "contenido": contenido, "pie": pie})


s._tg_mensaje = _fake_mensaje
s._tg_editar_mensaje = _fake_editar
s._tg_documento = _fake_documento
s._arrancar_reloj = lambda call_sid: None
s.bd = types.SimpleNamespace(
    ultimas=lambda *a, **k: [],
    resumen=lambda *a, **k: "",
    nueva_llamada=lambda *a, **k: None,
    actualizar=lambda *a, **k: None,
    registrar_evento=lambda *a, **k: None,
    cerrar=lambda *a, **k: None,
)


class _FakeCalls:
    def __init__(self):
        self.creados: list = []

    def create(self, **kwargs):
        self.creados.append(kwargs)
        return types.SimpleNamespace(sid="CAfakePAG000000000000000000000001")


class _FakeClient:
    def __init__(self):
        self.calls = _FakeCalls()


s.twilio_client = _FakeClient()
s._crear_llamada = lambda destino, base: s.twilio_client.calls.create(
    to=destino, from_=cfg.TWILIO_NUMBER, url=f"{base}/voice/start"
)

c = s.app.test_client()
s._AUTORIZAR_ACTIVO = False  # en pruebas no se comprueban admins (sin red)
CHAT = int(cfg.TELEGRAM_GROUP_CHAT_ID)
fallos = []


def revisar(desc, cond):
    print(f"[{'OK  ' if cond else 'FALLO'}] {desc}")
    if not cond:
        fallos.append(desc)


def mandar(texto):
    c.post(
        "/telegram/webhook",
        json={
            "message": {
                "chat": {"id": CHAT},
                "text": texto,
                "from": {"username": "tester"},
            }
        },
    )


def pulsar(data):
    c.post(
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


def botones(teclado):
    return [b for fila in (teclado or []) for b in fila]


# --- 0) Los números se parten en grupos de 10 --------------------------------
revisar("NUMEROS_POR_PAGINA = 10", cfg.NUMEROS_POR_PAGINA == 10)
revisar("25 números dan 3 páginas", s._total_paginas(NUMEROS) == 3)
revisar("la página 1 trae 10 números", len(s._tramo_pagina(NUMEROS, 1)) == 10)
revisar("los tramos NO se solapan",
        not (set(s._tramo_pagina(NUMEROS, 1)) & set(s._tramo_pagina(NUMEROS, 2))))
revisar("la última página trae los 5 que quedan (25 = 10+10+5)",
        len(s._tramo_pagina(NUMEROS, 3)) == 5)
revisar("una página fuera de rango se ajusta a la última",
        s._tramo_pagina(NUMEROS, 99) == NUMEROS[-5:])

# --- 1) /numeros: solo 10 por mensaje + aviso de página ----------------------
enviados.clear()
mandar("/numeros")
texto, teclado = enviados[-1]["texto"], enviados[-1]["teclado"]
revisar("/numeros lista justo 10 números (no 25)",
        sum(1 for ln in texto.splitlines() if ln.split(".")[0].isdigit()) == 10)
revisar("/numeros dice en qué página estamos", "página 1/3" in texto)
revisar("/numeros numera desde el 1", "1. +57300000001" in texto)
revisar("/numeros NO enseña aún el número 11 (es de la página 2)",
        "+57300000011" not in texto)
etiquetas = [b["text"] for b in botones(teclado)]
datos = [b["callback_data"] for b in botones(teclado)]
revisar("en la 1.ª página NO hay botón 'Anterior'",
        not any("Anterior" in e for e in etiquetas))
revisar("en la 1.ª página SÍ hay botón 'Siguiente'", "numeros_2" in datos)
revisar("hay UN solo botón para ver TODOS los números",
        datos.count("vertodos_todos") == 1)

# --- 2) Al pasar de página se ven los siguientes 10 -------------------------
editados.clear()
pulsar("numeros_2")
texto, teclado = editados[-1]["texto"], editados[-1]["teclado"]
revisar("la página 2 se edita en el MISMO mensaje (sin duplicar)", len(enviados) == 1)
revisar("la página 2 dice 'página 2/3'", "página 2/3" in texto)
revisar("la página 2 empieza en el número 11 (posición real)",
        "11. +57300000011" in texto)
revisar("en la página 2 hay botón 'Anterior'",
        "numeros_1" in [b["callback_data"] for b in botones(teclado)])
revisar("al paginar sigue habiendo UN solo botón 📄 (no se duplica)",
        [b["callback_data"] for b in botones(teclado)].count("vertodos_todos") == 1)

# --- 4) El menú de /llamar también va por páginas (10 botones) --------------
enviados.clear()
mandar("/llamar")
texto, teclado = enviados[-1]["texto"], enviados[-1]["teclado"]
revisar("/llamar avisa en qué página está", "página 1/3" in texto)
botones_llamar = [b for b in botones(teclado) if b["callback_data"].startswith("llamar_+")]
revisar("/llamar ofrece 10 números (un botón por número)", len(botones_llamar) == 10)
revisar("/llamar incluye botón de cancelar",
        "llamar_cancelar" in [b["callback_data"] for b in botones(teclado)])
revisar("/llamar incluye botón 'Siguiente'",
        "menu_2" in [b["callback_data"] for b in botones(teclado)])

editados.clear()
pulsar("menu_2")
texto, teclado = editados[-1]["texto"], editados[-1]["teclado"]
revisar("la 2.ª página del menú dice 'página 2/3'", "página 2/3" in texto)
revisar("la 2.ª página del menú arranca en el número 11",
        any(b["callback_data"] == "llamar_+57300000011" for b in botones(teclado)))

# --- 5) 📄 Ver todos manda la lista COMPLETA en un .txt ----------------------
documentos.clear()
pulsar("vertodos_todos")
revisar("el botón 📄 manda un archivo .txt", len(documentos) == 1)
revisar("el .txt se llama numeros-nova.txt",
        documentos and documentos[0]["nombre"] == "numeros-nova.txt")
revisar("el .txt trae los 25 números (todos, sin paginar)",
        documentos and len(documentos[0]["contenido"].splitlines()) == 25)

# --- 6) '/llamar +573000000004' llama DE UNA VEZ ----------------------------
cfg.guardar_numeros(NUMEROS + ["+573000000004"])
enviados.clear()
s.twilio_client.calls.creados.clear()
mandar("/llamar +573000000004")
revisar("'/llamar <tel>' crea la llamada sin pasar por el menú",
        len(s.twilio_client.calls.creados) == 1)
revisar("llama al número escrito",
        s.twilio_client.calls.creados
        and s.twilio_client.calls.creados[0]["to"] == "+573000000004")
revisar("'llamar <tel>' NO manda el menú (ninguna lista de números)",
        not any(
            b["callback_data"].startswith("llamar_+")
            for e in enviados
            for b in botones(e["teclado"])
        ))
revisar("'llamar <tel>' NO abre el panel todavía (solo sale al contestar)", not any(
    b["callback_data"].startswith("colgar_")
    for e in enviados
    for b in botones(e["teclado"])
))
revisar("avisa en Telegram a quién se llamó",
        any("+573000000004" in e["texto"] for e in enviados))

# --- 7) '/llamar <tel>' acepta el número sin el '+' --------------------------
s.twilio_client.calls.creados.clear()
mandar("/llamar 573000000004")
revisar("'/llamar 573000000004' normaliza el número y llama",
        s.twilio_client.calls.creados
        and s.twilio_client.calls.creados[0]["to"] == "+573000000004")

# --- 8) Un número que NO está en la lista: avisa y no llama -----------------
enviados.clear()
s.twilio_client.calls.creados.clear()
mandar("/llamar +573999999999")
revisar("'/llamar <tel>' con número desconocido NO llama",
        not s.twilio_client.calls.creados)
revisar("avisa de que no está en la lista",
        any("no está en la lista" in e["texto"] for e in enviados))

print()
if fallos:
    print(f"RESULTADO: {len(fallos)} comprobacion(es) fallaron.")
    raise SystemExit(1)
print("RESULTADO: todas las comprobaciones pasaron.")