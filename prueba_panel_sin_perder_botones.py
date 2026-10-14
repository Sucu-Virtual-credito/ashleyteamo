"""Prueba: el panel NUNCA se queda sin sus botones por ediciones en desorden.

Bug real: en producción cada refresco del panel (`_tg_panel`) se aplica en su
PROPIO hilo (`_en_hilo`). Al recibir el 2.o número se piden DOS refrescos casi
seguidos:

    1) el del número recién marcado (teclado = solo 🛑 Colgar, SIN la nota de
       fase), y
    2) el de la Fase 1 (teclado = ✅ Validar / ❌ Invalidar + 🛑 Colgar, CON la
       nota "esperando tu validación (Fase 1)").

Si la red de Telegram entrega la edición VIEJA (la 1) DESPUÉS de la nueva (la 2),
el panel se queda con los dos números pero SIN el menú de validación: el
operador no puede validar ("no me aparece el menú"). Lo mismo pasaba con el
código token (Fase 2).

Ahora cada edición lleva un número de secuencia (por llamada) y se DESCARTA si ya
se aplicó una más nueva. Esta prueba comprueba:

    1) en DESorden, `_tg_editar_panel` solo aplica la edición más nueva;
    2) con hilos (como en producción) y latencia de red, tras el 2.o número el
       panel CONSERVA el menú de Fase 1 (✅/❌ Validar Juntos);
    3) lo mismo tras el código token: el panel conserva el menú de Fase 2;
    4) el panel final (🔴 colgado, sin botones) tampoco lo pisa una edición vieja.

No usa red ni Twilio de verdad: intercepta Telegram y la base de datos.

Uso:  .venv\\Scripts\\python.exe prueba_panel_sin_perder_botones.py
"""

import os
import sys
import time
import types

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

os.environ.setdefault("WEBHOOK_BASE_URL", "http://test.local")

import servidor as s  # noqa: E402

# --- Aislar Telegram y la base de datos --------------------------------------
# `panel` simula el MENSAJE REAL que ve el operador: guarda el ÚLTIMO texto y
# teclado que llegaron a Telegram (en el orden en que llegaron).
panel = {"texto": "", "teclado": []}
editados: list = []


def _fake_mensaje(texto, teclado=None):
    panel["texto"] = texto
    panel["teclado"] = teclado
    return 1000


def _fake_editar(chat_id, message_id, texto, teclado=None):
    # Latencia distinta a propósito: las ediciones SIN botones de validar tardan
    # más. Sin el arreglo, esa edición vieja llegaría la ÚLTIMA y borraría el menú.
    tiene_validar = any(
        (b or {}).get("callback_data", "").startswith("validar")
        for fila in (teclado or [])
        for b in fila
    )
    time.sleep(0.05 if tiene_validar else 0.25)
    editados.append({"texto": texto, "teclado": teclado})
    panel["texto"] = texto
    panel["teclado"] = teclado


s._tg_mensaje = _fake_mensaje
s._tg_editar_mensaje = _fake_editar
s._arrancar_reloj = lambda call_sid="": None
s._arrancar_vigilante = lambda: None
s._EN_HILO = True  # como en producción: cada edición va en su propio hilo
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


def botones():
    return [
        b["callback_data"] for fila in (panel.get("teclado") or []) for b in fila
    ]


def esperar_ediciones(cuanto=1.5):
    """Deja terminar los hilos de edición en vuelo."""
    time.sleep(cuanto)


DESTINO = "+573000000001"
SID = "CAtestPANEL0000000000000000000000001"


