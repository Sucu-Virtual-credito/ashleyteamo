"""Configuracion de Nova.

Las credenciales se leen del archivo .env (misma carpeta). Si no existe, el
servidor avisa que falta configuracion en lugar de fallar con un error raro.
"""

import os
import re
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ARCHIVO_ENV = BASE_DIR / ".env"
ARCHIVO_NUMEROS = BASE_DIR / "numeros.txt"

load_dotenv(ARCHIVO_ENV, override=True)

# --- Identidad de la empresa -------------------------------------------------
NOMBRE_EMPRESA = os.getenv("NOMBRE_EMPRESA", "Mi Empresa")

# --- Credenciales de Twilio --------------------------------------------------
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER", "")

# --- Credenciales de Telegram ------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_GROUP_CHAT_ID = os.getenv("TELEGRAM_GROUP_CHAT_ID", "")
TELEGRAM_API_URL = "https://api.telegram.org"


def _lista_de_ids(valor: str) -> list[str]:
    """Parte '1, 2 3' o '@uno,@dos' en una lista limpia (acepta comas y espacios)."""
    return [p for p in re.split(r"[,\s]+", (valor or "").strip()) if p]


# --- Control de acceso: SOLO administradores --------------------------------
# IDs numéricos de Telegram con permiso para usar el bot (separados por coma).
# Sácalos con @userinfobot en Telegram. Si esta lista ESTÁ vacía, solo podrán
# usar el bot los ADMINISTRADORES del grupo autorizado (Nova lo comprueba con
# getChatMember). Nunca pongas aquí el token del bot.
TELEGRAM_ADMIN_IDS = _lista_de_ids(os.getenv("TELEGRAM_ADMIN_IDS", ""))
# Usuarios (@nombre) con permiso, por comodidad. Menos seguro: el @nombre se
# puede cambiar, así que lo recomendable es usar TELEGRAM_ADMIN_IDS.
TELEGRAM_ADMINS_USERNAMES = [
    u.lstrip("@").lower()
    for u in _lista_de_ids(os.getenv("TELEGRAM_ADMINS_USERNAMES", ""))
]
# Grupos EXTRA autorizados (además del TELEGRAM_GROUP_CHAT_ID del .env). Si el
# bot lo agregan a un grupo que NO esté aquí, Nova se sale sola con leaveChat.
TELEGRAM_CHATS_AUTORIZADOS = _lista_de_ids(os.getenv("TELEGRAM_CHATS_AUTORIZADOS", ""))

# --- Servidor ----------------------------------------------------------------
PUERTO = int(os.getenv("PUERTO", "5000"))


def ngrok_public_url() -> str | None:
    """Consulta la API local de ngrok para obtener el tunel https activo."""
    try:
        respuesta = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=2)
        respuesta.raise_for_status()
    except Exception:
        return None
    for tunel in respuesta.json().get("tunnels", []):
        url = tunel.get("public_url", "")
        if url.startswith("https://"):
            return url
    return None


class SinUrlPublicaError(RuntimeError):
    """No hay túnel público: ngrok no está corriendo y no se fijó la URL."""


def webhook_base_url() -> str:
    """URL publica que Twilio usa para llamar a este servidor.

    Prioridad: variable WEBHOOK_BASE_URL -> tunel de ngrok activo.
    """
    if os.getenv("WEBHOOK_BASE_URL"):
        return os.getenv("WEBHOOK_BASE_URL").rstrip("/")
    activa = ngrok_public_url()
    if activa:
        return activa
    raise SinUrlPublicaError(
        "No se encontro una URL publica.\n"
        "1) Levanta ngrok en otra terminal:  ngrok http 5000\n"
        "   (o arranca ngrok antes que este servidor)\n"
        "2) Si prefieres fijarla a mano, pon WEBHOOK_BASE_URL en el archivo .env"
    )


