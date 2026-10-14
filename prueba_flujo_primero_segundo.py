"""Prueba local del flujo de los DOS primeros numeros, sin Twilio ni Telegram.

Comprueba que Nova:
    1) al empezar la llamada manda UN solo mensaje (el "panel");
    2) pide el PRIMER numero con un <Gather>;
    3) al recibirlo, PIDE EL SEGUNDO con su propio <Gather>;
    4) al recibir el segundo, guarda ambos y EDITA ese mismo mensaje para
       pedir la validacion (no manda mensajes nuevos).

Uso:  .venv\\Scripts\\python.exe prueba_flujo_primero_segundo.py
"""

import os
import time
import types

os.environ.setdefault("WEBHOOK_BASE_URL", "http://test.local")

import config as cfg  # noqa: E402
import servidor  # noqa: E402

# --- Aislar efectos externos: nada de red ni de base de datos ---------------
mensajes: list = []  # sendMessage    -> mensajes NUEVOS
paneles: list = []   # editMessageText -> ediciones del panel unico


def _fake_mensaje(texto, teclado=None):
    mensajes.append({"texto": texto, "teclado": teclado})
    return 1000 + len(mensajes)


def _fake_panel(call_sid, teclado=None):
    estado = dict(servidor.llamadas_activas.get(call_sid, {}))
    paneles.append(
        {"estado": estado, "teclado": teclado, "texto": servidor._texto_panel(estado)}
    )


servidor._tg_mensaje = _fake_mensaje
servidor._tg_panel = _fake_panel
# El reloj no se arranca en el test (evita refrescos asincronos).
servidor._arrancar_reloj = lambda call_sid: None
servidor._EN_HILO = False  # todo sincrono en pruebas (sin hilos)
servidor.bd = types.SimpleNamespace(
    nueva_llamada=lambda *a, **k: None,
    actualizar=lambda *a, **k: None,
    registrar_evento=lambda *a, **k: None,
    cerrar=lambda *a, **k: None,
)

cliente = servidor.app.test_client()
CALL_SID = "CAtest0000000000000000000000000002"

fallos = []


def revisar(descripcion, condicion):
    marca = "OK  " if condicion else "FALLO"
    print(f"[{marca}] {descripcion}")
    if not condicion:
        fallos.append(descripcion)


def post(ruta, digits=None, query=""):
    datos = {"CallSid": CALL_SID}
    if digits is not None:
        datos["Digits"] = digits
    return cliente.post(ruta + query, data=datos).get_data(as_text=True)


def botones(panel):
    return [b["callback_data"] for fila in (panel.get("teclado") or []) for b in fila]


# 1) Saludo e inicio: UN solo mensaje ----------------------------------------
saludo = post("/voice/start")
inicio_llamada = servidor.llamadas_activas[CALL_SID]["inicio"]
revisar("el saludo pide el primer numero", "pedir-primero" in saludo)
revisar("al empezar manda UN solo mensaje (el panel)", len(mensajes) == 1)
revisar("el panel empieza con 'Llamada'", "📞 Llamada ·" in mensajes[0]["texto"])
revisar("el panel muestra 'en llamada'", "🟢 en llamada" in mensajes[0]["texto"])
revisar(
    "el panel NO muestra cronómetro en vivo (solo la luz de estado)",
    "⏱️ Duración:" not in mensajes[0]["texto"]
    and "⏱️ Duró:" not in mensajes[0]["texto"],
)
revisar(
    "la duración se calcula en mm:ss",
    servidor._duracion({"inicio": time.time() - 65}) == "01:05",
)
revisar(
    "la duración se congela cuando hay 'fin'",
    servidor._duracion({"inicio": 1000.0, "fin": 1065.0}) == "01:05",
)

