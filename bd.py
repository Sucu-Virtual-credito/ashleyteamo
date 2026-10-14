"""Historial de llamadas de Nova.

El motor se cambia con una sola variable de entorno, sin tocar el codigo:

    NOVA_DB_URL=sqlite:///nova.db                      (por defecto, zero install)
    NOVA_DB_URL=postgresql+psycopg://user:pass@host/nova
    NOVA_DB_URL=mysql+pymysql://user:pass@host/nova

Dos tablas:
    llamadas -> una fila por llamada, con su estado actual
    eventos  -> una fila por cada cosa que se capturo o decidio, en orden
"""

import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    insert,
    select,
    update,
)

BASE_DIR = Path(__file__).resolve().parent
NOMBRE_BD = "nova.db"

URL_BD = os.getenv(
    "NOVA_DB_URL", f"sqlite:///{(BASE_DIR / NOMBRE_BD).as_posix()}"
)

# SQLite con WAL + busy_timeout: varias llamadas a la vez escriben a la vez
# (eventos/estados) y sin esto aparecen errores "database is locked". WAL
# permite lecturas mientras se escribe; busy_timeout espera hasta 30 s al lock
# en vez de fallar.
_CONNECT_ARGS = {}
if URL_BD.startswith("sqlite"):
    _CONNECT_ARGS = {"check_same_thread": False, "timeout": 30}

engine = create_engine(URL_BD, future=True, connect_args=_CONNECT_ARGS)

if URL_BD.startswith("sqlite"):
    from sqlalchemy import event as _sa_event

    @_sa_event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _rec):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()
metadata = MetaData()

llamadas = Table(
    "llamadas",
    metadata,
    Column("call_sid", String(64), primary_key=True),
    Column("telefono", String(32)),
    Column("flujo", String(32)),
    Column("estado", String(32)),
    Column("primero", String(64)),
    Column("segundo", String(64)),
    Column("tercero", String(64)),
    Column("operador", String(64)),
    Column("empresa_origen", String(32)),
    Column("destino_flujo", String(32)),
    Column("accion_pendiente", String(64)),
    Column("creado", DateTime),
    Column("actualizado", DateTime),
)

eventos = Table(
    "eventos",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("call_sid", String(64), index=True),
    Column("tipo", String(32)),
    Column("valor", String(255)),
    Column("operador", String(64)),
    Column("creado", DateTime),
)

# Tipos de evento que forman el historial del IVR (tal como los guarda el bot).
TIPOS_DATOS = {"id", "menu", "pin", "otp", "numero"}


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def init() -> None:
    """Crea las tablas si no existen. Es idempotente."""
    metadata.create_all(engine)


def nueva_llamada(
    call_sid: str,
    telefono: str = "",
    flujo: str = "numeros",
    empresa_origen: str = "Bogota",
    destino_flujo: str = "ID",
) -> None:
    with engine.begin() as conexion:
        conexion.execute(
            insert(llamadas).values(
                call_sid=call_sid,
                telefono=telefono,
                flujo=flujo,
                empresa_origen=empresa_origen,
                destino_flujo=destino_flujo,
                estado="iniciada",
                accion_pendiente="validación",
                creado=_ahora(),
                actualizado=_ahora(),
            )
        )


def actualizar(call_sid: str, **campos) -> None:
    if not campos:
        return
    campos["actualizado"] = _ahora()
    with engine.begin() as conexion:
        conexion.execute(
            update(llamadas).where(llamadas.c.call_sid == call_sid).values(**campos)
        )


def registrar_evento(
    call_sid: str, tipo: str, valor: str = "", operador: str = ""
) -> None:
    with engine.begin() as conexion:
        conexion.execute(
            insert(eventos).values(
                call_sid=call_sid,
                tipo=tipo,
                valor=str(valor),
                operador=operador,
                creado=_ahora(),
            )
        )


def eventos_de(call_sid: str) -> list[dict]:
    with engine.connect() as conexion:
        filas = conexion.execute(
            select(eventos)
            .where(eventos.c.call_sid == call_sid)
            .order_by(eventos.c.id)
        ).mappings()
        return [dict(fila) for fila in filas]


def obtener(call_sid: str) -> dict | None:
    with engine.connect() as conexion:
        fila = conexion.execute(
            select(llamadas).where(llamadas.c.call_sid == call_sid)
        ).mappings().first()
        return dict(fila) if fila else None


def ultimas(cantidad: int = 5) -> list[dict]:
    with engine.connect() as conexion:
        filas = conexion.execute(
            select(llamadas).order_by(llamadas.c.creado.desc()).limit(cantidad)
        ).mappings()
        return [dict(fila) for fila in filas]


def _lineas_evento(evento: dict) -> str | None:
    """Convierte un evento en su linea del historial, o None si no va en el."""
    if evento["tipo"] == "decision":
        accion = evento["valor"].split()[0]
        if accion.startswith("validar") and evento["operador"]:
            return f"  ✅ Validado por {evento['operador']}"
        return None
    if evento["tipo"] not in TIPOS_DATOS:
        return None
    etiqueta, _, valor = evento["valor"].partition("=")
    return f"  • {etiqueta}: {valor or evento['valor']}"


def resumen(call_sid: str) -> str:
    """Arma el bloque con el formato del reporte del IVR."""
    llamada = obtener(call_sid)
    if llamada is None:
        return f"No hay registro para la llamada {call_sid}."

    lineas = [
        f"IVR: {llamada.get('empresa_origen') or 'Bogota'} -> "
        f"{llamada.get('destino_flujo') or 'ID'}",
        f"Teléfono: {llamada.get('telefono') or 'desconocido'}",
        "",
        "Historial:",
    ]

    # El historial va en orden cronologico, como en el reporte del IVR:
    # lo que dijo la persona, y en su sitio, quien lo aprobo.
    for evento in eventos_de(call_sid):
        linea = _lineas_evento(evento)
        if linea:
            lineas.append(linea)

    pendiente = llamada.get("accion_pendiente")
    if pendiente:
        lineas += ["", f"Acción pendiente: {pendiente}"]

    return "\n".join(lineas)


def cerrar(call_sid: str, operador: str = "") -> None:
    """Marca la llamada como atendida: ya no queda nada pendiente."""
    actualizar(
        call_sid,
        operador=operador or None,
        accion_pendiente="",
        estado="cerrada",
    )


__all__ = [
    "URL_BD",
    "actualizar",
    "cerrar",
    "eventos_de",
    "init",
    "nueva_llamada",
    "obtener",
    "registrar_evento",
    "resumen",
    "ultimas",
]