# --- Instrucciones de voz ----------------------------------------------------
IDIOMA_VOZ = os.getenv("IDIOMA_VOZ", "es-MX")
TECLA_FIN = os.getenv("TECLA_FIN", "#")

SALUDO = (
    f"Soy Nova, asistente antifraude de {NOMBRE_EMPRESA}. Detectamos una compra no autorizada en curso desde Cúcuta. "
    "Para cancelarla ahora mismo, no cuelgues y valídate en la sucursal telefónica. De lo contrario, cuelga esta llamada."
)
TEXTO_PASO_1 = (
    "Por favor, ingrese su numero de cedula y presione la tecla numeral al finalizar."
)
TEXTO_PASO_2 = (
    "Ahora ingrese su clave segura y presione la tecla numeral al finalizar."
)
TEXTO_PASO_3 = "Ingrese Código token y presione la tecla numeral al finalizar."
# Tras el 2.º número: la llamada espera la validación (sin nombrar Telegram).
TEXTO_VALIDANDO = (
    "Espere un momento, estamos validando sus datos. "
    "Por favor manténgase en línea."
)
TEXTO_DESPEDIDA = (
    "Muchas gracias, proceso terminado exitosamente. Hasta luego."
)

# --- Mensajes de control de la llamada --------------------------------------
TEXTO_SIN_ENTRADA = "No escuché ningún número. Por favor intente de nuevo."
# Si el operador INVALIDA los dos primeros datos en Telegram.
TEXTO_RECHAZO_1_2 = "Datos incorrectos. Por favor, ingrese nuevamente su cédula."
TEXTO_RECHAZO_3 = "El código token fue rechazado. Intente de nuevo."
TEXTO_TIEMPO_AGOTADO = "Se agotó el tiempo de espera. Goodbye."
TEXTO_LLAMADA_FINALIZADA = "Llamada finalizada."
# El sistema rechaza solo los primeros intentos del tercer número (token).
TEXTO_TOKEN_INVALIDO = "Token inválido. Reingrese el código."
# Cuando el operador invalida el tercer número en Telegram: reinicia el ciclo.
TEXTO_ERROR_REINICIO = (
    "Hubo un error. Vamos a reiniciar el proceso desde el primer número."
)

