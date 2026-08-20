#!/usr/bin/env python3
"""Bot de Telegram para recordar las fechas importantes de la familia."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)


# --- Rutas, zona horaria y registro -----------------------------------------

PROJECT_DIR = Path(__file__).resolve().parent
FECHAS_PATH = PROJECT_DIR / "fechas.yaml"
STATE_PATH = PROJECT_DIR / "state.json"
MADRID = ZoneInfo("Europe/Madrid")
YAML_HEADER = (
    "# Fechas familiares. Usa dia y mes para las recurrentes, los santos y las bodas, "
    "y fecha AAAA-MM-DD para las puntuales.\n"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("familia")
# httpx puede incluir la URL completa de Telegram en sus mensajes informativos.
logging.getLogger("httpx").setLevel(logging.WARNING)

FECHAS_LOCK = asyncio.Lock()
STATE_LOCK = asyncio.Lock()


class _FormateadorSinToken(logging.Formatter):
    """Última barrera para que una excepción de una librería no revele el token."""

    def __init__(self, token: str) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")
        self._token = token

    def format(self, record: logging.LogRecord) -> str:
        return super().format(record).replace(self._token, "[TOKEN OCULTO]")


def proteger_token_en_logs(token: str) -> None:
    for handler in logging.getLogger().handlers:
        handler.setFormatter(_FormateadorSinToken(token))


# --- Errores y validación de datos -----------------------------------------

class FechasError(ValueError):
    """Error legible relacionado con fechas.yaml."""


class FechaDuplicadaError(FechasError):
    """La fecha que se intenta añadir ya existe."""


def parsear_dia_mes(texto: str) -> tuple[int, int]:
    """Convierte DD/MM en (día, mes), permitiendo el 29 de febrero."""
    try:
        partes = texto.strip().split("/")
        if len(partes) != 2:
            raise ValueError
        dia, mes = (int(parte) for parte in partes)
        # 2000 es bisiesto, por lo que valida también 29/02.
        date(2000, mes, dia)
    except (TypeError, ValueError):
        raise FechasError("La fecha no es válida. Usa el formato DD/MM, por ejemplo 12/03.") from None
    return dia, mes


def parsear_fecha(texto: str) -> date:
    """Convierte DD/MM/AAAA en una fecha real."""
    try:
        partes = texto.strip().split("/")
        if len(partes) != 3:
            raise ValueError
        dia, mes, anno = (int(parte) for parte in partes)
        return date(anno, mes, dia)
    except (TypeError, ValueError):
        raise FechasError(
            "La fecha no es válida. Usa el formato DD/MM/AAAA, por ejemplo 24/12/2026."
        ) from None


def _fecha_yaml(valor: Any) -> date:
    """Normaliza una fecha ISO procedente de YAML."""
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, str):
        try:
            return date.fromisoformat(valor)
        except ValueError:
            pass
    raise FechasError("Una fecha puntual de fechas.yaml no tiene el formato AAAA-MM-DD.")


def _datos_vacios() -> dict[str, list[dict[str, Any]]]:
    return {"recurrentes": [], "santos": [], "bodas": [], "puntuales": []}


def validar_datos(datos: Any) -> dict[str, list[dict[str, Any]]]:
    """Valida y normaliza todo el contenido del fichero YAML."""
    if datos is None:
        return _datos_vacios()
    if not isinstance(datos, dict):
        raise FechasError(
            "fechas.yaml debe contener las secciones recurrentes, santos, bodas y puntuales."
        )

    resultado = _datos_vacios()
    secciones_validas = set(resultado.keys())
    desconocidas = set(datos.keys()) - secciones_validas
    if desconocidas:
        raise FechasError(
            f"Secciones no reconocidas en fechas.yaml: {', '.join(sorted(desconocidas))}."
        )
    for seccion in resultado:
        entradas = datos.get(seccion, [])
        if entradas is None:
            entradas = []
        if not isinstance(entradas, list):
            raise FechasError(f"La sección {seccion} de fechas.yaml debe ser una lista.")

        for entrada in entradas:
            if not isinstance(entrada, dict) or not str(entrada.get("nombre", "")).strip():
                raise FechasError(f"Hay una entrada no válida en la sección {seccion}.")
            nombre = str(entrada["nombre"]).strip()
            if seccion in {"recurrentes", "santos", "bodas"}:
                try:
                    dia = int(entrada["dia"])
                    mes = int(entrada["mes"])
                    date(2000, mes, dia)
                except (KeyError, TypeError, ValueError):
                    if seccion == "recurrentes":
                        mensaje = f"La fecha recurrente «{nombre}» no es válida."
                    elif seccion == "santos":
                        mensaje = f"El santo «{nombre}» no es válido."
                    else:
                        mensaje = f"El aniversario de boda «{nombre}» no es válido."
                    raise FechasError(mensaje) from None
                resultado[seccion].append({"nombre": nombre, "dia": dia, "mes": mes})
            else:
                resultado[seccion].append({"nombre": nombre, "fecha": _fecha_yaml(entrada.get("fecha"))})
    return resultado


# --- Lectura y escritura atómicas ------------------------------------------

def cargar_fechas(ruta: Path = FECHAS_PATH) -> dict[str, list[dict[str, Any]]]:
    """Lee fechas desde disco. No mantiene ninguna caché."""
    if not ruta.exists():
        return _datos_vacios()
    try:
        with ruta.open("r", encoding="utf-8") as fichero:
            return validar_datos(yaml.safe_load(fichero))
    except yaml.YAMLError as exc:
        raise FechasError(f"No se puede leer fechas.yaml: {exc}") from exc


def guardar_fechas_atomico(
    datos: dict[str, list[dict[str, Any]]], ruta: Path = FECHAS_PATH
) -> None:
    """Guarda YAML mediante un temporal en el mismo directorio y os.replace."""
    normalizados = validar_datos(datos)
    serializables = {
        "recurrentes": normalizados["recurrentes"],
        "santos": normalizados["santos"],
        "bodas": normalizados["bodas"],
        "puntuales": [
            {"nombre": item["nombre"], "fecha": item["fecha"].isoformat()}
            for item in normalizados["puntuales"]
        ],
    }
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=ruta.parent, prefix=f".{ruta.name}.", delete=False
        ) as fichero:
            temporal = fichero.name
            fichero.write(YAML_HEADER)
            yaml.safe_dump(serializables, fichero, allow_unicode=True, sort_keys=False)
            fichero.flush()
            os.fsync(fichero.fileno())
        os.replace(temporal, ruta)
    finally:
        if temporal and os.path.exists(temporal):
            os.unlink(temporal)


def cargar_estado(ruta: Path = STATE_PATH) -> dict[str, Any]:
    if not ruta.exists():
        return {}
    try:
        with ruta.open("r", encoding="utf-8") as fichero:
            estado = json.load(fichero)
        return estado if isinstance(estado, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("No se pudo leer state.json: %s", exc)
        return {}


def guardar_estado_atomico(estado: dict[str, Any], ruta: Path = STATE_PATH) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=ruta.parent, prefix=f".{ruta.name}.", delete=False
        ) as fichero:
            temporal = fichero.name
            json.dump(estado, fichero, ensure_ascii=False, indent=2)
            fichero.write("\n")
            fichero.flush()
            os.fsync(fichero.fileno())
        os.replace(temporal, ruta)
    finally:
        if temporal and os.path.exists(temporal):
            os.unlink(temporal)


# --- Operaciones sobre fechas ----------------------------------------------

def agregar_recurrente(
    datos: dict[str, list[dict[str, Any]]], nombre: str, dia: int, mes: int
) -> None:
    date(2000, mes, dia)  # valida y admite 29/02
    nombre = nombre.strip()
    if any(
        item["nombre"].casefold() == nombre.casefold()
        and item["dia"] == dia
        and item["mes"] == mes
        for item in datos["recurrentes"]
    ):
        raise FechaDuplicadaError("Esa fecha recurrente ya está guardada.")
    datos["recurrentes"].append({"nombre": nombre, "dia": dia, "mes": mes})


def agregar_puntual(
    datos: dict[str, list[dict[str, Any]]], nombre: str, fecha: date
) -> None:
    nombre = nombre.strip()
    if any(
        item["nombre"].casefold() == nombre.casefold() and item["fecha"] == fecha
        for item in datos["puntuales"]
    ):
        raise FechaDuplicadaError("Esa fecha puntual ya está guardada.")
    datos["puntuales"].append({"nombre": nombre, "fecha": fecha})


def agregar_santo(
    datos: dict[str, list[dict[str, Any]]], nombre: str, dia: int, mes: int
) -> None:
    date(2000, mes, dia)  # valida y admite 29/02
    nombre = nombre.strip()
    if any(
        item["nombre"].casefold() == nombre.casefold()
        and item["dia"] == dia
        and item["mes"] == mes
        for item in datos["santos"]
    ):
        raise FechaDuplicadaError("Ese santo ya está guardado.")
    datos["santos"].append({"nombre": nombre, "dia": dia, "mes": mes})


def agregar_boda(
    datos: dict[str, list[dict[str, Any]]], nombre: str, dia: int, mes: int
) -> None:
    date(2000, mes, dia)  # valida y admite 29/02
    nombre = nombre.strip()
    if any(
        item["nombre"].casefold() == nombre.casefold()
        and item["dia"] == dia
        and item["mes"] == mes
        for item in datos["bodas"]
    ):
        raise FechaDuplicadaError("Ese aniversario de boda ya está guardado.")
    datos["bodas"].append({"nombre": nombre, "dia": dia, "mes": mes})


def proxima_recurrente(dia: int, mes: int, hoy: date) -> date:
    """Calcula la siguiente aparición, incluso para 29/02."""
    anno = hoy.year
    while True:
        try:
            candidata = date(anno, mes, dia)
        except ValueError:
            anno += 1
            continue
        if candidata >= hoy:
            return candidata
        anno += 1


def entradas_ordenadas(
    datos: dict[str, list[dict[str, Any]]], hoy: date
) -> list[dict[str, Any]]:
    entradas: list[dict[str, Any]] = []
    for indice, item in enumerate(datos["recurrentes"]):
        entradas.append(
            {
                "tipo": "recurrente",
                "indice": indice,
                "nombre": item["nombre"],
                "fecha": proxima_recurrente(item["dia"], item["mes"], hoy),
                "dia": item["dia"],
                "mes": item["mes"],
            }
        )
    for indice, item in enumerate(datos["santos"]):
        entradas.append(
            {
                "tipo": "santo",
                "indice": indice,
                "nombre": item["nombre"],
                "fecha": proxima_recurrente(item["dia"], item["mes"], hoy),
                "dia": item["dia"],
                "mes": item["mes"],
            }
        )
    for indice, item in enumerate(datos["bodas"]):
        entradas.append(
            {
                "tipo": "boda",
                "indice": indice,
                "nombre": item["nombre"],
                "fecha": proxima_recurrente(item["dia"], item["mes"], hoy),
                "dia": item["dia"],
                "mes": item["mes"],
            }
        )
    for indice, item in enumerate(datos["puntuales"]):
        entradas.append(
            {
                "tipo": "puntual",
                "indice": indice,
                "nombre": item["nombre"],
                "fecha": item["fecha"],
            }
        )
    # Orden por calendario: primero mes, luego día. Las anuales usan su
    # día/mes fijo; las puntuales, el mes y día de su fecha.
    def _clave_calendario(item: dict[str, Any]) -> tuple[int, int, str]:
        mes = item.get("mes", item["fecha"].month)
        dia = item.get("dia", item["fecha"].day)
        return (mes, dia, item["nombre"].casefold())

    return sorted(entradas, key=_clave_calendario)


def _es_dia_de_celebracion(dia: int, mes: int, hoy: date) -> bool:
    """¿Coincide el día/mes con hoy? El 29/02 se celebra el 28/02 en años no bisiestos."""
    if dia == hoy.day and mes == hoy.month:
        return True
    import calendar

    return (
        mes == 2
        and dia == 29
        and hoy.month == 2
        and hoy.day == 28
        and not calendar.isleap(hoy.year)
    )


def fechas_de_hoy(
    datos: dict[str, list[dict[str, Any]]], hoy: date
) -> list[dict[str, Any]]:
    coincidentes: list[dict[str, Any]] = []
    coincidentes.extend(
        {"tipo": "recurrente", **item}
        for item in datos["recurrentes"]
        if _es_dia_de_celebracion(item["dia"], item["mes"], hoy)
    )
    coincidentes.extend(
        {"tipo": "santo", **item}
        for item in datos["santos"]
        if _es_dia_de_celebracion(item["dia"], item["mes"], hoy)
    )
    coincidentes.extend(
        {"tipo": "boda", **item}
        for item in datos["bodas"]
        if _es_dia_de_celebracion(item["dia"], item["mes"], hoy)
    )
    coincidentes.extend(
        {"tipo": "puntual", **item}
        for item in datos["puntuales"]
        if item["fecha"] == hoy
    )
    return coincidentes


def texto_lista(datos: dict[str, list[dict[str, Any]]], hoy: date) -> str:
    entradas = entradas_ordenadas(datos, hoy)
    if not entradas:
        return "No hay fechas guardadas todavía."
    lineas = ["Fechas guardadas (por fecha):"]
    for identificador, item in enumerate(entradas, start=1):
        if item["tipo"] == "puntual":
            fecha = item["fecha"].strftime("%d/%m/%Y")
            sufijo = ""
        else:
            fecha = f"{item['dia']:02d}/{item['mes']:02d}"
            sufijo = " · todos los años"
            if item["tipo"] == "santo":
                sufijo += " (santo)"
            elif item["tipo"] == "boda":
                sufijo += " (boda)"
        lineas.append(f"[{identificador}] {fecha} · {item['nombre']}{sufijo}")
    return "\n".join(lineas)


def _es_cumpleanos(nombre: str) -> bool:
    return nombre.casefold().startswith("cumpleaños de ")


def _persona_de(item: dict[str, Any]) -> str:
    """Nombre de la persona del evento (sin el prefijo 'Cumpleaños de')."""
    nombre = item["nombre"]
    if _es_cumpleanos(nombre):
        return nombre[len("Cumpleaños de ") :]
    return nombre


def entradas_por_persona(datos: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Entradas agrupadas por persona (orden alfabético) y, dentro de cada una, por fecha."""
    entradas = entradas_ordenadas(datos, date.min)
    return sorted(
        entradas,
        key=lambda item: (_persona_de(item).casefold(), item["fecha"], item["nombre"].casefold()),
    )


