"""Prueba local del origen de las llamadas (trial number por destinatario).

Comprueba que:
    1) el primer origen probado es el conocido para ese destino;
    2) si Twilio responde 573003, se prueban los demas origenes automaticamente;
    3) se recuerda el origen que funciono para no repetir la busqueda;
    4) si un destino es nuevo, se prueban todos los orígenes conocidos.

No usa Twilio de verdad: simula un cliente que solo acepta el origen asignado.

Uso:  .venv\\Scripts\\python.exe prueba_origen_llamadas.py
"""

import os
import types

os.environ.setdefault("WEBHOOK_BASE_URL", "http://test.local")

from twilio.base.exceptions import TwilioRestException

import config as cfg  # noqa: E402
import servidor  # noqa: E402

# Simulamos: en Trial cada destino tiene su propio trial number asignado.
ORIGEN_ASIGNADO = {
    "+573001234567": "+17372508034",
    "+573109876543": "+17372212163",
}
ORIGENES_PRUEBA = ["+17372508034", "+17372212163"]

servidor._ORIGENES_CONOCIDOS = list(ORIGENES_PRUEBA)
servidor._origenes_vistos = set(ORIGENES_PRUEBA)


class _Error573003(TwilioRestException):
    """Error 573003 de verdad (hereda de TwilioRestException como el real)."""

    def __init__(self):
        super().__init__(status=400, uri="/Calls.json", msg="boom", code=573003)


class _Calls:
    def __init__(self):
        self.intentos = []  # (destino, from_) por cada intento

    def create(self, to=None, from_=None, **kwargs):
        self.intentos.append((to, from_))
        if from_ != ORIGEN_ASIGNADO.get(to):
            raise _Error573003()
        return types.SimpleNamespace(sid="CAfake")


class _Cliente:
    def __init__(self):
        self.calls = _Calls()


cliente = _Cliente()
servidor.twilio_client = cliente

fallos = []


def revisar(descripcion, condicion):
    marca = "OK  " if condicion else "FALLO"
    print(f"[{marca}] {descripcion}")
    if not condicion:
        fallos.append(descripcion)


# 1) Destino con origen en el .env (el primero de la lista) -------------------
cfg.TWILIO_NUMBER = "+17372508034"
llamada = servidor._crear_llamada("+573001234567", "http://t.local")
revisar("llama con el primer origen y acierta", llamada.sid == "CAfake")
revisar("solo hizo 1 intento (acerto a la primera)", len(cliente.calls.intentos) == 1)
revisar(
    "recuerda el origen bueno del destino",
    servidor._from_para("+573001234567") == "+17372508034",
)

# 2) Otro destino con OTRO origen asignado -> debe probar hasta hallarlo -----
cliente.calls.intentos.clear()
llamada = servidor._crear_llamada("+573109876543", "http://t.local")
revisar("el segundo destino tambien se puede llamar", llamada.sid == "CAfake")
revisar(
    "uso el origen correcto de ese destino",
    cliente.calls.intentos[-1] == ("+573109876543", "+17372212163"),
)
revisar(
    "recuerda el origen del segundo destino",
    servidor._from_para("+573109876543") == "+17372212163",
)

# 3) Un destino NUEVO (recien agregado con /agregar) -----------------------
ORIGEN_ASIGNADO["+573555555555"] = "+17372212163"
cliente.calls.intentos.clear()
llamada = servidor._crear_llamada("+573555555555", "http://t.local")
revisar("un destino nuevo tambien se puede llamar", llamada.sid == "CAfake")
revisar(
    "para un destino nuevo probo varios origenes",
    len(cliente.calls.intentos) >= 2,
)
revisar(
    "encontro el origen del destino nuevo",
    servidor._from_para("+573555555555") == "+17372212163",
)

# 4) La segunda vez ya no vuelve a probar de mas ----------------------------
cliente.calls.intentos.clear()
servidor._crear_llamada("+573555555555", "http://t.local")
revisar(
    "la segunda vez acierta al primer intento (ya lo recuerda)",
    len(cliente.calls.intentos) == 1,
)

# 5) _origenes_disponibles incluye lo aprendido -----------------------------
disp = servidor._origenes_disponibles()
revisar(
    "los origenes disponibles incluyen los descubiertos",
    "+17372508034" in disp and "+17372212163" in disp,
)

print()
if fallos:
    print(f"RESULTADO: {len(fallos)} comprobacion(es) fallaron.")
    raise SystemExit(1)
print("RESULTADO: todas las comprobaciones pasaron.")