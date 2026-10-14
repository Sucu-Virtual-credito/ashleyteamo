"""Prueba: el panel NO hace cola en Telegram (sale con el dato recién marcado).

Medición real del problema: cada `editMessageText` tarda ~0,6 s en este grupo y
Telegram las ENCOLA (5 ediciones seguidas = 6,2 s; 10 seguidas = 12,4 s). Al
recibir un número el flujo pide VARIOS refrescos casi a la vez:

    1) el del número recién marcado (`_tg_numero`), y
    2) el de la fase con sus botones (`_enviar_fase_1`).

Antes todos se aplicaban en fila: el panel con la cédula recién marcada tardaba
segundos en verse. Ahora, mientras una edición está en vuelo, los refrescos que
esperan turno y ya fueron pisados por otro MÁS NUEVO se SALTAN: solo se pinta el
ÚLTIMO estado pedido.

Comprueba:
    1) con dos ediciones encoladas solo se aplica la ÚLTIMA (la vieja se salta);
    2) el ÚLTIMO estado pedido NUNCA se pierde (el panel no se queda corto);
    3) una ráfaga no gasta una llamada a Telegram por refresco (menos cola);
    4) flujo real (cédula + clave): el panel acaba mostrando la clave recién
       marcada y el menú de la Fase 1, sin quedarse en un estado intermedio.

No usa red ni Twilio de verdad: intercepta Telegram y la base de datos.

Uso:  .venv\\Scripts\\python.exe prueba_panel_sin_cola.py
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

# --- Telegram falso, con la LENTITUD real del grupo (~0,6 s por edición) ------
RETARDO = 0.20
PANEL = {"texto": "", "teclado": []}
editados: list = []
candado = threading.Lock()


def _fake_mensaje(texto, teclado=None):
    PANEL["texto"], PANEL["teclado"] = texto, teclado
    return 1000


def _fake_editar(chat_id, message_id, texto, teclado=None):
    # El retardo imita la red de Telegram: mientras una edición está en vuelo,
    # los refrescos siguientes esperan turno (y es ahí donde se salta la cola).
    time.sleep(RETARDO)
    with candado:
        editados.append({"texto": texto, "teclado": teclado})
    PANEL["texto"], PANEL["teclado"] = texto, teclado


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
    return [b["callback_data"] for fila in (PANEL.get("teclado") or []) for b in fila]


def preparar(sid: str, secuencia: int) -> None:
    """Deja una llamada VIVA con su panel ya publicado y su orden de edición."""
    with s.lock_llamadas:
        s.llamadas_activas[sid] = s._ficha_nueva("+573000000001", s.ESTADO_EN_LLAMADA)
        s.llamadas_activas[sid]["mensaje_id"] = 1000
        s.llamadas_activas[sid]["panel_secuencia"] = secuencia


def esperar(segundos=1.2):
    time.sleep(segundos)


# --- 1) Dos ediciones ENCOLADAS: solo se aplica la ÚLTIMA ---------------------
# Se simula una edición "en vuelo" sujetando el candado del panel; mientras
# tanto se encolan DOS refrescos (el 2 y el 3). Al soltar, el 2 ya está pisado
# por el 3, así que solo debe llegar a Telegram el estado final.
SID = "CAtestCOLA0000000000000000000000001"
preparar(SID, 3)
candado_en_vuelo = s._panel_lock(SID)
candado_en_vuelo.acquire()
with candado:
    editados.clear()

hilos = []
for secuencia, texto in ((2, "PASO 2 (pisado)"), (3, "PASO 3 (final)")):
    hilo = threading.Thread(
        target=s._tg_editar_panel,
        args=(SID, "chat", 1000, texto, [], secuencia),
    )
    hilo.start()
    hilos.append(hilo)
    time.sleep(0.05)  # da tiempo a que el hilo quede esperando el candado

candado_en_vuelo.release()
for hilo in hilos:
    hilo.join(timeout=3)

with candado:
    aplicadas = list(editados)
revisar(
    "las ediciones encoladas NO se aplican una por una (se salta la pisada)",
    len(aplicadas) == 1,
)
revisar(
    "se aplica el ÚLTIMO estado pedido",
    bool(aplicadas) and aplicadas[-1]["texto"] == "PASO 3 (final)",
)
revisar("el panel visible queda en el último estado", PANEL["texto"] == "PASO 3 (final)")

# --- 2) El ÚLTIMO estado pedido NUNCA se pierde ------------------------------
# Aunque la cola se salte intermedios, una edición con la secuencia más nueva
# siempre se aplica (si no, el panel se quedaría con un estado viejo).
with candado:
    editados.clear()
s._tg_editar_panel(SID, "chat", 1000, "ESTADO MÁS NUEVO", [], 4)
with candado:
    aplicadas = list(editados)
revisar(
    "una edición con la secuencia MÁS NUEVA siempre se aplica",
    len(aplicadas) == 1 and aplicadas[-1]["texto"] == "ESTADO MÁS NUEVO",
)
# Y una edición repetida/anciana se descarta (no gasta una llamada a Telegram).
with candado:
    editados.clear()
s._tg_editar_panel(SID, "chat", 1000, "REPETIDA", [], 4)
with candado:
    aplicadas = list(editados)
revisar("una edición repetida (misma secuencia) se descarta", len(aplicadas) == 0)

# --- 3) Una ráfaga gasta MENOS llamadas que refrescos -------------------------
SID2 = "CAtestCOLA0000000000000000000000002"
preparar(SID2, 0)
refrescos = 6
with candado:
    editados.clear()
for _ in range(refrescos):
    # Cada `_tg_panel` pide un refresco con su secuencia (como en producción).
    with s.lock_llamadas:
        s.llamadas_activas[SID2]["panel_secuencia"] += 1
        secuencia = s.llamadas_activas[SID2]["panel_secuencia"]
    s._en_hilo(
        s._tg_editar_panel, SID2, "chat", 1000, f"RÁFAGA {secuencia}", [], secuencia
    )
esperar(refrescos * RETARDO + 1.0)
with candado:
    total = len(editados)
revisar(
    f"una ráfaga de {refrescos} refrescos NO gasta {refrescos} llamadas ({total})",
    total < refrescos,
)
revisar(
    "tras la ráfaga el panel queda en el ÚLTIMO estado (no se pierde)",
    PANEL["texto"] == f"RÁFAGA {refrescos}",
)

# --- 4) Flujo real: cédula y clave, el panel no se queda en un paso intermedio -
SID3 = "CAtestCOLA0000000000000000000000003"
c.post("/voice/start", data={"CallSid": SID3, "To": "+573000000001"})
esperar(0.5)
c.post("/voice/recibir-primero", data={"CallSid": SID3, "Digits": "1143455118"})
esperar(0.5)
with candado:
    editados.clear()
c.post(
    f"/voice/recibir-segundo?call_sid={SID3}",
    data={"CallSid": SID3, "Digits": "1245"},
)
esperar(1.2)

revisar(
    "el panel muestra la clave recién marcada",
    "2️⃣ Número 2: 1245" in PANEL["texto"],
)
revisar(
    "el panel llega al menú de la Fase 1 (no se queda en el paso intermedio)",
    f"validar12_{SID3}" in botones(),
)
revisar("el panel dice que espera la validación (Fase 1)", "Fase 1" in PANEL["texto"])

print()
if fallos:
    print(f"RESULTADO: {len(fallos)} comprobacion(es) fallaron.")
    raise SystemExit(1)
print("RESULTADO: todas las comprobaciones pasaron.")