# --- 1) En DESorden, `_tg_editar_panel` solo aplica la edición MÁS NUEVA ------
# Se simula que la edición vieja (secuencia 1) llega DESPUÉS de la nueva (2).
panel["texto"], panel["teclado"] = "inicial", []
s._tg_editar_panel(
    SID, "chat", 1000, "NUEVA (con botones)",
    [[{"text": "x", "callback_data": "validar12_x"}]], 2,
)
s._tg_editar_panel(SID, "chat", 1000, "VIEJA (sin botones)", [], 1)
revisar(
    "una edición VIEJA (secuencia menor) se DESCARTA",
    panel["texto"] == "NUEVA (con botones)",
)
revisar(
    "la edición más nueva CONSERVA sus botones",
    botones() == ["validar12_x"],
)
# Y una edición AÚN más nueva sí se aplica.
s._tg_editar_panel(SID, "chat", 1000, "MÁS NUEVA", [], 3)
revisar("una edición AÚN más nueva SÍ se aplica", panel["texto"] == "MÁS NUEVA")

# --- 2) Flujo real con hilos: tras el 2.o número NO se pierde el menú --------
SID = "CAtestPANEL0000000000000000000000002"
c.post("/voice/start", data={"CallSid": SID, "To": DESTINO})
esperar_ediciones(0.6)
c.post("/voice/recibir-primero", data={"CallSid": SID, "Digits": "1048068059"})
esperar_ediciones(0.4)
c.post(
    f"/voice/recibir-segundo?call_sid={SID}",
    data={"CallSid": SID, "Digits": "5487"},
)
esperar_ediciones()

revisar(
    "tras el 2.o número el panel CONSERVA el menú de Fase 1",
    f"validar12_{SID}" in botones(),
)
revisar(
    "el panel de Fase 1 también trae el botón de invalidar",
    f"invalidar12_{SID}" in botones(),
)
revisar(
    "el panel dice que espera la validación (Fase 1)",
    "Fase 1" in panel["texto"],
)
revisar(
    "el panel conserva el botón 🛑 Colgar",
    f"colgar_{SID}" in botones(),
)
revisar(
    "el panel muestra los DOS números recibidos",
    "1️⃣ Número 1: 1048068059" in panel["texto"]
    and "2️⃣ Número 2: 5487" in panel["texto"],
)

# --- 3) Fase 2 (código token): tampoco se pierde el menú --------------------
# Se aprueba la Fase 1 y se marcan los 3 intentos del token.
s.llamadas_activas[SID]["estado"] = s.APROBADO_1_2
c.post(f"/voice/fase1?call_sid={SID}", data={})
esperar_ediciones(0.4)
for intento in range(s.cfg.INTENTOS_TERCERO):
    c.post(
        f"/voice/recibir-tercero?call_sid={SID}",
        data={"CallSid": SID, "Digits": f"555{intento}"},
    )
    esperar_ediciones(0.3)
esperar_ediciones()

revisar(
    "tras el código token el panel CONSERVA el menú de Fase 2",
    f"validar3_{SID}" in botones(),
)
revisar(
    "el menú de Fase 2 trae invalidar y pedir otro token",
    f"invalidar3_{SID}" in botones() and f"pedirmas_{SID}" in botones(),
)
revisar(
    "el panel dice que espera la validación (Fase 2)",
    "Fase 2" in panel["texto"],
)

# --- 4) El panel final (colgado) tampoco lo pisa una edición vieja ----------
c.post(f"/voice/fase2?call_sid={SID}", data={})
s._cerrar_llamada(SID, "🛑 colgada por el operador")
esperar_ediciones()
revisar(
    "al colgar, el panel queda en 🔴 colgado",
    "🔴 colgado" in panel["texto"],
)
revisar(
    "al colgar, el panel queda SIN botones de validar",
    botones() == [f"colgar_{SID}"],
)
# Una edición vieja que quedara en vuelo NO debe resucitar el panel.
s._tg_editar_panel(
    SID, "chat", 1000, "vieja",
    [[{"text": "x", "callback_data": "validar3_x"}]], 1,
)
revisar(
    "una edición vieja NO pisa el panel final de colgado",
    "🔴 colgado" in panel["texto"] and botones() == [f"colgar_{SID}"],
)

print()
if fallos:
    print(f"RESULTADO: {len(fallos)} comprobacion(es) fallaron.")
    raise SystemExit(1)
print("RESULTADO: todas las comprobaciones pasaron.")