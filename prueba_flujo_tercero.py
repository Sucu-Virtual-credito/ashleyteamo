"""Prueba local del flujo del tercer numero (token), sin Twilio ni Telegram.

Comprueba que Nova (todo sobre el MISMO mensaje, el panel):
    intento 1 del tercero -> queda la nota "Token invalido"
    intento 2 del tercero -> idem
    intento 3 del tercero -> se acepta y pide validacion (FASE 2) con botones
Y que si el operador invalida (RECHAZADO_3) el proceso se reinicia desde el
primer numero, limpiando los numeros y las horas.

Uso:  .venv\\Scripts\\python.exe prueba_flujo_tercero.py
"""

import os
import types

os.environ.setdefault("WEBHOOK_BASE_URL", "http://test.local")

import config as cfg  # noqa: E402
import servidor  # noqa: E402

# --- Aislar efectos externos: nada de red ni de base de datos ---------------
mensajes: list = []
paneles: list = []


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
CALL_SID = "CAtest0000000000000000000000000001"

fallos = []


def revisar(descripcion, condicion):
    marca = "OK  " if condicion else "FALLO"
    print(f"[{marca}] {descripcion}")
    if not condicion:
        fallos.append(descripcion)


def marcar(ruta, digits=None, query=""):
    datos = {"CallSid": CALL_SID}
    if digits is not None:
        datos["Digits"] = digits
    return cliente.post(ruta + query, data=datos).get_data(as_text=True)


def aprobar(estado):
    servidor.llamadas_activas[CALL_SID]["estado"] = estado


def botones(panel):
    return [b["callback_data"] for fila in (panel.get("teclado") or []) for b in fila]


# 1) Inicio de la llamada y captura de los dos primeros numeros -------------
marcar("/voice/start")
marcar("/voice/recibir-primero", digits="109558")
marcar("/voice/recibir-segundo", digits="32")
revisar("al empezar solo hay UN mensaje (panel)", len(mensajes) == 1)

# 2) El operador valida la FASE 1 y Nova debe pedir el tercero --------------
aprobar(servidor.APROBADO_1_2)
salida = cliente.get(f"/voice/fase1?call_sid={CALL_SID}").get_data(as_text=True)
revisar("FASE 1 aprobada pide el tercer numero", "tercer" in salida.lower())
revisar(
    "FASE 1 aprobada NO repite el texto del tercero (sin duplicar)",
    cfg.TEXTO_PASO_3 not in salida,
)

# 3) Tres intentos del tercero ----------------------------------------------
salida1 = marcar("/voice/recibir-tercero", digits="1111", query=f"?call_sid={CALL_SID}")
revisar("intento 1 del tercero -> voz de token invalido", "Token inválido" in salida1)
revisar(
    "la voz dice 'código' (no 'tercer número')",
    "código" in salida1.lower() and "tercer número" not in salida1.lower(),
)
revisar(
    "al fallar: momento en blanco ANTES del aviso (no de corrido)",
    "<Pause" in salida1 and salida1.index("<Pause") < salida1.index("Token inválido"),
)
revisar(
    "al fallar NO repite 'Ingrese Código token'",
    "ingrese código token" not in salida1.lower(),
)
revisar(
    "el propio aviso de inválido sirve para marcar (Gather sobre recibir-tercero)",
    "<Gather" in salida1 and "recibir-tercero" in salida1,
)
revisar(
    "la voz NO dice 'Intento X de Y'",
    "intento" not in salida1.lower(),
)
revisar(
    "el mensaje de token inválido es corto y claro",
    len(cfg.TEXTO_TOKEN_INVALIDO) <= 45
    and "inválido" in cfg.TEXTO_TOKEN_INVALIDO.lower()
    and "código" in cfg.TEXTO_TOKEN_INVALIDO.lower(),
)
pedir_token = cliente.get(f"/voice/pedir-tercero?call_sid={CALL_SID}").get_data(as_text=True)
revisar(
    "al pedir el token la voz dice 'código token'",
    "código token" in pedir_token.lower(),
)
revisar(
    "el token deja una pausa DESPUÉS de hablar (para marcar sin cortes)",
    "<Say" in pedir_token and "<Pause" in pedir_token
    and pedir_token.index("<Say") < pedir_token.index("<Pause"),
)
# El token suele ser mas largo, asi que conserva el silencio COMPLETO
# (TIMEOUT_ENTRADA) y tambien avisa aunque no marquen (reintenta el token en vez
# de cortar la llamada en seco).
revisar(
    "el token usa el silencio completo (TIMEOUT_ENTRADA, no el corto)",
    f'timeout="{cfg.TIMEOUT_ENTRADA}"' in pedir_token,
)
revisar(
    "el token avisa aunque no marquen (no cuelga solo)",
    'actionOnEmptyResult="true"' in pedir_token,
)
revisar(
    "intento 1 deja la nota 'código token inválido' en el panel",
    "código token inválido" in paneles[-1]["texto"].lower(),
)
revisar("intento 1 NO pide validacion (sin botones validar3)", not any(
    "validar3" in b for b in botones(paneles[-1])
))
revisar("el panel muestra 'Código token1: 1111'", "Código token1: 1111" in paneles[-1]["texto"])
revisar(
    "el panel deja hueco para token2 y token3",
    "Código token2:" in paneles[-1]["texto"] and "Código token3:" in paneles[-1]["texto"],
)
revisar("intento 1 sigue con UN solo mensaje", len(mensajes) == 1)

