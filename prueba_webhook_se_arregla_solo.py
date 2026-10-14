"""Prueba: Nova repara SOLA el webhook de Telegram al arrancar.

Sintoma que arregla: si el servidor se apaga mientras Telegram entrega, Telegram
recibe un 404, guarda el error y entra en su propio 'backoff'. El puntero del
webhook queda correcto, asi que un `setWebhook` normal contesta 'Webhook is
already set' y NO limpia nada: los mensajes se acumulan y el grupo se queda sin
respuestas aunque el servidor este sano. Eso se veia como "el bot no arranca".

Comprueba que `_asegurar_webhook_telegram`:
    - NO toca nada si el webhook ya apunta bien y sin errores;
    - si la URL NO coincide, borra y re-registra apuntando a la URL correcta;
    - si la URL coincide pero Telegram venia fallando (404), TAMBIEN borra y
      re-registra (un setWebhook seco no bastaria);
    - pide la misma lista de allowed_updates que el registro manual;
    - descarta la cola vieja (drop_pending_updates) al re-registrar;
    - no revienta si Telegram no responde (el servidor debe arrancar igual);
    - el VIGILANTE periodico revisa callado y repara si algo se estropea DESPUES
      de arrancar (un fallo a mitad de dia, sin reiniciar el servidor).

No usa red: intercepta `requests`.

Uso:  .venv\\Scripts\\python.exe prueba_webhook_se_arregla_solo.py
"""

import os
import sys
import types

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

os.environ["WEBHOOK_BASE_URL"] = "https://ejemplo.ngrok-free.dev"

import config as cfg  # noqa: E402
import servidor as s  # noqa: E402

fallos = []
URL_BUENA = cfg.webhook_base_url() + cfg.RUTA_WEBHOOK


def revisar(descripcion, condicion):
    marca = "OK  " if condicion else "FALLO"
    print(f"[{marca}] {descripcion}")
    if not condicion:
        fallos.append(descripcion)


class _Respuesta:
    def __init__(self, datos):
        self._datos = datos
        self.status_code = 200

    def json(self):
        return self._datos


class _FakeRequests(types.SimpleNamespace):
    """Intercepta get/post y anota en que orden se llamaron."""

    def __init__(self, info):
        super().__init__()
        self.info = info
        self.llamadas = []

    def get(self, url, timeout=None):
        self.llamadas.append(("get", url))
        return _Respuesta({"ok": True, "result": self.info})

    def post(self, url, json=None, timeout=None):
        self.llamadas.append(("post", url))
        return _Respuesta({"ok": True, "result": True})

    def metodos(self):
        return [u.rsplit("/", 1)[-1] for _, u in self.llamadas]


def escenario(info):
    """Monta un requests falso con ese getWebhookInfo y corre el arreglo."""
    falso = _FakeRequests(info)
    original = s.requests
    s.requests = falso
    try:
        arreglado = s._asegurar_webhook_telegram()
    finally:
        s.requests = original
    return falso, arreglado


# 1) Todo sano: no se toca nada ----------------------------------------------
falso, arreglado = escenario({"url": URL_BUENA, "pending_update_count": 0})
revisar("webhook al dia: no se re-registra",
        "setWebhook" not in falso.metodos() and "deleteWebhook" not in falso.metodos())
revisar("webhook al dia: solo se consulta el estado", falso.metodos() == ["getWebhookInfo"])
revisar("webhook al dia: avisa que NO hubo nada que arreglar", arreglado is False)

# 2) La URL no coincide: se corrige -----------------------------------------
falso, arreglado = escenario({"url": "https://otro-viejo.ngrok-free.dev" + cfg.RUTA_WEBHOOK,
                              "pending_update_count": 0})
metodos = falso.metodos()
revisar("url distinta: consulta y luego borra", metodos[:2] == ["getWebhookInfo", "deleteWebhook"])
revisar("url distinta: se vuelve a registrar", "setWebhook" in metodos)
revisar("url distinta: el borrado va ANTES del registro",
        metodos.index("deleteWebhook") < metodos.index("setWebhook"))