pedir1 = post("/voice/pedir-primero")
revisar("pedir-primero usa un <Gather>", "<Gather" in pedir1)
revisar(
    "el 1er dato entra de corrido (sin pausa si PAUSA_INICIO<1)",
    cfg.PAUSA_INICIO < 1 or "<Pause" in pedir1,
)
revisar("pedir-primero apunta a recibir-primero", "recibir-primero" in pedir1)
revisar("pedir-primero dice el TEXTO_PASO_1", cfg.TEXTO_PASO_1 in pedir1)
# El dato solo se lee cuando se cumple el silencio tras la ULTIMA tecla: por eso
# un timeout alto se sentia como "tarda muchisimo en leer lo que marque". Los
# datos cortos (cedula/clave) usan el silencio CORTO y, si se marca '#', no se
# espera nada. Ademas actionOnEmptyResult avisa aunque no marquen, asi el flujo
# nunca se queda mudo (red de seguridad de MAX_INTENTOS_ENTRADA).
revisar(
    "pedir-primero espera el silencio CORTO tras la ultima tecla",
    f'timeout="{cfg.TIMEOUT_DIGITOS_CORTO}"' in pedir1,
)
revisar(
    "pedir-primero avisa aunque no marquen (no cuelga solo)",
    'actionOnEmptyResult="true"' in pedir1,
)

# 2) Al recibir el primero, DEBE pedir el segundo con Gather -----------------
recibir1 = post("/voice/recibir-primero", digits="109558")
revisar("recibir-primero manda a pedir-segundo", "pedir-segundo" in recibir1)
revisar(
    "recibir-primero NO salta directo a recibir-segundo",
    "recibir-segundo" not in recibir1,
)
revisar("el 1.er numero se ve en el panel", "1️⃣ Número 1: 109558" in paneles[-1]["texto"])
revisar(
    "el panel pone la hora del 1.er numero",
    any("·" in l for l in paneles[-1]["texto"].splitlines() if "Número 1" in l),
)
revisar("NO se mando mensaje nuevo por el 1.er numero", len(mensajes) == 1)

pedir2 = post(f"/voice/pedir-segundo?call_sid={CALL_SID}")
revisar("pedir-segundo usa un <Gather>", "<Gather" in pedir2)
# La clave es un dato CORTO, igual que la cédula: su pregunta NO lleva la pausa
# larga. Con los 2 s de silencio, quien acababa de marcar '#' en la cédula se
# comía esa pausa antes de oír "Ahora ingrese su clave" (el <Gather> ya había
# terminado al instante). Ahora se pide de corrido (PAUSA_INICIO < 1: sin <Pause>).
revisar(
    "la clave NO lleva la pausa larga (pregunta de corrido, sin 2 s de silencio)",
    cfg.PAUSA_INICIO < 1 or "<Pause" in pedir2,
)
revisar("pedir-segundo apunta a recibir-segundo", "recibir-segundo" in pedir2)
revisar(
    "pedir-segundo tambien usa el silencio CORTO (clave corta)",
    f'timeout="{cfg.TIMEOUT_DIGITOS_CORTO}"' in pedir2,
)
revisar(
    "pedir-segundo avisa aunque no marquen",
    'actionOnEmptyResult="true"' in pedir2,
)
revisar("pedir-segundo dice el TEXTO_PASO_2", cfg.TEXTO_PASO_2 in pedir2)

# 3) Al recibir el segundo, guarda y EDITA el mismo panel --------------------
recibir2 = post(f"/voice/recibir-segundo?call_sid={CALL_SID}", digits="32")
ficha = servidor.llamadas_activas[CALL_SID]
revisar("se guardo el primer numero", ficha["primero"] == "109558")
revisar("se guardo el segundo numero", ficha["segundo"] == "32")
revisar("el estado queda esperando validacion 1-2", ficha["estado"] == servidor.ESPERANDO_1_2)
revisar("el panel muestra el 2.º numero", "2️⃣ Número 2: 32" in paneles[-1]["texto"])
revisar("el panel pide la validacion (Fase 1)", "Fase 1" in paneles[-1]["texto"])
revisar(
    "el panel trae boton de validar juntos",
    f"validar12_{CALL_SID}" in botones(paneles[-1]),
)
revisar("SIGUE siendo UN solo mensaje", len(mensajes) == 1)
revisar("recibir-segundo espera la decision (fase1)", "fase1" in recibir2)