def texto_lista_personas(datos: dict[str, list[dict[str, Any]]]) -> str:
    entradas = entradas_por_persona(datos)
    if not entradas:
        return "No hay fechas guardadas todavía."
    lineas = ["Fechas por persona:"]
    persona_actual = None
    for item in entradas:
        persona = _persona_de(item)
        if persona != persona_actual:
            lineas.append(f"- {persona}:")
            persona_actual = persona
        if item["tipo"] == "puntual":
            fecha = item["fecha"].strftime("%d/%m/%Y")
            etiqueta = "Evento"
        elif item["tipo"] == "recurrente":
            fecha = f"{item['dia']:02d}/{item['mes']:02d}"
            etiqueta = "Cumpleaños" if _es_cumpleanos(item["nombre"]) else "Aniversario"
        elif item["tipo"] == "santo":
            fecha = f"{item['dia']:02d}/{item['mes']:02d}"
            etiqueta = "Santo"
        else:  # boda
            fecha = f"{item['dia']:02d}/{item['mes']:02d}"
            etiqueta = "Boda"
        lineas.append(f"   - {etiqueta} {fecha}")
    lineas.append("")
    lineas.append("Para borrar una fecha, usa /lista y /borrar <id>.")
    return "\n".join(lineas)


# --- Mensajes de felicitación con rotación ----------------------------------

