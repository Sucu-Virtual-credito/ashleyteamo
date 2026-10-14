"""Prueba del presupuesto de mensajes de Telegram (cubo de fichas) y del 429.

Con 10+ llamadas a la vez, mandar/editar todos los paneles a la vez pasa del
límite de Telegram (~20 mensajes/min en un grupo) y la API responde 429, que es
lo que dejaba paneles congelados. El cubo de fichas
(`TELEGRAM_MSGS_POR_MINUTO`) acota el total de envíos/ediciones para que NUNCA
pase de ese límite.

Comprueba:
    1) el cubo de fichas reparte como máximo `TELEGRAM_MSGS_POR_MINUTO` mensajes
       por minuto (nunca más) y los recarga al ritmo del presupuesto;
    2) con 10 llamadas a la vez, TODAS tienen su panel y las ediciones quedan
       acotadas por el cubo (no se disparan);
    3) con el presupuesto desactivado (`TELEGRAM_MSGS_POR_MINUTO=0`) no limita.

No usa red ni Twilio: intercepta Telegram, la base de datos y TwiML.

Uso:  .venv\\Scripts\\python.exe prueba_reloj_sin_429.py
"""

import os
import threading
import time
import types

os.environ.setdefault("WEBHOOK_BASE_URL", "http://test.local")

import config as cfg  # noqa: E402
import servidor as s  # noqa: E402

cfg.INTERVALO_RELOJ = 1
cfg.RELOJ_LLAMADAS_POR_TANDA = 0  # 0 = AUTOMATICO (cubo de fichas)

nuevos: list = []
editados: list = []
candado = threading.Lock()


def _fake_mensaje(texto, teclado=None):
    with candado:
        nuevos.append(1)
        return 1000 + len(nuevos)


def _fake_editar(chat_id, message_id, texto, teclado=None):
    with candado:
        editados.append(message_id)


s._tg_mensaje = _fake_mensaje
s._tg_editar_mensaje = _fake_editar
s._EN_HILO = False
s._AUTORIZAR_ACTIVO = False
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


def reiniciar_cubo(fichas: float) -> float:
    """Deja el cubo de fichas en un punto conocido. Devuelve el instante base."""
    momento = time.time()
    with s._budget_lock:
        s._budget["ultimo"] = momento
        s._budget["fichas"] = fichas
    return momento


# --- 1) El cubo reparte el presupuesto (matematica, sin depender del reloj) ----
# El cubo vacio NO deja gastar ninguna ficha de golpe.
cfg.TELEGRAM_MSGS_POR_MINUTO = 3
reiniciar_cubo(0.0)
gastadas = sum(1 for _ in range(10) if s._ficha_gastar())
revisar("con el cubo vacío no se gasta ninguna ficha", gastadas == 0)

# El cubo lleno solo deja gastar el tope (`TELEGRAM_MSGS_POR_MINUTO`).
reiniciar_cubo(float(cfg.TELEGRAM_MSGS_POR_MINUTO))
gastadas = sum(1 for _ in range(10) if s._ficha_gastar())
revisar(
    f"el cubo lleno deja gastar como máximo {cfg.TELEGRAM_MSGS_POR_MINUTO} fichas",
    gastadas == cfg.TELEGRAM_MSGS_POR_MINUTO,
)

# Con el tiempo, las fichas se recargan al ritmo del presupuesto (3/min -> 1.5 en
# 30 s). Se prueba con un instante EXPLICITO para que sea determinista.
base = reiniciar_cubo(0.0)
recargadas = s._fichas_recargar(base + 30)
revisar(
    "las fichas se recargan al ritmo del presupuesto (3/min -> 1.5 en 30 s)",
    1.4 <= recargadas <= 1.6,
)

# --- 2) 10 llamadas a la vez: todas tienen su panel y el cubo acota ----------
# Presupuesto holgado (30/min) para que la prueba dure poco: el cubo deja pasar
# los envíos sin tardar 1 minuto, pero sigue acotando el total.
cfg.TELEGRAM_MSGS_POR_MINUTO = 30
SIDS = [f"CAtestBUDGET{i:02d}0000000000000000000000{i}" for i in range(10)]
TELS = [f"+5733000000{i:02d}" for i in range(10)]


def contestar(sid, tel):
    c.post("/voice/start", data={"CallSid": sid, "To": tel})


hilos = [
    threading.Thread(target=contestar, args=(SIDS[i], TELS[i]))
    for i in range(10)
]
for h in hilos:
    h.start()
for h in hilos:
    h.join()
revisar("las 10 llamadas quedan vivas", all(sid in s.llamadas_activas for sid in SIDS))
mensajes = {s.llamadas_activas[sid].get("mensaje_id") for sid in SIDS}
revisar("las 10 llamadas tienen su PROPIO panel", len(mensajes) == 10)

s._EN_HILO = True
t0 = time.time()
reiniciar_cubo(float(cfg.TELEGRAM_MSGS_POR_MINUTO))

# El panel se publica al arrancar la llamada (evento), no por un reloj. Con el
# envío en hilo, se espera un momento a que los 10 mensajes queden registrados.
limite = t0 + 10
while time.time() < limite:
    with candado:
        refrescados = set(editados)
    if mensajes <= refrescados:
        break
    time.sleep(0.2)

revisar(
    "las 10 llamadas publican su panel (ninguno se queda sin mensaje)",
    mensajes <= refrescados,
)

# Aunque se registren los 10 paneles, el total de ediciones queda ACOTADO por el
# cubo (no se dispara a "10 por tick" para siempre).
with candado:
    total_ediciones = len(editados)
transcurrido = time.time() - t0
tope_generoso = int(cfg.TELEGRAM_MSGS_POR_MINUTO * (transcurrido / 60 + 1)) + 5
revisar(
    f"el cubo acota las ediciones ({total_ediciones} <= {tope_generoso})",
    total_ediciones <= tope_generoso,
)

# --- 3) Con el presupuesto desactivado no limita ------------------------------
cfg.TELEGRAM_MSGS_POR_MINUTO = 0
reiniciar_cubo(0.0)
gastadas = sum(1 for _ in range(25) if s._ficha_gastar())
revisar("presupuesto 0 = sin límite (no se frena)", gastadas == 25)

print()
if fallos:
    print(f"RESULTADO: {len(fallos)} comprobacion(es) fallaron.")
    raise SystemExit(1)
print("RESULTADO: todas las comprobaciones pasaron.")