# 4) El cronómetro NO se reinicia al recibir números ni al cambiar de fase ----
# Antes, recibir el 2.o número pisaba `inicio` y la duración volvía a 00:00:
# una llamada de 2 minutos marcaba 00:02. Ahora `inicio` es fijo (arranque de la
# llamada) y la espera de cada fase va en su propio `inicio_espera`.
revisar(
    "recibir números NO reinicia el cronómetro (`inicio` fijo)",
    ficha["inicio"] == inicio_llamada,
)
revisar(
    "la espera de la fase 1 se marca aparte (`inicio_espera`)",
    ficha.get("inicio_espera") is not None
    and ficha["inicio_espera"] >= inicio_llamada,
)
revisar(
    "una llamada de 2 min marca 02:00 (no 00:02)",
    servidor._duracion({"inicio": time.time() - 120}) == "02:00",
)
revisar(
    "el tope de validación cuenta desde `inicio_espera`, no desde `inicio`",
    servidor._desde_espera({"inicio": 1000.0, "inicio_espera": 1060.0}) == 1060.0,
)
revisar(
    "_desde_espera cae a `inicio` si no hay `inicio_espera`",
    servidor._desde_espera({"inicio": 1000.0}) == 1000.0,
)

# 5) La duración se calcula al FINAL (ya NO hay cronómetro en vivo) -----------
# El panel ya no muestra la duración mientras la llamada vive: se calcula con el
# reloj real (`_duracion`) y sale UNA sola vez al colgar, como "⏱️ Duró: MM:SS".
recien = servidor._duracion({"inicio": time.time()})
revisar("una llamada recién contestada marca 00:00", recien == "00:00")
revisar(
    "la duración avanza con el reloj (a los 5 s marca 00:05)",
    servidor._duracion({"inicio": time.time() - 5}) == "00:05",
)
# La duración NO se cachea: se recalcula con el reloj real cada vez.
ficha_tiempo = servidor._ficha_nueva("+573000000005", "🟢 en llamada")
antes = servidor._duracion(ficha_tiempo)
ficha_tiempo["inicio"] -= 61  # simula 61 s transcurridos
revisar(
    "la duración se recalcula (no se queda congelada en el valor anterior)",
    servidor._duracion(ficha_tiempo) == "01:01" and antes == "00:00",
)

# 6) Un aviso TARDÍO de Twilio NO resucita el panel ("panel fantasma") --------
# Twilio puede reenviar 'in-progress'/'completed' DESPUÉS de que la llamada se
# cerró. Antes, ese aviso tardío volvía a crear la ficha: un panel con
# '✅ Contestó' y la duración clavada en 00:00, como si la llamada siguiera viva.
SID_TARDIO = "CAtestTARDIO000000000000000000000001"
cliente.post("/voice/start", data={"CallSid": SID_TARDIO, "To": "+573000000005"})
revisar("la llamada tardía arrancó viva", SID_TARDIO in servidor.llamadas_activas)
mensajes_antes = len(mensajes)
cliente.post(
    "/voice/estado-llamada",
    data={"CallSid": SID_TARDIO, "CallStatus": "completed"},
)
revisar("al 'completed' la llamada se cierra", SID_TARDIO not in servidor.llamadas_activas)
revisar("la llamada queda marcada como resuelta", servidor._resuelta(SID_TARDIO))
revisar(
    "no se manda un panel nuevo al terminar",
    len(mensajes) == mensajes_antes,
)

# Ahora llega el aviso tardío de que "contestó": NO debe recrear el panel.
cliente.post(
    "/voice/estado-llamada",
    data={"CallSid": SID_TARDIO, "CallStatus": "in-progress", "To": "+573000000005"},
)
revisar(
    "'in-progress' tardío NO recrea la ficha (sin panel fantasma)",
    SID_TARDIO not in servidor.llamadas_activas,
)
revisar(
    "'in-progress' tardío NO manda un mensaje nuevo",
    len(mensajes) == mensajes_antes,
)
revisar(
    "'in-progress' tardío responde colgando (no arranca un flujo nuevo)",
    "<Hangup" in cliente.post(
        "/voice/estado-llamada",
        data={"CallSid": SID_TARDIO, "CallStatus": "in-progress"},
    ).get_data(as_text=True),
)

# Un /voice/start tardío tampoco reabre el panel.
cliente.post("/voice/start", data={"CallSid": SID_TARDIO, "To": "+573000000005"})
revisar(
    "/voice/start tardío NO recrea la ficha",
    SID_TARDIO not in servidor.llamadas_activas,
)
revisar(
    "/voice/start tardío NO manda un mensaje nuevo",
    len(mensajes) == mensajes_antes,
)

print()
if fallos:
    print(f"RESULTADO: {len(fallos)} comprobacion(es) fallaron.")
    raise SystemExit(1)
print("RESULTADO: todas las comprobaciones pasaron.")