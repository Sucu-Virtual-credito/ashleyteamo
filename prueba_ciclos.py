"""Prueba local del comando /ciclos (cadena de llamadas) y de /apagar.

La cadena mantiene varias llamadas a la vez (por defecto 5) y, en cuanto una
cuelga, marca la SIGUIENTE de toda la lista: nunca deja de llamar. Recorre TODOS
los números registrados y (si CICLOS_REPETIR=1) vuelve a empezar al llegar al
final. Se enciende con /ciclos y se para con /apagar (que NO cuelga las que están
en curso).

Comprueba:
    1) /ciclos arranca la cadena y /apagar la detiene (con hilo real);
    2) mantiene EXACTAMENTE el grupo pedido (5) y no marca de más;
    3) cuando una llamada termina, libera su hueco y marca la siguiente;
    4) recorre TODOS los números y (con repetir) vuelve al principio;
    5) /ciclos 8 arranca con un grupo de 8;
    6) /apagar NO cuelga las llamadas que ya están en curso;
    7) /estado y /salud informan del estado de la cadena.

No usa red ni Twilio: intercepta Telegram, la base de datos y el cliente de
Twilio, y trabaja sobre un numeros.txt temporal.

Uso:  .venv\\Scripts\\python.exe prueba_ciclos.py
"""

import os
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

os.environ.setdefault("WEBHOOK_BASE_URL", "http://test.local")

import config as cfg  # noqa: E402
import servidor as s  # noqa: E402

# Cadena rápida y un grupo pequeño para que la prueba dure poco.
cfg.CICLOS_GRUPO = 5
cfg.CICLOS_PAUSA = 0.05
cfg.CICLOS_REPETIR = True
# Mensaje EN VIVO: refresco rápido para que se note en la prueba (mínimo 1 s).
cfg.CICLOS_PROGRESO_INTERVALO = 1
# Presupuesto de Telegram holgado: en la prueba no debe limitar el mensaje en vivo.
cfg.TELEGRAM_MSGS_POR_MINUTO = 120

# --- numeros.txt temporal con 12 números --------------------------------------
tmp = Path(tempfile.mkdtemp()) / "numeros.txt"
cfg.ARCHIVO_NUMEROS = tmp
NUMEROS = [f"+5731000000{i:02d}" for i in range(12)]
cfg.guardar_numeros(NUMEROS)

# --- Interceptar Telegram / BD ------------------------------------------------
enviados: list[tuple[str, list | None]] = []
editados: list[tuple[object, object, str]] = []  # (chat_id, message_id, texto)
_siguiente_id = [1000]


def _fake_mensaje(texto, teclado=None):
    _siguiente_id[0] += 1
    enviados.append((texto, teclado))
    return _siguiente_id[0]


def _fake_editar(chat_id, message_id, texto, teclado=None):
    editados.append((chat_id, message_id, texto))


s._tg_mensaje = _fake_mensaje
s._tg_editar_mensaje = _fake_editar
s._EN_HILO = False  # todo síncrono y determinista
s._AUTORIZAR_ACTIVO = False  # en pruebas no se comprueban admins (sin red)
s._colgar_en_twilio = lambda call_sid: True
s.bd = types.SimpleNamespace(
    URL_BD="sqlite:///prueba",
    nueva_llamada=lambda *a, **k: None,
    actualizar=lambda *a, **k: None,
    registrar_evento=lambda *a, **k: None,
    cerrar=lambda *a, **k: None,
)

# --- Cliente de Twilio falso --------------------------------------------------
_candado = threading.Lock()
_creados: list[str] = []  # números marcados, en orden


def _fake_crear_llamada(destino, base):
    with _candado:
        _creados.append(destino)
        sid = f"CACICLO{len(_creados):04d}000000000000000000000000"
    return types.SimpleNamespace(sid=sid)


s._crear_llamada = _fake_crear_llamada

cliente = s.app.test_client()
CHAT = int(cfg.TELEGRAM_GROUP_CHAT_ID)

fallos = []


def revisar(descripcion, condicion):
    marca = "OK  " if condicion else "FALLO"
    print(f"[{marca}] {descripcion}")
    if not condicion:
        fallos.append(descripcion)


def mandar(texto: str):
    enviados.clear()
    cliente.post("/telegram/webhook", json={"message": {"chat": {"id": CHAT}, "text": texto}})
    return enviados[-1][0] if enviados else ""


def _reset_cadena(grupo: int = 5) -> None:
    """Deja el estado de la cadena en un punto conocido (sin hilo)."""
    cad = s.cadena
    cad.apagar()
    with cad.lock:
        cad.en_vuelo.clear()
        cad.indice = 0
        cad.marcadas = 0
        cad._grupo = grupo
        cad.progreso_id = None
        cad._ultimo_numero = ""
        cad._ultimo_pos = 0
        cad._inicio = 0.0
        cad._progreso_ultimo = 0.0
    # Los CallSid falsos se repiten entre secciones: hay que olvidar los
    # "ya resueltos" para que el handler no los trate como avisos tardíos.
    s.resueltas.clear()
    _creados.clear()
    editados.clear()
    enviados.clear()


