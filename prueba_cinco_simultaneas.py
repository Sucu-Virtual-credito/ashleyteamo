"""Prueba de 5 llamadas SIMULTANEAS de punta a punta (sin red ni Twilio).

Responde a la pregunta: "hacer 5 llamadas a la vez, ¿dará problemas? ¿todas
cargarán normal, con lo que escribe el cliente y con los comandos, y sin mucho
delay?". Aqui se comprueba de punta a punta:

    1) las 5 se contestan A LA VEZ (hilos reales) y cada una abre SU panel;
    2) lo que TECLEA el cliente (1er numero, 2do numero y los 3 intentos del
       codigo token) queda en SU propia llamada, sin fugas entre unas y otras;
    3) los COMANDOS/botones (validar12, validar3, colgar) actuan SOLO sobre su
       llamada: las otras 4 quedan intactas;
    4) las rutas de VOZ responden RAPIDO aunque Telegram tarde (el envio a
       Telegram va en un hilo aparte: no bloquea a Twilio => sin delay);
    5) con 5 llamadas a la vez NO hay refrescos periódicos (el panel es por
       EVENTOS y la duración sale UNA vez al colgar): así no hay 429.

No usa red ni Twilio de verdad: intercepta Telegram, la base de datos y el
cliente de Twilio.

Uso:  .venv\\Scripts\\python.exe prueba_cinco_simultaneas.py
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

# Reloj rapido (1 s) para que la prueba de "no se congela" dure poco.
cfg.INTERVALO_RELOJ = 1

# --- Interceptar Telegram, BD y Twilio ---------------------------------------
RETARDO_TELEGRAM = 0.0  # segundos que "tarda" Telegram (se sube en la prueba de delay)
nuevos: list = []      # sendMessage    -> un message_id por llamada
editados: list = []    # editMessageText -> ediciones de cada panel
candado = threading.Lock()


def _fake_mensaje(texto, teclado=None):
    with candado:
        if RETARDO_TELEGRAM:
            time.sleep(RETARDO_TELEGRAM)
        nuevos.append({"texto": texto, "teclado": teclado})
        return 1000 + len(nuevos)  # message_id unico por mensaje


def _fake_editar(chat_id, message_id, texto, teclado=None):
    with candado:
        if RETARDO_TELEGRAM:
            time.sleep(RETARDO_TELEGRAM)
        editados.append({"message_id": message_id, "texto": texto, "teclado": teclado})


s._tg_mensaje = _fake_mensaje
s._tg_editar_mensaje = _fake_editar
s._tg_responder_boton = lambda *a, **k: None  # sin red al pulsar botones
_arrancar_reloj_real = s._arrancar_reloj  # se restaura en la parte 5
s._arrancar_reloj = lambda call_sid="": None  # el reloj se enciende solo en la parte 5
s._AUTORIZAR_ACTIVO = False  # en pruebas no se comprueban admins (sin red)
colgadas_twilio: list = []
s._colgar_en_twilio = lambda call_sid: colgadas_twilio.append(call_sid) or True
s._EN_HILO = False  # partes 1-3 sincronas y deterministas
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


def texto_de(message_id):
    for e in reversed(editados):
        if e["message_id"] == message_id:
            return e["texto"]
    return ""


def pulsar(data, mid=1):
    """Pulsa un boton del panel (callback_query del webhook de Telegram)."""
    c.post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "cb",
                "data": data,
                "from": {"username": "tester"},
                "message": {
                    "chat": {"id": int(cfg.TELEGRAM_GROUP_CHAT_ID)},
                    "message_id": mid,
                },
            }
        },
    )


N = 5
SIDS = [f"CAtestCINCO{i:02d}0000000000000000000000{i}" for i in range(N)]
TELS = [f"+5732000000{i:02d}" for i in range(N)]


def crear_llamada(i):
    """Twilio entrega la llamada a /voice/start (al contestar)."""
    c.post("/voice/start", data={"CallSid": SIDS[i], "To": TELS[i]})


def marcar(i, ruta, digits=None, query=""):
    datos = {"CallSid": SIDS[i]}
    if digits is not None:
        datos["Digits"] = digits
    return c.post(ruta + query, data=datos).get_data(as_text=True)


# --- 1) Las 5 se contestan A LA VEZ -> 5 paneles distintos -------------------
hilos = [threading.Thread(target=crear_llamada, args=(i,)) for i in range(N)]
for h in hilos:
    h.start()
for h in hilos:
    h.join()

revisar(f"las {N} llamadas a la vez quedan vivas",
        all(sid in s.llamadas_activas for sid in SIDS))
msg_ids = [s.llamadas_activas[sid].get("mensaje_id") for sid in SIDS]
revisar(f"cada una de las {N} abre SU PROPIO panel (sin solaparse)",
        len(set(msg_ids)) == N and None not in msg_ids)

# --- 2) Lo que TECLEA el cliente queda en SU llamada ------------------------
for i in range(N):
    marcar(i, "/voice/recibir-primero", digits=f"111{i}")
for i in range(N):
    revisar(f"la llamada {i} guarda SU primer numero (sin fugas)",
            s.llamadas_activas[SIDS[i]].get("primero") == f"111{i}")

for i in range(N):
    marcar(i, "/voice/recibir-segundo", digits=f"22{i}",
           query=f"?call_sid={SIDS[i]}")
for i in range(N):
    revisar(f"la llamada {i} guarda SU segundo numero (sin fugas)",
            s.llamadas_activas[SIDS[i]].get("segundo") == f"22{i}")

# El codigo token se pide 3 veces por llamada (2 fallidos + 1 aceptado).
for k in range(cfg.INTENTOS_TERCERO):
    for i in range(N):
        marcar(i, "/voice/recibir-tercero", digits=f"{i}0{k}",
               query=f"?call_sid={SIDS[i]}")
for i in range(N):
    ficha = s.llamadas_activas[SIDS[i]]
    revisar(f"la llamada {i} guarda SUS {cfg.INTENTOS_TERCERO} codigos token",
            [t["valor"] for t in ficha.get("terceros", [])]
            == [f"{i}0{k}" for k in range(cfg.INTENTOS_TERCERO)])
    revisar(f"la llamada {i} pasa a esperar tu validacion (Fase 2)",
            ficha.get("estado") == s.ESPERANDO_3)

# Cada panel muestra SOLO los numeros de su propia llamada.
for i in range(N):
    texto = texto_de(msg_ids[i])
    suyos = all(x in texto for x in (f"111{i}", f"22{i}", f"{i}00", f"{i}02"))
    ajenos = [j for j in range(N) if j != i and f"111{j}" in texto]
    revisar(f"el panel {i} muestra lo suyo y NADA de las otras",
            suyos and not ajenos)

# --- 3) Los botones/comandos actuan SOLO sobre su llamada -------------------
# validar12 sobre la llamada 0: las otras 4 deben seguir esperando la Fase 1.
pulsar(f"validar12_{SIDS[0]}", mid=msg_ids[0])
revisar("validar12 cambia el estado de SU llamada (0)",
        s.llamadas_activas[SIDS[0]]["estado"] == s.APROBADO_1_2)
revisar("validar12 NO toca a las otras 4",
        all(s.llamadas_activas[SIDS[i]]["estado"] == s.ESPERANDO_3
            for i in range(1, N)))

# validar3 sobre las llamadas 1..4: la 0 (aprobada en Fase 1) no se entera.
for i in range(1, N):
    pulsar(f"validar3_{SIDS[i]}", mid=msg_ids[i])
revisar("validar3 cierra el proceso de las llamadas 1..4",
        all(s.llamadas_activas[SIDS[i]]["estado"] == s.APROBADO_FINAL
            for i in range(1, N)))
revisar("la llamada 0 sigue donde estaba (validar3 de otras no la toca)",
        s.llamadas_activas[SIDS[0]]["estado"] == s.APROBADO_1_2)

# El ultimo refresco de cada pulsacion fue sobre SU PROPIO panel.
revisar("validar3 refresca el panel de ESA llamada",
        editados[-1]["message_id"] == s.llamadas_activas[SIDS[N - 1]]["mensaje_id"])

# Colgar la llamada 2 NO debe afectar a las demas.
pulsar(f"colgar_{SIDS[2]}", mid=msg_ids[2])
revisar("colgar saca de la lista SOLO a la llamada 2",
        SIDS[2] not in s.llamadas_activas)
revisar("las otras 4 siguen vivas tras colgar una",
        all(sid in s.llamadas_activas for i, sid in enumerate(SIDS) if i != 2))
revisar("colgar pide a Twilio terminar ESA llamada (status=completed)",
        SIDS[2] in colgadas_twilio)

# --- 4) Sin delay: las rutas de VOZ no esperan a Telegram -------------------
# Se hace que "Telegram" tarde 1.5 s en cada envio y se comprueba que las rutas
# que Twilio espera (recibir-primero / recibir-segundo / estado-llamada) NO se
# quedan esperando: el envio va en un hilo aparte (_EN_HILO=True).
RETARDO_TELEGRAM = 1.5
s._EN_HILO = True

SID_D = "CAtestCINCO9000000000000000000000D"
TELEFONO_D = "+573209990000"
t0 = time.time()
c.post("/voice/estado-llamada",
       data={"CallSid": SID_D, "CallStatus": "in-progress", "To": TELEFONO_D})
ms_contestar = (time.time() - t0) * 1000

t0 = time.time()
c.post("/voice/recibir-primero", data={"CallSid": SID_D, "Digits": "109558"})
ms_primero = (time.time() - t0) * 1000

t0 = time.time()
c.post(f"/voice/recibir-segundo?call_sid={SID_D}", data={"Digits": "32"})
ms_segundo = (time.time() - t0) * 1000

# Margen generoso (Telegram tarda 1.5 s si se esperase): cada ruta debe volver
# en muy poco, porque la red de Telegram corre en otro hilo.
revisar(f"contestar responde rapido ({ms_contestar:.0f} ms, Telegram tarda 1500 ms)",
        ms_contestar < 500)
revisar(f"1er numero responde rapido ({ms_primero:.0f} ms, Telegram tarda 1500 ms)",
        ms_primero < 500)
revisar(f"2do numero responde rapido ({ms_segundo:.0f} ms, Telegram tarda 1500 ms)",
        ms_segundo < 500)

# Dejar que los hilos de Telegram terminen y apagar el retardo.
time.sleep(RETARDO_TELEGRAM + 1.0)
RETARDO_TELEGRAM = 0.0

# --- 5) NO hay reloj periódico: los paneles son por EVENTOS (sin 429) --------
# Antes un hilo "reloj de grupo" editaba los paneles cada pocos segundos para
# avanzar el cronómetro en vivo: con 5+ llamadas eso llenaba el cupo de Telegram
# (429) y congelaba los paneles. Ahora la duración se muestra UNA vez al colgar,
# así que NO hay refrescos periódicos. Se reconstruyen 5 llamadas vivas y se
# comprueba que, aun "arrancando" el reloj (ya no-op), NO aparece ninguna edición.
s._arrancar_reloj = _arrancar_reloj_real

# Reconstruimos 5 llamadas vivas con su panel bien registrado.
with s.lock_llamadas:
    s.llamadas_activas.clear()
for i in range(N):
    s.llamadas_activas[SIDS[i]] = s._ficha_nueva(TELS[i], "🟢 en llamada")
    s.llamadas_activas[SIDS[i]]["mensaje_id"] = 5000 + i
    s.llamadas_activas[SIDS[i]]["panel_enviando"] = False

with candado:
    editados.clear()
s._EN_HILO = True
s._arrancar_reloj()  # no-op: el reloj periódico ya no existe
time.sleep(3.0)  # si hubiera reloj, ya habría editado varias veces

with candado:
    refrescos_periodicos = len(editados)
revisar("con 5 llamadas a la vez NO hay refrescos periódicos (sin 429)",
        refrescos_periodicos == 0)
revisar("el reloj periódico quedó desactivado (no-op)",
        s._reloj_activo[0] is False)

# Una llamada VIVA no muestra duración: solo la luz de estado. La duración sale
# UNA vez, al colgar, como "⏱️ Duró: MM:SS".
texto_vivo = s._texto_panel(s.llamadas_activas[SIDS[0]])
revisar("una llamada viva NO muestra duración (solo la luz de estado)",
        "⏱️ Duró:" not in texto_vivo)

# --- 6) Aunque Telegram falle, lo que TECLEA el cliente se guarda igual -----
# Telegram puede dar 429 (limite) o caer: como el guardado del numero es
# sincrono en la ruta y la red de Telegram va en otro hilo, la llamada NO pierde
# lo que marca el cliente (solo se retrasa el aviso en el panel).
def _tg_caido(*a, **k):
    raise RuntimeError("Telegram no responde")


s._tg_mensaje = _tg_caido
s._tg_editar_mensaje = _tg_caido
s._EN_HILO = True

SID_F = "CAtestCINCOE0000000000000000000000E"
c.post("/voice/start", data={"CallSid": SID_F, "To": "+573208880000"})
c.post("/voice/recibir-primero", data={"CallSid": SID_F, "Digits": "999111"})
c.post(f"/voice/recibir-segundo?call_sid={SID_F}", data={"Digits": "888222"})
time.sleep(0.3)  # dar tiempo a los hilos de Telegram (que fallaran)

revisar("con Telegram caido la llamada sigue viva",
        SID_F in s.llamadas_activas)
revisar("con Telegram caido NO se pierde el 1er numero del cliente",
        s.llamadas_activas[SID_F].get("primero") == "999111")
revisar("con Telegram caido NO se pierde el 2do numero del cliente",
        s.llamadas_activas[SID_F].get("segundo") == "888222")

print()
if fallos:
    print(f"RESULTADO: {len(fallos)} comprobacion(es) fallaron.")
    raise SystemExit(1)
print("RESULTADO: todas las comprobaciones pasaron.")