salida2 = marcar("/voice/recibir-tercero", digits="2222", query=f"?call_sid={CALL_SID}")
revisar("intento 2 del tercero -> voz de token invalido", "Token inválido" in salida2)
revisar(
    "intento 2 deja la nota en el panel",
    "código token inválido" in paneles[-1]["texto"].lower(),
)
revisar("intento 2 NO pide validacion", not any(
    "validar3" in b for b in botones(paneles[-1])
))

salida3 = marcar("/voice/recibir-tercero", digits="8443", query=f"?call_sid={CALL_SID}")
revisar("intento 3 ya NO dice token invalido", "Token inválido" not in salida3)
revisar("intento 3 pide la validacion (Fase 2)", "Fase 2" in paneles[-1]["texto"])
revisar(
    "intento 3 trae boton de validar tercero",
    f"validar3_{CALL_SID}" in botones(paneles[-1]),
)
revisar("el token 1 se ve en el panel", "Código token1: 1111" in paneles[-1]["texto"])
revisar("el token 2 se ve en el panel", "Código token2: 2222" in paneles[-1]["texto"])
revisar("el token 3 se ve en el panel", "Código token3: 8443" in paneles[-1]["texto"])
revisar(
    "los 3 tokens se ven a la vez (uno por intento)",
    all(f"Código token{i}:" in paneles[-1]["texto"] for i in (1, 2, 3)),
)
revisar(
    "el estado quedo esperando validacion del tercero",
    servidor.llamadas_activas[CALL_SID]["estado"] == servidor.ESPERANDO_3,
)
revisar("todo el tercero sigue en UN solo mensaje", len(mensajes) == 1)

# 4) El operador invalida el tercero -> reinicia el proceso -----------------
aprobar(servidor.RECHAZADO_3)
salida4 = cliente.get(f"/voice/fase2?call_sid={CALL_SID}").get_data(as_text=True)
revisar("RECHAZADO_3 dice 'Hubo un error'", "Hubo un error" in salida4)
revisar("RECHAZADO_3 vuelve a pedir el primer numero", "pedir-primero" in salida4)
ficha = servidor.llamadas_activas[CALL_SID]
revisar(
    "los numeros guardados se limpiaron",
    ficha["primero"] is None and ficha["segundo"] is None and ficha["tercero"] is None,
)
revisar("las horas se limpiaron", ficha["horas"] == {})
revisar("el contador del tercero se reinicia", ficha["intentos_tercero"] == 0)
revisar("el estado vuelve a iniciada", ficha["estado"] == servidor.INICIADA)

print()
if fallos:
    print(f"RESULTADO: {len(fallos)} comprobacion(es) fallaron.")
    raise SystemExit(1)
print("RESULTADO: todas las comprobaciones pasaron.")