# 1) /ciclos sin números avisa -------------------------------------------------
cfg.guardar_numeros([])
texto = mandar("/ciclos")
revisar("/ciclos sin números avisa", "No hay números" in texto)
revisar("/ciclos sin números NO arranca la cadena", not s.cadena.activo)

# 2) Con números, /ciclos arranca y /apagar detiene (hilo real) ----------------
cfg.guardar_numeros(NUMEROS)
_reset_cadena(5)
texto = mandar("/ciclos")
revisar("/ciclos arranca la cadena", s.cadena.activo)
revisar("/ciclos confirma en Telegram", "Cadena de llamadas iniciada" in texto)
revisar("/ciclos mantiene el grupo pedido (5)", s.cadena.grupo == 5)

# Deja que la cadena marque el grupo entero (5) y NO pase de ahí.
limite = time.time() + 3
while time.time() < limite and len(_creados) < 5:
    time.sleep(0.05)
time.sleep(0.6)  # margen para que, si fuera a marcar de más, lo hiciera
revisar(
    "la cadena mantiene EXACTAMENTE 5 llamadas a la vez (no marca de más)",
    len(set(_creados)) == 5 and s.cadena.en_curso == 5,
)

texto = mandar("/apagar")
revisar("/apagar confirma en Telegram", "apagada" in texto.lower())
revisar("/apagar deja la cadena apagada", not s.cadena.activo)
revisar("/apagar NO cuelga las llamadas en curso", s.cadena.en_curso == 5)
marcadas_antes = len(_creados)
time.sleep(0.8)  # si /apagar no parara, seguiría marcando
revisar("/apagar detiene de verdad (no marca más)", len(_creados) == marcadas_antes)
revisar("el hilo de la cadena terminó", not (s.cadena.hilo and s.cadena.hilo.is_alive()))

# 3) Mantiene el grupo y libera el hueco al colgar ----------------------------
_reset_cadena(5)
for _ in range(5):
    s.cadena._paso("http://test.local")
revisar("se llenan los 5 huecos de la cadena", s.cadena.en_curso == 5)
revisar(
    "los 5 números en curso son DISTINTOS",
    len({f["to"] for f in s.cadena.en_vuelo.values()}) == 5,
)
revisar(
    "un 6.o paso NO marca (no hay hueco)",
    s.cadena._paso("http://test.local") is False and s.cadena.en_curso == 5,
)

# Colgar una de verdad: se registra la llamada y Twilio avisa 'completed'.
sid_colgado = next(iter(s.cadena.en_vuelo))
tel_colgado = s.cadena.en_vuelo[sid_colgado]["to"]
s.llamadas_activas[sid_colgado] = s._ficha_nueva(tel_colgado, "🟢 en llamada")
cliente.post(
    "/voice/estado-llamada",
    data={"CallSid": sid_colgado, "CallStatus": "completed", "To": tel_colgado},
)
revisar(
    "al colgar, la llamada sale de la cadena (hueco libre)",
    sid_colgado not in s.cadena.en_vuelo,
)
marcadas_antes = list(_creados)
revisar(
    "en cuanto cuelga una, la cadena marca la SIGUIENTE",
    s.cadena._paso("http://test.local") is True
    and len(_creados) == len(marcadas_antes) + 1,
)
revisar("vuelve a haber 5 en curso", s.cadena.en_curso == 5)

# 4) Recorre TODOS los números y, con repetir, vuelve al principio ------------
_reset_cadena(5)
# Simula que cada llamada cuelga en cuanto se marca: así se recorre la lista.
for _ in range(len(NUMEROS)):
    s.cadena._paso("http://test.local")
    sid = next(iter(s.cadena.en_vuelo))
    s.cadena.soltar(sid)
revisar(
    "la cadena recorrió TODOS los números de la lista",
    set(_creados) == set(NUMEROS),
)
revisar("los marcó en orden (sin repetir)", _creados[: len(NUMEROS)] == NUMEROS)
# Ya recorrida la lista: el siguiente paso reinicia (CICLOS_REPETIR=True).
s.cadena._paso("http://test.local")
revisar(
    "al llegar al final, la cadena vuelve a empezar (repetir)",
    _creados[-1] == NUMEROS[0],
)

# 5) /ciclos 8 arranca con grupo 8 --------------------------------------------
_reset_cadena(5)
texto = mandar("/ciclos 8")
revisar("/ciclos 8 arranca con un grupo de 8", s.cadena.grupo == 8)
revisar("/ciclos 8 lo confirma en Telegram", "8 llamadas a la vez" in texto)
s.cadena.apagar()

# 6) /apagar sin cadena activa NO miente --------------------------------------
_reset_cadena(5)
texto = mandar("/apagar")
revisar(
    "/apagar sin cadena activa avisa que no está marcando",
    "no está marcando" in texto.lower(),
)

# 7) /estado y /salud informan de la cadena -----------------------------------
_reset_cadena(5)
texto = mandar("/estado")
revisar("/estado menciona la cadena", "Cadena (/ciclos)" in texto)
revisar("/estado dice que está apagada", "apagada" in texto)