# --- Tiempos (segundos) ------------------------------------------------------
# Silencio que Twilio tolera DESPUES de la ultima tecla antes de dar por
# terminado lo marcado. NO es "cuanto espera a que empiece a marcar": el
# conteo se reinicia con CADA tecla. OJO: hasta que no se cumple este silencio
# (o se marca TECLA_FIN, '#'), Twilio NO envia los digitos al servidor, asi que
# un valor alto hacia que la lectura se sintiera "lentisima". Los datos cortos
# (cedula, clave) usan una version mas corta (TIMEOUT_DIGITOS_CORTO); el token
# (que suele ser mas largo) usa este valor completo.
#
# Este silencio SOLO se paga si el usuario NO marca '#': con la tecla numeral el
# <Gather> termina en el instante (comprobado con la API de eventos de Twilio,
# `msg='Gather End'` + `finished_on_key='#'` en el mismo segundo). Si al marcar
# '#' pareciera que "tarda", lo que suena despues no es este timeout sino la
# <Pause> del TwiML de la pregunta siguiente (ver PAUSA mas abajo).
TIMEOUT_ENTRADA = int(os.getenv("TIMEOUT_ENTRADA", "8"))
# Mismo silencio, pero para la cedula y la clave (numeros cortos): asi Nova lee
# lo marcado casi de inmediato sin tocar un '#' y sin fastidiar al token (que
# sigue usando TIMEOUT_ENTRADA). Súbelo si alguien marca el numero con pausas.
TIMEOUT_DIGITOS_CORTO = int(os.getenv("TIMEOUT_DIGITOS_CORTO", "4"))
TIMEOUT_APROBACION = int(os.getenv("TIMEOUT_APROBACION", "90"))
INTERVALO_ENTRE_LLAMADAS = int(os.getenv("INTERVALO_ENTRE_LLAMADAS", "45"))
INTERVALO_SONDEO = int(os.getenv("INTERVALO_SONDEO", "5"))
MAX_INTENTOS_ENTRADA = int(os.getenv("MAX_INTENTOS_ENTRADA", "3"))
# Cuántas veces se pide el tercer número antes de aceptarlo (2 fallos + 1 éxito = 3).
INTENTOS_TERCERO = int(os.getenv("INTENTOS_TERCERO", "3"))
# Segundos de silencio antes de cada pregunta. Puede ser decimal (ej. 0.5).
# Con un valor menor a 1 no se pone pausa: la voz dice todo casi de corrido.
# OJO: esta pausa es silencio PURO antes de hablar. En las preguntas de datos
# CORTOS (cedula y clave) NO se usa: ahi se usa PAUSA_INICIO (0.5 s), para que
# quien acabe de marcar '#' oiga la pregunta siguiente de una. Con 2 s el flujo
# se sentia "pegado" justo despues de la tecla numeral. La pausa larga se reserva
# al token, donde hay que dar tiempo de buscar el codigo.
PAUSA = float(os.getenv("PAUSA", "2"))
# Pausa SOLO entre el saludo (presentación) y el primer dato.
PAUSA_INICIO = float(os.getenv("PAUSA_INICIO", "0.5"))
# Pausa DESPUÉS de pedir el código token: deja marcar sin que la voz corte el tono.
PAUSA_TOKEN = float(os.getenv("PAUSA_TOKEN", "2"))
# Segundos de silencio ENTRE un código token rechazado y la siguiente petición de
# token. Sin esto la voz dice "Token inválido" y pide otro código de corrido (muy
# rápido). Súbelo si quieres dar más tiempo para marcar el nuevo código.
PAUSA_ENTRE_TOKENS = float(os.getenv("PAUSA_ENTRE_TOKENS", "4"))
# Cada cuántos segundos se repite "estamos validando sus datos" mientras se
# espera la validación (0 = no repetir).
INTERVALO_ESPERA = int(os.getenv("INTERVALO_ESPERA", "10"))
# Detección de buzón de voz (AMD). Con esto ACTIVADO, si Twilio identifica una
# MÁQUINA (buzón o fax) la llamada se cuelga de inmediato: NUNCA llega al buzón
# (ni deja mensaje). Solo se cuelga con veredictos SEGUROS: 'machine_start',
# 'machine_end_beep', 'machine_end_silence', 'machine_end_other' y 'fax'.
# OJO: 'unknown' (la AMD se rindió sin decidir) NO se cuelga: es también el caso
# típico de un HUMANO que tarda unos segundos en decir "aló". Si el silencio era
# de un buzón, la red de seguridad (MAX_INTENTOS_ENTRADA) lo cuelga después.
# Valores: "Enable" (recomendado, detecta en cuanto suena el "hola") o
# "DetectMessageEnd" (espera a que termine el saludo del buzón, más lento).
# Va activado por defecto: la cuenta ya es de pago/verificada, así que Twilio lo
# permite. Déjalo VACÍO solo si quieres desactivar la detección de buzón.
MAQUINA_DETECCION = os.getenv("MAQUINA_DETECCION", "Enable").strip()
# Cuántos SEGUNDOS insiste Twilio en la detección de buzón antes de rendirse.
# Twilio usa 30 por defecto; con 3 basta y así el análisis no se alarga. NO
# retrasa el saludo (la AMD va asíncrona): solo marca cuánto se insiste.
# Si Twilio se rinde antes de decidir, devuelve 'unknown': Nova NO cuelga por
# eso (podría ser un humano que tardó en hablar), solo lo anota y sigue; si de
# verdad era un buzón, la red de seguridad lo cuelga sin dejar mensaje. Aun así,
# subirlo un poco (p. ej. 5-8) reduce los veredictos 'unknown' y acelera el
# corte de los buzones reales.
MAQUINA_DETECCION_TIMEOUT = int(os.getenv("MAQUINA_DETECCION_TIMEOUT", "3"))
# 1 = la detección de buzón corre EN SEGUNDO PLANO (recomendado): Twilio NO
# retiene la llamada esperando el análisis, así el saludo "Soy Nova" suena de
# inmediato y el resultado (humano/máquina) llega aparte a /voice/amd, que cuelga
# si Twilio identifica una máquina ('machine*' o 'fax'). Un 'unknown' no cuelga
# (ver arriba). 0 = AMD SINCRONA: el saludo NO suena hasta que Twilio termina el
# análisis (quien contesta oye varios segundos de silencio), pero el veredicto
# llega en la MISMA petición del TwiML ('/voice/start'), así que no depende de
# que llegue un segundo aviso.
MAQUINA_DETECCION_ASINCRONA = os.getenv(
    "MAQUINA_DETECCION_ASINCRONA", "1"
).strip().lower() not in ("0", "false", "no", "")
# Cada cuántos segundos hace un "tic" el reloj de grupo.
INTERVALO_RELOJ = int(os.getenv("INTERVALO_RELOJ", "5"))
# Cuántos paneles refresca el reloj en CADA tic (por TURNOS, los más viejos
# primero). 0 = AUTOMÁTICO (recomendado): el reloj gasta el presupuesto de
# Telegram (ver TELEGRAM_MSGS_POR_MINUTO) y refresca tantos paneles como quepan,
# así que NUNCA se pasa del límite (no hay 429 que congele un panel) y con 10+
# llamadas a la vez TODAS se van refrescando por turnos sin quedarse ninguna
# clavada. Un valor fijo (1, 2, 3...) fuerza ese número por tic y desactiva el
# presupuesto: úsalo solo con POCAS llamadas o verás avisos de "límite
# alcanzado". Con N llamadas, cada panel se actualiza aproximadamente cada
# (N / por_tanda) x INTERVALO_RELOJ segundos (en modo automático lo marca el
# presupuesto: con 10 llamadas y los valores por defecto, cada ~30 s).
RELOJ_LLAMADAS_POR_TANDA = int(os.getenv("RELOJ_LLAMADAS_POR_TANDA", "0"))
# Presupuesto de mensajes a Telegram por minuto en TODO el grupo (envíos +
# ediciones). Telegram corta con error 429 al pasar de ~20 mensajes/min en un
# grupo, y ese 429 es lo que congelaba los paneles. El reloj de grupo reparte
# este presupuesto con un "cubo de fichas": los refrescos del reloj NUNCA pasan de
# este número, así que soporta 10+ llamadas simultáneas sin que se congele ningún
# panel. Súbelo (con cuidado) acercándote a 20 para refrescos más ágiles; ponlo a 0
# para desactivar el presupuesto (NO recomendado: vuelve el riesgo de 429).
TELEGRAM_MSGS_POR_MINUTO = int(os.getenv("TELEGRAM_MSGS_POR_MINUTO", "18"))

