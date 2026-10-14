"""Prueba de las reglas de la llamada (nuevas).

Comprueba que:
    1) tras el 2.º numero la voz dice "estamos validando sus datos...";
    2) si en Telegram se INVALIDA -> dice "Datos incorrectos" y pide 1.º y 2.º;
    3) si se VALIDA -> pide el codigo token (y lo pide 3 veces);
    4) el nombre del 3.º dato es "Codigo token".

No usa red ni Twilio: todo simulado.

Uso:  .venv\\Scripts\\python.exe prueba_reglas_llamada.py
"""

import os
import sys
import types

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

os.environ["WEBHOOK_BASE_URL"] = "http://test.local"

import config as cfg  # noqa: E402
import servidor as s  # noqa: E402

s._tg_mensaje = lambda *a, **k: 1
s._tg_panel = lambda *a, **k: None
s._bd = lambda *a, **k: None
s._EN_HILO = False  # todo sincrono en pruebas (sin hilos)
s._AUTORIZAR_ACTIVO = False  # en pruebas no se comprueban admins (sin red)
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


def iniciar(sid):
    c.post("/voice/start", data={"CallSid": sid})
    c.post("/voice/recibir-primero", data={"CallSid": sid, "Digits": "1048068059"})
    return c.post(
        f"/voice/recibir-segundo?call_sid={sid}",
        data={"CallSid": sid, "Digits": "2580"},
    ).get_data(as_text=True)


def boton(sid, data):
    c.post(
        "/telegram/webhook",
        json={
            "callback_query": {
                "id": "cb",
                "data": f"{data}_{sid}",
                "from": {"username": "tester"},
                "message": {"chat": {"id": int(cfg.TELEGRAM_GROUP_CHAT_ID)}, "message_id": 1},
            }
        },
    )


# --- 1) Tras el 2.º número dice "validando sus datos" -----------------------
SID = "CAtestREGLA1000000000000000000000001"
r = iniciar(SID)
revisar("tras el 2.º numero dice 'estamos validando sus datos'", "validando" in r.lower())
revisar("dice 'manténgase en línea'", "línea" in r.lower())
revisar("NO menciona Telegram", "telegram" not in r.lower())
revisar(
    "consulta seguido (pausa corta) para reaccionar rápido",
    f'<Pause length="{cfg.INTERVALO_SONDEO}"' in r,
)

# --- 1b) Si no se valida: fase1 NO repite al instante, pero SI tras 10s -----
r = c.get(f"/voice/fase1?call_sid={SID}").get_data(as_text=True)
revisar(
    "fase1 recien empezada NO repite todavia (aun no pasan 10s)",
    "validando" not in r.lower(),
)
revisar("fase1 consulta seguido (pausa corta)", f'<Pause length="{cfg.INTERVALO_SONDEO}"' in r)

# Simulamos que pasaron INTERVALO_ESPERA segundos sin validar
import time as _t

s.llamadas_activas[SID]["ultimo_aviso"] = _t.time() - (cfg.INTERVALO_ESPERA + 1)
r = c.get(f"/voice/fase1?call_sid={SID}").get_data(as_text=True)
revisar("tras INTERVALO_ESPERA segundos SIN validar, repite el aviso", "validando" in r.lower())

# --- 2) Si se INVALIDA -> "Datos incorrectos" y pide el 1.º ----------------
SID2 = "CAtestREGLA2000000000000000000000002"
iniciar(SID2)
boton(SID2, "invalidar12")
r = c.get(f"/voice/fase1?call_sid={SID2}").get_data(as_text=True)
revisar("al invalidar dice 'Datos incorrectos'", "Datos incorrectos" in r)
revisar("al invalidar vuelve a pedir el 1.º numero", "pedir-primero" in r)

# --- 3) Si se VALIDA -> pide el código token -------------------------------
SID3 = "CAtestREGLA3000000000000000000000003"
iniciar(SID3)
boton(SID3, "validar12")
r = c.get(f"/voice/fase1?call_sid={SID3}").get_data(as_text=True)
revisar("al validar pide el código token", "pedir-tercero" in r)
r2 = c.get(f"/voice/pedir-tercero?call_sid={SID3}").get_data(as_text=True)
revisar("el 3.º dato se llama 'Código token'", "Código token" in r2)
revisar("el 3.º dato no dice 'tercer número'", "tercer número" not in r2.lower())

# --- 4) El token se pide 3 veces -------------------------------------------
revisar("INTENTOS_TERCERO = 3", cfg.INTENTOS_TERCERO == 3)
for i in range(1, 4):
    r = c.post(
        f"/voice/recibir-tercero?call_sid={SID3}",
        data={"CallSid": SID3, "Digits": f"111{i}"},
    ).get_data(as_text=True)
    if i < 3:
        revisar(
            f"intento {i} del token se rechaza y se vuelve a pedir",
            "Token inválido" in r and "recibir-tercero" in r,
        )
        revisar(
            f"intento {i} NO repite 'Ingrese Código token'",
            "ingrese código token" not in r.lower(),
        )
    else:
        revisar("intento 3 del token pasa a validación (fase2)", "fase2" in r)

print()
if fallos:
    print(f"RESULTADO: {len(fallos)} comprobacion(es) fallaron.")
    raise SystemExit(1)
print("RESULTADO: todas las comprobaciones pasaron.")