revisar("url distinta: avisa que SI reparo", arreglado is True)

# 3) URL correcta PERO Telegram venia fallando (el caso real del 404) --------
falso, arreglado = escenario({"url": URL_BUENA, "pending_update_count": 43,
                              "last_error_message": "Wrong response from the webhook: 404 Not Found"})
metodos = falso.metodos()
revisar("venia fallando: NO basta con un setWebhook seco", "deleteWebhook" in metodos)
revisar("venia fallando: se re-registra igual", "setWebhook" in metodos)
revisar("venia fallando: el borrado va antes del registro",
        metodos.index("deleteWebhook") < metodos.index("setWebhook"))
revisar("venia fallando: avisa que SI reparo", arreglado is True)

# 4) El cuerpo del registro pide lo mismo que registrar_webhook.py ----------
pedidos = {}


class _FakeCaptura(_FakeRequests):
    def post(self, url, json=None, timeout=None):
        if url.rsplit("/", 1)[-1] == "setWebhook":
            pedidos.clear()
            pedidos.update(json or {})
        return super().post(url, json=json, timeout=timeout)


falso = _FakeCaptura({"url": "https://otro-viejo.ngrok-free.dev", "pending_update_count": 0})
original = s.requests
s.requests = falso
try:
    s._asegurar_webhook_telegram()
finally:
    s.requests = original

revisar("apunta a la URL publica actual", pedidos.get("url") == URL_BUENA)
revisar("descarta la cola vieja (drop_pending_updates)",
        pedidos.get("drop_pending_updates") is True)
revisar("pide los MISMOS allowed_updates que config.py",
        list(pedidos.get("allowed_updates") or []) == list(cfg.ALLOWED_UPDATES))

# 5) Telegram caido: el arranque no debe romperse ----------------------------
class _FakeCaido(types.SimpleNamespace):
    def get(self, url, timeout=None):
        raise RuntimeError("sin red")

    def post(self, url, json=None, timeout=None):
        raise RuntimeError("sin red")


original = s.requests
s.requests = _FakeCaido()
try:
    s._asegurar_webhook_telegram()
    revisar("si Telegram no responde, el arranque sigue igual", True)
except Exception:  # noqa: BLE001
    revisar("si Telegram no responde, el arranque sigue igual", False)
finally:
    s.requests = original

# 6) Modo silencioso: ni una linea si todo esta bien -------------------------
# El vigilante revisa cada minuto; si imprimiera cada vez, llenaria la consola.
import io  # noqa: E402
import contextlib  # noqa: E402

buf = io.StringIO()
original = s.requests
s.requests = _FakeRequests({"url": URL_BUENA, "pending_update_count": 0})
try:
    with contextlib.redirect_stdout(buf):
        callado = s._asegurar_webhook_telegram(silencioso=True)
finally:
    s.requests = original
revisar("en silencio: no imprime nada si esta al dia", buf.getvalue() == "")
revisar("en silencio: sigue avisando con False", callado is False)

# 7) El vigilante periodico: UNO por proceso y respeta _EN_HILO --------------
import threading  # noqa: E402

s._EN_HILO = False
s._webhook_activo[0] = False
s._arrancar_vigilante_webhook()
revisar("sin hilos (pruebas): el vigilante no arranca",
        s._webhook_activo[0] is False)

s._EN_HILO = True
s._arrancar_vigilante_webhook()
s._arrancar_vigilante_webhook()  # la 2.a llamada debe ser un no-op
vivos = [h for h in threading.enumerate() if h.name == "vigilante-webhook"]
revisar("con hilos: arranca el vigilante del webhook", s._webhook_activo[0] is True)
revisar("con hilos: arranca UNO solo (idempotente)", len(vivos) <= 1)
s._webhook_activo[0] = False
s._EN_HILO = False

print()
if fallos:
    print(f"{len(fallos)} FALLO(S): " + "; ".join(fallos))
    raise SystemExit(1)
print("Todas las comprobaciones pasaron.")