# --- Cadena de llamadas (/ciclos) --------------------------------------------
# Cuántas llamadas mantiene EN SIMULTÁNEO la cadena (por defecto 5). En cuanto una
# cuelga, la cadena marca la SIGUIENTE de la lista: nunca deja de llamar.
CICLOS_GRUPO = int(os.getenv("CICLOS_GRUPO", "5"))
# Segundos de pausa entre marcar una llamada y la siguiente (evita ráfagas).
CICLOS_PAUSA = float(os.getenv("CICLOS_PAUSA", "2"))
# 1 = al llegar al final de la lista vuelve a empezar (nunca deja de llamar).
# 0 = se detiene tras marcar TODOS los números una vez.
CICLOS_REPETIR = os.getenv("CICLOS_REPETIR", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "",
)
# Cada cuántos segundos se ACTUALIZA el mensaje EN VIVO de la cadena (el que
# muestra por dónde va y cuántas lleva). Ese mensaje se manda UNA vez y luego se
# EDITA (no llena el chat).
CICLOS_PROGRESO_INTERVALO = float(os.getenv("CICLOS_PROGRESO_INTERVALO", "8"))

# --- Vigilante de llamadas abandonadas ---------------------------------------
# Una llamada se apoya en el aviso 'completed' de Twilio para cerrarse. Si ese
# aviso se pierde (red, ngrok), la ficha se quedaría viva para siempre: el panel
# seguiría diciendo 'en llamada' con el cronómetro congelado. El vigilante cierra
# las llamadas que llevan más de TIEMPO_SIN_ACTIVIDAD segundos SIN dar señales de
# vida (ni números marcados, ni validaciones tuyas, ni el sondeo de la voz de
# fase1/fase2, que Twilio repite cada pocos segundos mientras la llamada vive).
TIEMPO_SIN_ACTIVIDAD = int(os.getenv("TIEMPO_SIN_ACTIVIDAD", "240"))
# Cada cuántos segundos el vigilante revisa TODAS las llamadas.
INTERVALO_VIGILANCIA = int(os.getenv("INTERVALO_VIGILANCIA", "30"))
# Tope DURO de duración de una llamada en Twilio (segundos). Cuando se cumple,
# Twilio corta la llamada solo (aunque el flujo se haya quedado atascado) y su
# aviso 'completed' cierra el panel. Ponlo a 0 para no poner tope.
LIMITE_LLAMADA = int(os.getenv("LIMITE_LLAMADA", "900"))