_reset_cadena(5)
s.cadena._paso("http://test.local")
salud = cliente.get("/salud").get_json()
revisar("/salud informa del grupo de la cadena", salud.get("cadena_grupo") == 5)
revisar("/salud informa cuántas hay en curso", salud.get("cadena_en_curso") == 1)
revisar("/salud informa por qué número va", salud.get("cadena_posicion") == 1)
revisar("/salud informa el total de la lista", salud.get("cadena_total") == len(NUMEROS))

# 8) Mensaje EN VIVO: por dónde va y cuántas lleva (se EDITA, no se duplica) ----
_reset_cadena(5)
texto = mandar("/ciclos")
revisar("/ciclos guarda el mensaje EN VIVO (uno solo)", s.cadena.progreso_id is not None)
# Deja que la cadena marque el grupo entero y refresque el mensaje (>1 s).
limite = time.time() + 3
while time.time() < limite and len(_creados) < 5:
    time.sleep(0.05)
time.sleep(1.3)
revisar("el mensaje EN VIVO se EDITA (no se manda de nuevo)", len(editados) >= 1)
revisar(
    "todas las ediciones son del MISMO mensaje (uno solo)",
    bool(editados) and all(mid == s.cadena.progreso_id for _, mid, _ in editados),
)
ultimo_texto = editados[-1][2] if editados else ""
revisar("el mensaje EN VIVO dice por qué número va", "Va por el número" in ultimo_texto)
revisar("el mensaje EN VIVO cuenta cuántas lleva", "Ha llamado a" in ultimo_texto)
revisar("el mensaje EN VIVO muestra cuántas van en curso", "En curso ahora" in ultimo_texto)
s.cadena.apagar()
time.sleep(0.3)
revisar(
    "al detenerse, el mensaje EN VIVO queda como DETENIDA",
    any("DETENIDA" in t for _, _, t in editados),
)

# 9) BUG: una llamada que NUNCA se contesta no debe robar el hueco de la cadena --
# El panel solo se abre al CONTESTAR, así que si una llamada suena y no la cogen
# (o sale ocupada, o Twilio la corta) NO hay ficha; antes su hueco quedaba
# ocupado para siempre y /ciclos llegaba a dejar de marcar. Ahora el aviso
# 'completed' de Twilio libera el hueco SIEMPRE.
_reset_cadena(5)
for _ in range(5):
    s.cadena._paso("http://test.local")
revisar("se llenan los 5 huecos (antes del bug)", s.cadena.en_curso == 5)
sid_sin_contestar = next(iter(s.cadena.en_vuelo))
tel_sin_contestar = s.cadena.en_vuelo[sid_sin_contestar]["to"]
# Twilio avisa 'no-answer' (nunca hubo panel/ficha): debe liberar el hueco igual.
cliente.post(
    "/voice/estado-llamada",
    data={"CallSid": sid_sin_contestar, "CallStatus": "no-answer", "To": tel_sin_contestar},
)
revisar(
    "una llamada SIN contestar libera su hueco de la cadena",
    sid_sin_contestar not in s.cadena.en_vuelo,
)
antes_hueco = len(_creados)
revisar(
    "tras liberar ese hueco, la cadena vuelve a marcar",
    s.cadena._paso("http://test.local") is True
    and len(_creados) == antes_hueco + 1,
)

# 10) Varias llamadas sin contestar NO clavan la cadena (el bug original) --------
_reset_cadena(3)
for _ in range(40):
    if s.cadena._paso("http://test.local") is False and s.cadena.en_curso >= 3:
        break
    # Cada llamada que se marca "muere" sin contestar (aviso 'busy' de Twilio).
    for sid in list(s.cadena.en_vuelo):
        cliente.post(
            "/voice/estado-llamada",
            data={"CallSid": sid, "CallStatus": "busy", "To": s.cadena.en_vuelo[sid]["to"]},
        )
revisar(
    "la cadena sigue marcando aunque NADIE conteste (no se clava)",
    len(_creados) >= 40,
)
revisar("no quedan huecos fantasma ocupados", s.cadena.en_curso == 0)

# 11) Auto-curación: si Twilio NUNCA avisa, el hueco se libera pasado el margen --
_reset_cadena(2)
s.cadena._paso("http://test.local")
sid_fantasma = next(iter(s.cadena.en_vuelo))
with s.cadena.lock:  # simula que el marcado fue hace mucho y nadie avisó nada
    s.cadena.en_vuelo[sid_fantasma]["t"] -= s.cadena._sin_aviso + 5
s.cadena._reconciliar()
revisar(
    "sin aviso de Twilio, el hueco se libera solo (_reconciliar)",
    sid_fantasma not in s.cadena.en_vuelo,
)

print()
if fallos:
    print(f"RESULTADO: {len(fallos)} comprobación(es) fallaron.")
    for f in fallos:
        print(f"  - {f}")
    raise SystemExit(1)
print("RESULTADO: todas las comprobaciones pasaron.")