MENSAJES_CUMPLE = [
    "🎂 ¡Hoy es el cumpleaños de {nombre}! Que pases un día genial y que cumplas muchos más.",
    "¡Feliz cumpleaños, {nombre}! 🎉 Que hoy sea un día tan especial como tú.",
    "🎂 ¡Hoy cumplimos años contigo, {nombre}! Un abrazo enorme y que lo celebres por todo lo alto.",
    "¡Hoy es el día de {nombre}! 🎈 Feliz cumpleaños y que el año que empieza venga cargado de cosas buenas.",
    "🎉 ¡Feliz cumpleaños, {nombre}! Desde aquí te mandamos un abrazo gigante.",
    "¡Que viva {nombre}! 🎂 Hoy es su cumpleaños, no se os olvide felicitarlo.",
    "🎈 ¡Hoy es el cumpleaños de {nombre}! Felicidades y que lo pases rodeado de los tuyos.",
    "¡Feliz cumpleaños, {nombre}! 🥳 Que cumplas muchos más y que cada año te encuentre igual de bien.",
    "🎂 Hoy es un gran día: el cumpleaños de {nombre}. ¡Felicidades!",
    "¡Por {nombre}, que hoy cumple años! 🎉 Felicidades y que tengas un día redondo.",
    "🎂 ¡Hoy es el cumpleaños de {nombre}! Un abrazo muy fuerte y felicidades de parte de toda la familia.",
    "¡Feliz cumpleaños, {nombre}! 🎈 Que la pases en grande y que sople muchas velas.",
]