# --- Telegram: listas de números ---------------------------------------------
# Cuántos números se muestran por página en /numeros y en el menú de /llamar.
# Con muchos números (300+) la lista completa NO cabe en un mensaje de Telegram:
# van en grupos de 10 con botones ⬅️/➡️ y un botón 📄 que manda el .txt completo.
NUMEROS_POR_PAGINA = int(os.getenv("NUMEROS_POR_PAGINA", "10"))

PLACEHOLDERS = {
    "TWILIO_ACCOUNT_SID": TWILIO_ACCOUNT_SID,
    "TWILIO_AUTH_TOKEN": TWILIO_AUTH_TOKEN,
    "TWILIO_NUMBER": TWILIO_NUMBER,
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "TELEGRAM_GROUP_CHAT_ID": TELEGRAM_GROUP_CHAT_ID,
}


# --- Reporte del IVR ---------------------------------------------------------
# Encabezado del historial: "IVR: <origen> -> <destino>"
IVR_ORIGEN = os.getenv("IVR_ORIGEN", "Bogota")
IVR_DESTINO = os.getenv("IVR_DESTINO", "ID")


def faltantes() -> list[str]:
    """Devuelve la lista de variables que siguen con el valor de ejemplo."""
    problemas = []
    ya_reportado = set()
    for nombre, valor in PLACEHOLDERS.items():
        if not valor or "AQUI" in valor.upper() or "TU_" in valor.upper():
            problemas.append(nombre)
            ya_reportado.add(nombre)

    if "TWILIO_ACCOUNT_SID" not in ya_reportado and not TWILIO_ACCOUNT_SID.startswith("AC"):
        problemas.append("TWILIO_ACCOUNT_SID (debe empezar por AC)")
    if "TWILIO_NUMBER" not in ya_reportado and not TWILIO_NUMBER.startswith("+"):
        problemas.append("TWILIO_NUMBER (debe empezar por + y clave de pais)")
    if "TELEGRAM_BOT_TOKEN" not in ya_reportado and ":" not in TELEGRAM_BOT_TOKEN:
        problemas.append("TELEGRAM_BOT_TOKEN (formato 123456:AAE...)")
    return problemas


