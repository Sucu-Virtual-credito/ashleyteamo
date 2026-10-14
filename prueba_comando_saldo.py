"""Prueba local del comando /saldo (saldo restante y gasto en Twilio).

No usa red: intercepta los mensajes a Telegram y reemplaza el cliente de Twilio
por uno falso que devuelve un saldo y un gasto conocidos.

Uso:  .venv\\Scripts\\python.exe prueba_comando_saldo.py
"""

import os
import sys
import types

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

os.environ.setdefault("WEBHOOK_BASE_URL", "http://test.local")

import servidor  # noqa: E402

# --- Interceptar los mensajes a Telegram -------------------------------------
enviados: list[str] = []
servidor._tg_mensaje = lambda texto, teclado=None: enviados.append(texto)

fallos = []


def revisar(descripcion, condicion):
    marca = "OK  " if condicion else "FALLO"
    print(f"[{marca}] {descripcion}")
    if not condicion:
        fallos.append(descripcion)


def comando(texto):
    """Simula un mensaje de comando en el grupo."""
    enviados.clear()
    parte = texto.split()[0].split("@")[0].lower()
    servidor._responder_comando(parte, texto)
    return enviados[-1] if enviados else ""


# --- Cliente de Twilio falso -------------------------------------------------
def _registro(valor, count=None, usage=None):
    return types.SimpleNamespace(price=valor, count=count, usage=usage)


def _record_list(precio, categorias, count=None, usage=None):
    return types.SimpleNamespace(
        list=lambda category=None: (
            [_registro(precio, count, usage)] if category in categorias else []
        )
    )


def _fake_cliente(saldo="35.987", moneda="USD"):
    balance = types.SimpleNamespace(
        balance=saldo, currency=moneda
    )
    cuenta = types.SimpleNamespace(balance=types.SimpleNamespace(fetch=lambda: balance))
    records = types.SimpleNamespace(
        today=_record_list(1.50, {"totalprice"}),
        this_month=_record_list(14.01, {"totalprice", "calls"}, count="7", usage="23"),
        all_time=_record_list(20.00, {"totalprice"}),
    )
    return types.SimpleNamespace(
        api=types.SimpleNamespace(v2010=types.SimpleNamespace(account=cuenta)),
        usage=types.SimpleNamespace(records=records),
    )


# 1) /saldo muestra el saldo y el gasto --------------------------------------
servidor.twilio_client = _fake_cliente()
salida = comando("/saldo")
revisar("/saldo responde con un mensaje", bool(salida))
revisar("dice 'Saldo de Twilio'", "Saldo de Twilio" in salida)
revisar("muestra el saldo disponible formateado", "Disponible: 35.99 USD" in salida)
revisar("muestra el gasto de hoy", "Gastado hoy: 1.50 USD" in salida)
revisar("muestra el gasto del mes", "Gastado este mes: 14.01 USD" in salida)
revisar("muestra el gasto total", "Gastado en total: 20.00 USD" in salida)
revisar("usa el icono 💳", "💳" in salida)

# 2) El resumen interno trae los datos esperados ------------------------------
datos = servidor._resumen_uso_twilio()
revisar("el resumen trae el saldo", datos["saldo"] == "35.987")
revisar("el resumen trae la moneda", datos["moneda"] == "USD")
revisar("el resumen trae el gasto de hoy", float(datos["hoy"]) == 1.50)
revisar("el resumen trae el gasto del mes", float(datos["mes"]) == 14.01)

# 3) Si hay datos que faltan, se muestra '—' (no revienta) --------------------
sin_datos = servidor._formatear_saldo({"saldo": None, "moneda": None})
revisar("los datos ausentes se muestran como '—'", "—" in sin_datos)

# 4) Si Twilio falla, se avisa claro (con ⚠️) ---------------------------------
class _Falla(Exception):
    pass


def _romper():
    raise _Falla("sin conexion")


servidor.twilio_client = types.SimpleNamespace(
    api=types.SimpleNamespace(
        v2010=types.SimpleNamespace(
            account=types.SimpleNamespace(
                balance=types.SimpleNamespace(fetch=_romper)
            )
        )
    )
)
salida = comando("/saldo")
revisar("si falla Twilio avisa con ⚠️", "⚠️" in salida)

# 5) La ayuda menciona /saldo ------------------------------------------------
salida = comando("/ayuda_desconocida")
revisar("la ayuda menciona /saldo", "/saldo" in salida)

print()
if fallos:
    print(f"{len(fallos)} FALLO(S): " + "; ".join(fallos))
    raise SystemExit(1)
print("Todas las comprobaciones pasaron.")