MENSAJES_SANTO = [
    "⛪ ¡Hoy felicitamos a {nombre}, que es su santo! Que tengas un precioso día.",
    "¡Hoy es el santo de {nombre}! ⛪ Felicidades y que pases un día muy bonito.",
    "⛪ ¡Por {nombre}, que hoy celebra su santo! Un abrazo enorme y felicidades.",
    "¡Hoy toca felicitar a {nombre}, que es su santo! Que tengas un día estupendo.",
    "⛪ ¡Felicidades a {nombre} por su santo! Que la familia lo celebre como se merece.",
    "¡Hoy es el santo de {nombre}! Que tengas un precioso día, te lo mereces.",
    "⛪ ¡Feliz santo, {nombre}! Un abrazo de toda la familia y que lo disfrutes.",
    "¡Hoy celebramos el santo de {nombre}! ⛪ Felicidades y que el día te sonría.",
    "⛪ ¡Por {nombre} y su santo! Felicidades y que tengas un día de esos que no se olvidan.",
    "¡Hoy es el santo de {nombre}! Que tengas un precioso día lleno de cariño.",
]

MENSAJES_ANIVERSARIO = [
    "📅 ¡Hoy es el aniversario de {nombre}! Felicidades y que lo celebréis por todo lo alto.",
    "¡Hoy se celebra {nombre}! 📅 Un año más para recordar. ¡Felicidades!",
    "📅 ¡Hoy es {nombre}! Enhorabuena y que lo paséis en grande.",
    "¡Hoy toca celebrar {nombre}! Felicidades a los protagonistas.",
    "📅 ¡Feliz {nombre}! Que el día esté a la altura de lo que se celebra.",
    "¡Hoy es {nombre}! Un motivo más para juntarse y celebrarlo en familia.",
]

