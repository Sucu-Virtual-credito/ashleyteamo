"""Prueba local de los comandos de Telegram para administrar numeros.txt.

No usa red ni la base de datos: intercepta los mensajes a Telegram y trabaja
sobre un archivo temporal, asi que no toca tu numeros.txt real.

Uso:  .venv\\Scripts\\python.exe prueba_comandos_numeros.py
"""

import os
import sys
import tempfile
import types
from pathlib import Path

# Permitir imprimir emojis aunque la consola de Windows use cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

os.environ.setdefault("WEBHOOK_BASE_URL", "http://test.local")

import config as cfg  # noqa: E402
import servidor  # noqa: E402

# --- Trabajar sobre un numeros.txt temporal, no el real ----------------------
tmp = Path(tempfile.mkdtemp()) / "numeros.txt"
cfg.ARCHIVO_NUMEROS = tmp

# --- Interceptar los mensajes a Telegram -------------------------------------
enviados = []
servidor._tg_mensaje = lambda texto, teclado=None: enviados.append(texto)
servidor.bd = types.SimpleNamespace(
    ultimas=lambda *a, **k: [],
    resumen=lambda *a, **k: "",
)

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


# 1) normalizacion de telefonos ----------------------------------------------
revisar("normaliza '573001234567' -> '+573001234567'",
        cfg.normalizar_numero("573001234567") == "+573001234567")
revisar("normaliza '+57 300 123 4567' (quita espacios)",
        cfg.normalizar_numero("+57 300 123 4567") == "+573001234567")
revisar("normaliza '+57-300-123-4567' (quita guiones)",
        cfg.normalizar_numero("+57-300-123-4567") == "+573001234567")
try:
    cfg.normalizar_numero("abc")
    revisar("rechaza un texto que no es telefono", False)
except ValueError:
    revisar("rechaza un texto que no es telefono", True)

# 2) /agregar ----------------------------------------------------------------
r = comando("/agregar +573001234567")
revisar("/agregar guarda el numero", cfg.numeros_a_llamar() == ["+573001234567"])
revisar("/agregar confirma con ✅", "✅" in r)

r = comando("/agregar +573001234567")
revisar("/agregar no duplica (>ya estaba<)", "ya estaban" in r)
revisar("la lista sigue con un solo numero", len(cfg.numeros_a_llamar()) == 1)

r = comando("/agregar +573109876543 +573001112222")
revisar("/agregar acepta varios a la vez", len(cfg.numeros_a_llamar()) == 3)

r = comando("/agregar")
revisar("/agregar sin argumento avisa (⚠️)", "⚠️" in r)

r = comando("/agregar abc")
revisar("/agregar con dato invalido avisa (⚠️)", "⚠️" in r)
revisar("el dato invalido no entra a la lista", len(cfg.numeros_a_llamar()) == 3)

# 3) /numeros ----------------------------------------------------------------
r = comando("/numeros")
revisar("/numeros lista los 3 numeros",
        sum(1 for ln in r.splitlines() if ln.startswith(tuple("123456789"))) == 3)
revisar("/numeros incluye cada telefono guardado",
        all(n in r for n in cfg.numeros_a_llamar()))

# 4) /quitar -----------------------------------------------------------------
r = comando("/quitar +573109876543")
revisar("/quitar elimina el numero", "+573109876543" not in cfg.numeros_a_llamar())
revisar("/quitar confirma con 🗑️", "🗑️" in r)

r = comando("/quitar +573999999999")
revisar("/quitar avisa si no existe (🟡)", "🟡" in r)

# 5) /limpiar ----------------------------------------------------------------
r = comando("/limpiar")
revisar("/limpiar vacia la lista", cfg.numeros_a_llamar() == [])
revisar("/limpiar confirma", "vaciada" in r)

# 6) El archivo en disco quedo en formato valido -----------------------------
comando("/agregar +573001234567")
contenido = tmp.read_text(encoding="utf-8")
revisar("numeros.txt conserva el encabezado con #",
        contenido.startswith("#") and "+573001234567" in contenido)

# 7) El menu de ayuda menciona los comandos nuevos ---------------------------
r = comando("/ayuda_desconocida")
revisar("la ayuda menciona /agregar, /quitar y /limpiar",
        "/agregar" in r and "/quitar" in r and "/limpiar" in r)

print()
if fallos:
    print(f"{len(fallos)} FALLO(S): " + "; ".join(fallos))
    raise SystemExit(1)
print("Todas las comprobaciones pasaron.")