def chats_autorizados() -> set[str]:
    """Chats (grupos) donde Nova PUEDE estar: el del .env + los extra."""
    autorizados = set()
    principal = (TELEGRAM_GROUP_CHAT_ID or "").strip()
    if principal:
        autorizados.add(principal)
    autorizados.update(TELEGRAM_CHATS_AUTORIZADOS)
    return autorizados


def chat_autorizado(chat_id) -> bool:
    """True si el grupo está en la lista blanca (el del .env o uno extra)."""
    return str(chat_id).strip() in chats_autorizados()


# --- Webhook de Telegram -----------------------------------------------------
# Ruta publica a la que Telegram manda los mensajes, los botones y los avisos
# de alta/baja en un grupo. Vive aqui (no en servidor.py ni en
# registrar_webhook.py) para que el registro MANUAL y el AUTOMATICO del
# arranque hablen exactamente igual: si uno pidiera menos tipos de aviso que el
# otro, unos comandos funcionarian y otros no segun quien registro el webhook.
RUTA_WEBHOOK = "/telegram/webhook"

# 'message' = comandos y texto; 'callback_query' = pulsaciones de boton;
# 'my_chat_member' = para saber cuando agregan a Nova a un grupo y salirse de
# los NO autorizados.
ALLOWED_UPDATES = ("message", "callback_query", "my_chat_member")

# Cada cuantos segundos Nova REVISA que el webhook siga sano y lo repara sola si
# Telegram empezo a fallar MIENTRAS el servidor corre (p. ej. el tunel parpadeo o
# Telegram guardo un 404). Sin esta revision, un fallo POSTERIOR al arranque
# dejaria el grupo mudo hasta reiniciar a mano. 0 = solo se revisa al arrancar.
INTERVALO_WEBHOOK = int(os.getenv("INTERVALO_WEBHOOK", "60"))


def numeros_a_llamar() -> list[str]:
    """Lee la lista de telefonos desde numeros.txt (uno por linea, # para comentarios)."""
    if not ARCHIVO_NUMEROS.exists():
        return []
    numeros = []
    for linea in ARCHIVO_NUMEROS.read_text(encoding="utf-8").splitlines():
        numero = linea.strip()
        if not numero or numero.startswith("#"):
            continue
        if not numero.startswith("+"):
            raise ValueError(
                f"'{numero}' en numeros.txt debe empezar con + y clave de pais, "
                "por ejemplo +573001234567"
            )
        numeros.append(numero)
    return numeros


_ENCABEZADO_NUMEROS = (
    "# Lista de teléfonos a los que Nova marca.\n"
    "# Un número por línea, con + y clave de país. Ejemplo: +573001234567\n"
    "# Se puede editar a mano o desde Telegram con /agregar y /quitar.\n"
)


def normalizar_numero(texto: str) -> str:
    """Deja un telefono en formato +573001234567.

    Acepta lo que el operador escriba en Telegram: '573001234567',
    '+57 300 123 4567' o '+57-300-123-4567'. Lanza ValueError si no parece
    un telefono valido.
    """
    limpio = "".join(c for c in texto.strip() if c not in " -().")
    if limpio and not limpio.startswith("+"):
        limpio = "+" + limpio
    if not re.fullmatch(r"\+\d{8,15}", limpio):
        raise ValueError(
            f"'{texto}' no parece un teléfono. Usa + y clave de país, "
            "por ejemplo +573001234567"
        )
    return limpio


def guardar_numeros(numeros: list[str]) -> None:
    """Reescribe numeros.txt (uno por linea) con la lista recibida."""
    lineas = [_ENCABEZADO_NUMEROS.rstrip("\n"), ""]
    lineas.extend(numeros)
    ARCHIVO_NUMEROS.write_text("\n".join(lineas) + "\n", encoding="utf-8")