MENSAJES_BODA = [
    "💍 ¡Feliz aniversario de boda, {nombre}! Que sigáis cumpliendo muchos más juntos.",
    "¡Hoy es el aniversario de boda de {nombre}! 💍 Felicidades y que el amor siga creciendo.",
    "💍 ¡Por {nombre} y su aniversario de boda! Que celebréis el día como se merece.",
    "¡Feliz aniversario, {nombre}! 💍 Un día para recordar el día en que lo empezasteis todo.",
    "💍 ¡Hoy celebramos el aniversario de boda de {nombre}! Enhorabuena y mucho amor.",
    "¡Felicidades en vuestro aniversario, {nombre}! 💍 Que la historia siga escribiéndose.",
    "💍 ¡Hoy es un día grande: aniversario de boda de {nombre}! Felicidades a los dos.",
    "¡Que viva el amor de {nombre}! 💍 Feliz aniversario de boda y que sean muchos más.",
    "💍 ¡Feliz aniversario de boda, {nombre}! Un brindis por vosotros y por lo que viene.",
    "¡Hoy toca brindar por {nombre}! 💍 Feliz aniversario y que el amor dure siempre.",
]

_POOLS_MENSAJES = {
    "cumple": MENSAJES_CUMPLE,
    "santo": MENSAJES_SANTO,
    "aniversario": MENSAJES_ANIVERSARIO,
    "boda": MENSAJES_BODA,
}


def _tipo_ocasion(item: dict[str, Any]) -> str:
    if item["tipo"] == "recurrente":
        return "cumple" if _es_cumpleanos(item["nombre"]) else "aniversario"
    if item["tipo"] == "santo":
        return "santo"
    if item["tipo"] == "boda":
        return "boda"
    return "puntual"


def elegir_mensaje(tipo: str, nombre: str, estado: dict[str, Any]) -> str:
    pool = _POOLS_MENSAJES[tipo]
    usados_por_tipo = estado.setdefault("recordatorios_usados", {})
    usados = usados_por_tipo.get(tipo, [])
    if len(usados) >= len(pool):
        usados = []
    candidatos = [i for i in range(len(pool)) if i not in usados]
    indice = candidatos[0]
    usados_por_tipo[tipo] = usados + [indice]
    return pool[indice].format(nombre=nombre)


def texto_recordatorio(coincidentes: list[dict[str, Any]], estado: dict[str, Any]) -> str:
    if len(coincidentes) == 1:
        item = coincidentes[0]
        tipo = _tipo_ocasion(item)
        if tipo == "puntual":
            return f"📅 Hoy: {item['nombre']}"
        if tipo == "cumple":
            persona = item["nombre"][len("Cumpleaños de ") :]
            return elegir_mensaje(tipo, persona, estado)
        return elegir_mensaje(tipo, item["nombre"], estado)

    # Varias ocasiones: cada persona recibe su felicitación individual
    # (rotando por su banco); los eventos puntuales mantienen la línea simple.
    lineas = []
    for item in coincidentes:
        tipo = _tipo_ocasion(item)
        if tipo == "puntual":
            lineas.append(f"📅 Hoy: {item['nombre']}")
        else:
            lineas.append(elegir_mensaje(tipo, _persona_de(item), estado))
    return "\n".join(lineas)


# --- Configuración y permisos ----------------------------------------------

def _entero_env(nombre: str, obligatorio: bool = False) -> int | None:
    valor = os.getenv(nombre, "").strip()
    if not valor:
        if obligatorio:
            raise RuntimeError(f"Falta {nombre} en el archivo .env.")
        return None
    try:
        return int(valor)
    except ValueError:
        raise RuntimeError(f"{nombre} debe ser un número entero.") from None


def obtener_chat_familiar() -> int | None:
    configurado = _entero_env("FAMILY_CHAT_ID")
    if configurado is not None:
        return configurado
    guardado = cargar_estado().get("family_chat_id")
    try:
        return int(guardado) if guardado is not None else None
    except (TypeError, ValueError):
        LOGGER.warning("family_chat_id no válido en state.json")
        return None


def autorizado_para_escribir(update: Update) -> bool:
    chat = update.effective_chat
    if chat is None:
        return False
    if chat.type == "channel":
        return True
    propietario = _entero_env("OWNER_CHAT_ID")
    usuario = update.effective_user
    return propietario is not None and usuario is not None and usuario.id == propietario


async def exigir_autorizacion(update: Update) -> bool:
    if autorizado_para_escribir(update):
        return True
    if update.effective_message:
        await update.effective_message.reply_text("No autorizado. Solo el administrador puede modificar las fechas.")
    return False


def _ahora_madrid() -> date:
    return datetime.now(MADRID).date()


# --- Handlers de Telegram ---------------------------------------------------

async def comando_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    await update.effective_message.reply_text(
        "¡Hola! Soy Familia. Guardo cumpleaños, santos, aniversarios y eventos puntuales, "
        "y aviso al canal familiar el día correspondiente. Usa /ayuda para ver los comandos."
    )


async def comando_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    await update.effective_message.reply_text(
        "Comandos disponibles:\n"
        "/start — Presentación del bot\n"
        "/ayuda — Mostrar esta ayuda\n"
        "/cumple <Nombre> <DD/MM> — Ejemplo: /cumple Lucas 12/03\n"
        "/santo <Nombre> <DD/MM> — Ejemplo: /santo Lucas 24/10\n"
        "/boda <Nombre> <DD/MM> — Ejemplo: /boda Lucía y Mario 15/06\n"
        "/fecha <Nombre> <DD/MM/AAAA> — Ejemplo: /fecha Cena Nochebuena 24/12/2026\n"
        "/lista — Mostrar todas las fechas (por fecha)\n"
        "/listap — Mostrar todas las fechas (por persona)\n"
        "/borrar <id> — Ejemplo: /borrar 2\n"
        "/chatid — Mostrar el id y el tipo del chat actual\n"
        "/recargar — Comprobar de nuevo fechas.yaml\n"
        "/avisar — Enviar ahora el aviso del día al canal"
    )


async def comando_cumple(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await exigir_autorizacion(update):
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text("Uso: /cumple <Nombre> <DD/MM>. Ejemplo: /cumple Lucas 12/03")
        return
    nombre_persona = " ".join(context.args[:-1]).strip()
    if not nombre_persona:
        await update.effective_message.reply_text("Indica el nombre de la persona.")
        return
    try:
        dia, mes = parsear_dia_mes(context.args[-1])
        nombre = f"Cumpleaños de {nombre_persona}"
        async with FECHAS_LOCK:
            datos = cargar_fechas()
            agregar_recurrente(datos, nombre, dia, mes)
            guardar_fechas_atomico(datos)
    except FechasError as exc:
        await update.effective_message.reply_text(f"No se ha podido añadir: {exc}")
        return
    await update.effective_message.reply_text(
        f"✅ Añadida: {nombre} ({dia:02d}/{mes:02d}, todos los años)"
    )


async def comando_fecha(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await exigir_autorizacion(update):
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "Uso: /fecha <Nombre> <DD/MM/AAAA>. Ejemplo: /fecha Cena Nochebuena 24/12/2026"
        )
        return
    nombre = " ".join(context.args[:-1]).strip()
    if not nombre:
        await update.effective_message.reply_text("Indica un nombre para el evento.")
        return
    try:
        fecha = parsear_fecha(context.args[-1])
        async with FECHAS_LOCK:
            datos = cargar_fechas()
            agregar_puntual(datos, nombre, fecha)
            guardar_fechas_atomico(datos)
    except FechasError as exc:
        await update.effective_message.reply_text(f"No se ha podido añadir: {exc}")
        return
    await update.effective_message.reply_text(
        f"✅ Añadida: {nombre} ({fecha.strftime('%d/%m/%Y')})"
    )


async def comando_santo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await exigir_autorizacion(update):
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "Uso: /santo <Nombre> <DD/MM>. Ejemplo: /santo Lucas 24/10"
        )
        return
    nombre = " ".join(context.args[:-1]).strip()
    if not nombre:
        await update.effective_message.reply_text("Indica el nombre de la persona.")
        return
    try:
        dia, mes = parsear_dia_mes(context.args[-1])
        async with FECHAS_LOCK:
            datos = cargar_fechas()
            agregar_santo(datos, nombre, dia, mes)
            guardar_fechas_atomico(datos)
    except FechasError as exc:
        await update.effective_message.reply_text(f"No se ha podido añadir: {exc}")
        return
    await update.effective_message.reply_text(
        f"✅ Añadido: Santo de {nombre} ({dia:02d}/{mes:02d}, todos los años)"
    )


async def comando_boda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await exigir_autorizacion(update):
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "Uso: /boda <Nombre> <DD/MM>. Ejemplo: /boda Lucía y Mario 15/06"
        )
        return
    nombre = " ".join(context.args[:-1]).strip()
    if not nombre:
        await update.effective_message.reply_text("Indica el nombre de la pareja.")
        return
    try:
        dia, mes = parsear_dia_mes(context.args[-1])
        async with FECHAS_LOCK:
            datos = cargar_fechas()
            agregar_boda(datos, nombre, dia, mes)
            guardar_fechas_atomico(datos)
    except FechasError as exc:
        await update.effective_message.reply_text(f"No se ha podido añadir: {exc}")
        return
    await update.effective_message.reply_text(
        f"✅ Añadido: Aniversario de boda de {nombre} ({dia:02d}/{mes:02d}, todos los años)"
    )


async def enviar_largo(
    update: Update, texto: str, max_len: int = 4000
) -> None:
    """Envía un texto posiblemente largo fragmentándolo por líneas (límite 4096 de Telegram)."""
    if len(texto) <= max_len:
        await update.effective_message.reply_text(texto)
        return
    bloque: list[str] = []
    longitud = 0
    for linea in texto.split("\n"):
        if longitud + len(linea) + 1 > max_len and bloque:
            await update.effective_message.reply_text("\n".join(bloque))
            bloque, longitud = [], 0
        bloque.append(linea)
        longitud += len(linea) + 1
    if bloque:
        await update.effective_message.reply_text("\n".join(bloque))


async def comando_lista(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    try:
        datos = cargar_fechas()
        texto = texto_lista(datos, _ahora_madrid())
    except FechasError as exc:
        texto = f"No se puede leer fechas.yaml: {exc}"
    await enviar_largo(update, texto)


async def comando_listap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    try:
        datos = cargar_fechas()
        texto = texto_lista_personas(datos)
    except FechasError as exc:
        texto = f"No se puede leer fechas.yaml: {exc}"
    await enviar_largo(update, texto)


async def comando_borrar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await exigir_autorizacion(update):
        return
    try:
        identificador = int(context.args[0]) if len(context.args) == 1 else 0
    except ValueError:
        identificador = 0

    async with FECHAS_LOCK:
        try:
            datos = cargar_fechas()
            entradas = entradas_ordenadas(datos, _ahora_madrid())
            if identificador < 1 or identificador > len(entradas):
                await enviar_largo(
                    update,
                    "Ese id no es válido.\n\n" + texto_lista(datos, _ahora_madrid()),
                )
                return
            elegida = entradas[identificador - 1]
            seccion = {
                "recurrente": "recurrentes",
                "santo": "santos",
                "boda": "bodas",
                "puntual": "puntuales",
            }[elegida["tipo"]]
            datos[seccion].pop(elegida["indice"])
            guardar_fechas_atomico(datos)
        except FechasError as exc:
            await update.effective_message.reply_text(f"No se ha podido borrar: {exc}")
            return
    await update.effective_message.reply_text(f"🗑️ Borrada: {elegida['nombre']}")


async def comando_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    chat = update.effective_chat
    await update.effective_message.reply_text(f"Id del chat: {chat.id}\nTipo: {chat.type}")


async def comando_recargar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not await exigir_autorizacion(update):
        return
    try:
        datos = cargar_fechas()
    except FechasError as exc:
        await update.effective_message.reply_text(f"No se ha podido recargar: {exc}")
        return
    await update.effective_message.reply_text(
        f"🔄 Recargadas {len(datos['recurrentes'])} fechas recurrentes, "
        f"{len(datos['santos'])} santos, "
        f"{len(datos['bodas'])} bodas y "
        f"{len(datos['puntuales'])} fechas puntuales."
    )


async def comando_avisar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await exigir_autorizacion(update):
        return
    texto = await aviso_diario(context)
    if texto is None:
        await update.effective_message.reply_text("No hay fechas para hoy.")
        return
    await update.effective_message.reply_text(
        f"✅ Aviso del día enviado al canal familiar:\n\n{texto}"
    )


async def comando_desconocido(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    await update.effective_message.reply_text("No conozco ese comando. Usa /ayuda para ver las opciones.")


async def descubrir_canal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Vincula el primer canal observado si no hay un destino configurado."""
    chat = update.effective_chat
    if chat is None or chat.type != "channel" or obtener_chat_familiar() is not None:
        return
    async with STATE_LOCK:
        if obtener_chat_familiar() is not None:
            return
        estado = cargar_estado()
        estado["family_chat_id"] = chat.id
        guardar_estado_atomico(estado)
    propietario = _entero_env("OWNER_CHAT_ID")
    titulo = chat.title or "Canal sin título"
    LOGGER.info("Canal familiar vinculado automáticamente: %s (%s)", titulo, chat.id)
    if propietario is not None:
        try:
            await context.bot.send_message(
                propietario,
                f"🔗 Canal vinculado: {titulo} (id {chat.id}). Los avisos diarios irán aquí.",
            )
        except Exception:
            LOGGER.exception("No se pudo notificar al propietario sobre el canal vinculado")


# --- Aviso diario y ciclo de vida ------------------------------------------

def texto_aviso_hoy(
    hoy: date, ruta: Path = FECHAS_PATH, estado: dict[str, Any] | None = None
) -> str | None:
    """Texto del aviso para hoy, o None si no hay fechas. No persiste estado."""
    datos = cargar_fechas(ruta)
    coincidentes = fechas_de_hoy(datos, hoy)
    if not coincidentes:
        return None
    if estado is None:
        estado = cargar_estado()
    return texto_recordatorio(coincidentes, estado)


async def aviso_diario(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    hoy = _ahora_madrid()
    destino = obtener_chat_familiar()
    if destino is None:
        LOGGER.info("Aviso diario %s: sin canal familiar configurado", hoy.isoformat())
        return None
    try:
        async with STATE_LOCK:
            estado = cargar_estado()
            texto = texto_aviso_hoy(hoy, estado=estado)
            if texto is None:
                LOGGER.info("Aviso diario %s: no hay fechas; no se envía ningún mensaje", hoy.isoformat())
                return None
            coincidentes = fechas_de_hoy(cargar_fechas(), hoy)
        # Enviar primero y persistir la rotación solo si el envío ha tenido éxito.
        await context.bot.send_message(destino, texto)
        if any(_tipo_ocasion(c) != "puntual" for c in coincidentes):
            async with STATE_LOCK:
                guardar_estado_atomico(estado)
        LOGGER.info("Aviso diario %s: enviado con %d fecha(s)", hoy.isoformat(), len(coincidentes))
        return texto
    except Exception:
        LOGGER.exception("Aviso diario %s: error al procesar o enviar", hoy.isoformat())
        return None


def parsear_hora_aviso(valor: str) -> time:
    try:
        hora_texto, minuto_texto = valor.strip().split(":")
        return time(int(hora_texto), int(minuto_texto), tzinfo=MADRID)
    except (TypeError, ValueError):
        raise RuntimeError("AVISO_HORA debe tener el formato HH:MM, por ejemplo 09:00.") from None


async def al_iniciar(application: Application) -> None:
    hora = parsear_hora_aviso(os.getenv("AVISO_HORA", "09:00"))
    if application.job_queue is None:
        raise RuntimeError("Falta instalar python-telegram-bot con el extra job-queue.")
    application.job_queue.run_daily(aviso_diario, time=hora, name="aviso-diario-familia")
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Presentación"),
            BotCommand("ayuda", "Ver los comandos"),
            BotCommand("cumple", "Añadir un cumpleaños"),
            BotCommand("santo", "Añadir el santo de alguien"),
            BotCommand("boda", "Añadir un aniversario de boda"),
            BotCommand("fecha", "Añadir un evento puntual"),
            BotCommand("lista", "Ver todas las fechas (por fecha)"),
            BotCommand("listap", "Ver todas las fechas (por persona)"),
            BotCommand("borrar", "Borrar una fecha por id"),
            BotCommand("chatid", "Ver el id de este chat"),
            BotCommand("recargar", "Comprobar fechas.yaml"),
            BotCommand("avisar", "Enviar ahora el aviso del día"),
        ]
    )
    LOGGER.info("Aviso diario programado a las %s (Europe/Madrid)", hora.strftime("%H:%M"))


async def manejar_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    del update
    LOGGER.error("Error no controlado al procesar una actualización", exc_info=context.error)


def crear_aplicacion(token: str) -> Application:
    aplicacion = ApplicationBuilder().token(token).post_init(al_iniciar).build()
    # El grupo -1 observa cualquier actualización antes de los comandos.
    aplicacion.add_handler(TypeHandler(Update, descubrir_canal), group=-1)
    aplicacion.add_handler(CommandHandler("start", comando_start))
    aplicacion.add_handler(CommandHandler("ayuda", comando_ayuda))
    aplicacion.add_handler(CommandHandler("cumple", comando_cumple))
    aplicacion.add_handler(CommandHandler("santo", comando_santo))
    aplicacion.add_handler(CommandHandler("boda", comando_boda))
    aplicacion.add_handler(CommandHandler("fecha", comando_fecha))
    aplicacion.add_handler(CommandHandler("lista", comando_lista))
    aplicacion.add_handler(CommandHandler("listap", comando_listap))
    aplicacion.add_handler(CommandHandler("borrar", comando_borrar))
    aplicacion.add_handler(CommandHandler("chatid", comando_chatid))
    aplicacion.add_handler(CommandHandler("recargar", comando_recargar))
    aplicacion.add_handler(CommandHandler("avisar", comando_avisar))
    aplicacion.add_handler(MessageHandler(filters.COMMAND, comando_desconocido))
    aplicacion.add_error_handler(manejar_error)
    return aplicacion


def main() -> None:
    load_dotenv(PROJECT_DIR / ".env")
    token = os.getenv("TOKEN", "").strip()
    if not token:
        raise SystemExit("Error de configuración: falta TOKEN en el archivo .env.")
    proteger_token_en_logs(token)
    # Se valida al arrancar para fallar antes de conectar con Telegram.
    _entero_env("OWNER_CHAT_ID", obligatorio=True)
    _entero_env("FAMILY_CHAT_ID")
    parsear_hora_aviso(os.getenv("AVISO_HORA", "09:00"))
    LOGGER.info("Iniciando el bot Familia")
    crear_aplicacion(token).run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
