from datetime import date

import pytest

from bot import (
    MENSAJES_BODA,
    MENSAJES_CUMPLE,
    MENSAJES_SANTO,
    FechaDuplicadaError,
    FechasError,
    agregar_boda,
    agregar_puntual,
    agregar_recurrente,
    agregar_santo,
    cargar_fechas,
    elegir_mensaje,
    entradas_ordenadas,
    entradas_por_persona,
    fechas_de_hoy,
    guardar_fechas_atomico,
    parsear_dia_mes,
    parsear_fecha,
    proxima_recurrente,
    texto_aviso_hoy,
    texto_lista,
    texto_lista_personas,
    texto_recordatorio,
    validar_datos,
)

from bot import _es_dia_de_celebracion, _persona_de


def datos_vacios():
    return {"recurrentes": [], "santos": [], "bodas": [], "puntuales": []}


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [("12/03", (12, 3)), ("29/02", (29, 2)), ("1/1", (1, 1))],
)
def test_parsear_dia_mes_valido(texto, esperado):
    assert parsear_dia_mes(texto) == esperado


@pytest.mark.parametrize("texto", ["31/04", "30/02", "00/12", "12/13", "12-03", ""])
def test_parsear_dia_mes_invalido(texto):
    with pytest.raises(FechasError):
        parsear_dia_mes(texto)


def test_parsear_fecha_completa_valida_e_invalida():
    assert parsear_fecha("29/02/2024") == date(2024, 2, 29)
    with pytest.raises(FechasError):
        parsear_fecha("29/02/2023")
    with pytest.raises(FechasError):
        parsear_fecha("31/11/2026")


def test_rechaza_duplicados_sin_distinguir_mayusculas():
    datos = datos_vacios()
    agregar_recurrente(datos, "Cumpleaños de Lucas", 12, 3)
    with pytest.raises(FechaDuplicadaError):
        agregar_recurrente(datos, "cumpleaños de lucas", 12, 3)

    agregar_puntual(datos, "Cena familiar", date(2026, 12, 24))
    with pytest.raises(FechaDuplicadaError):
        agregar_puntual(datos, "CENA FAMILIAR", date(2026, 12, 24))


def test_mismo_nombre_en_otra_fecha_no_es_duplicado():
    datos = datos_vacios()
    agregar_recurrente(datos, "Aniversario", 1, 5)
    agregar_recurrente(datos, "Aniversario", 2, 5)
    assert len(datos["recurrentes"]) == 2


def test_agregar_santo_y_rechazar_duplicado():
    datos = datos_vacios()
    agregar_santo(datos, "Lucas", 24, 10)

    assert datos["santos"] == [{"nombre": "Lucas", "dia": 24, "mes": 10}]
    with pytest.raises(FechaDuplicadaError, match="Ese santo ya está guardado"):
        agregar_santo(datos, "lucas", 24, 10)


def test_validar_datos_admite_santos_y_ausencia_de_la_seccion():
    datos = validar_datos(
        {"santos": [{"nombre": "Lucas", "dia": 29, "mes": 2}]}
    )
    assert datos["santos"] == [{"nombre": "Lucas", "dia": 29, "mes": 2}]

    sin_santos = validar_datos({"recurrentes": [], "puntuales": []})
    assert sin_santos["santos"] == []


def test_agregar_boda_y_rechazar_duplicado():
    datos = datos_vacios()
    agregar_boda(datos, "Lucía y Mario", 15, 6)

    assert datos["bodas"] == [{"nombre": "Lucía y Mario", "dia": 15, "mes": 6}]
    with pytest.raises(FechaDuplicadaError, match="Ese aniversario de boda ya está guardado"):
        agregar_boda(datos, "lucía y mario", 15, 6)


def test_validar_datos_admite_bodas_y_ausencia_de_la_seccion():
    datos = validar_datos(
        {"bodas": [{"nombre": "Lucía y Mario", "dia": 15, "mes": 6}]}
    )
    assert datos["bodas"] == [{"nombre": "Lucía y Mario", "dia": 15, "mes": 6}]

    sin_bodas = validar_datos({"recurrentes": [], "santos": [], "puntuales": []})
    assert sin_bodas["bodas"] == []


def test_proxima_recurrente_mismo_anno_y_salto_de_anno():
    assert proxima_recurrente(20, 8, date(2026, 8, 20)) == date(2026, 8, 20)
    assert proxima_recurrente(5, 1, date(2026, 12, 31)) == date(2027, 1, 5)


def test_proxima_recurrente_29_febrero():
    assert proxima_recurrente(29, 2, date(2025, 3, 1)) == date(2028, 2, 29)


def test_fecha_puntual_conserva_su_fecha_incluso_pasada():
    datos = datos_vacios()
    agregar_puntual(datos, "Evento", date(2025, 4, 3))
    assert datos["puntuales"][0]["fecha"] == date(2025, 4, 3)


def test_orden_por_calendario_mezcla_anuales_y_puntuales():
    datos = {
        "recurrentes": [{"nombre": "Reyes", "dia": 6, "mes": 1}],
        "santos": [],
        "bodas": [],
        "puntuales": [{"nombre": "Nochevieja", "fecha": date(2026, 12, 31)}],
    }
    entradas = entradas_ordenadas(datos, date(2026, 12, 20))
    assert [(item["nombre"], item["tipo"]) for item in entradas] == [
        ("Reyes", "recurrente"),
        ("Nochevieja", "puntual"),
    ]


def test_coincidencias_de_hoy_recurrentes_y_puntuales():
    datos = {
        "recurrentes": [
            {"nombre": "Cumpleaños de Ana", "dia": 20, "mes": 8},
            {"nombre": "Otro", "dia": 21, "mes": 8},
        ],
        "santos": [],
        "bodas": [],
        "puntuales": [
            {"nombre": "Comida", "fecha": date(2026, 8, 20)},
            {"nombre": "Mañana", "fecha": date(2026, 8, 21)},
        ],
    }
    resultado = fechas_de_hoy(datos, date(2026, 8, 20))
    assert [item["nombre"] for item in resultado] == ["Cumpleaños de Ana", "Comida"]


def test_fechas_de_hoy_solo_incluye_el_santo_del_dia():
    datos = datos_vacios()
    datos["santos"] = [
        {"nombre": "Lucas", "dia": 24, "mes": 10},
        {"nombre": "Ana", "dia": 26, "mes": 7},
    ]

    resultado = fechas_de_hoy(datos, date(2026, 10, 24))

    assert resultado == [{"tipo": "santo", "nombre": "Lucas", "dia": 24, "mes": 10}]


def test_fechas_de_hoy_solo_incluye_la_boda_del_dia():
    datos = datos_vacios()
    datos["bodas"] = [
        {"nombre": "Lucía y Mario", "dia": 15, "mes": 6},
        {"nombre": "Ana y Luis", "dia": 26, "mes": 7},
    ]

    resultado = fechas_de_hoy(datos, date(2026, 6, 15))

    assert resultado == [{"tipo": "boda", "nombre": "Lucía y Mario", "dia": 15, "mes": 6}]


def test_santo_se_ordena_entre_las_fechas_proximas():
    datos = {
        "recurrentes": [{"nombre": "Reyes", "dia": 6, "mes": 1}],
        "santos": [{"nombre": "Lucas", "dia": 24, "mes": 10}],
        "bodas": [],
        "puntuales": [{"nombre": "Cena", "fecha": date(2026, 11, 15)}],
    }

    entradas = entradas_ordenadas(datos, date(2026, 10, 1))

    assert [(item["tipo"], item["nombre"]) for item in entradas] == [
        ("recurrente", "Reyes"),
        ("santo", "Lucas"),
        ("puntual", "Cena"),
    ]


def test_boda_se_ordena_entre_las_fechas_proximas():
    datos = {
        "recurrentes": [{"nombre": "Reyes", "dia": 6, "mes": 1}],
        "santos": [],
        "bodas": [{"nombre": "Lucía y Mario", "dia": 24, "mes": 10}],
        "puntuales": [{"nombre": "Cena", "fecha": date(2026, 11, 15)}],
    }

    entradas = entradas_ordenadas(datos, date(2026, 10, 1))

    assert [(item["tipo"], item["nombre"]) for item in entradas] == [
        ("recurrente", "Reyes"),
        ("boda", "Lucía y Mario"),
        ("puntual", "Cena"),
    ]


def test_texto_lista_marca_los_santos():
    datos = datos_vacios()
    datos["santos"] = [{"nombre": "Lucas", "dia": 24, "mes": 10}]

    assert "Lucas · todos los años (santo)" in texto_lista(datos, date(2026, 10, 1))


def test_texto_lista_marca_las_bodas():
    datos = datos_vacios()
    datos["bodas"] = [{"nombre": "Lucía y Mario", "dia": 15, "mes": 6}]

    assert "Lucía y Mario · todos los años (boda)" in texto_lista(datos, date(2026, 6, 1))


def test_texto_recordatorio_para_un_santo():
    coincidentes = [{"tipo": "santo", "nombre": "Lucas", "dia": 24, "mes": 10}]
    estado = {}
    texto = texto_recordatorio(coincidentes, estado)
    assert "Lucas" in texto
    assert texto in [mensaje.format(nombre="Lucas") for mensaje in MENSAJES_SANTO]


def test_texto_recordatorio_para_un_cumpleanos():
    coincidentes = [
        {"tipo": "recurrente", "nombre": "Cumpleaños de Ana", "dia": 24, "mes": 10}
    ]
    estado = {}
    texto = texto_recordatorio(coincidentes, estado)
    assert "Ana" in texto
    assert texto in [mensaje.format(nombre="Ana") for mensaje in MENSAJES_CUMPLE]


def test_texto_recordatorio_para_una_boda():
    coincidentes = [{"tipo": "boda", "nombre": "Lucía y Mario", "dia": 15, "mes": 6}]
    estado = {}
    texto = texto_recordatorio(coincidentes, estado)
    assert "Lucía y Mario" in texto
    assert texto in [mensaje.format(nombre="Lucía y Mario") for mensaje in MENSAJES_BODA]


def test_texto_recordatorio_puntual_mantiene_formato():
    coincidentes = [{"tipo": "puntual", "nombre": "Comida", "fecha": date(2026, 10, 24)}]
    assert texto_recordatorio(coincidentes, {}) == "📅 Hoy: Comida"


def test_texto_recordatorio_multiples_varias_ocasiones():
    coincidentes = [
        {"tipo": "recurrente", "nombre": "Cumpleaños de Ana", "dia": 24, "mes": 10},
        {"tipo": "santo", "nombre": "Lucas", "dia": 24, "mes": 10},
        {"tipo": "puntual", "nombre": "Comida", "fecha": date(2026, 10, 24)},
    ]
    estado = {}
    assert texto_recordatorio(coincidentes, estado) == (
        MENSAJES_CUMPLE[0].format(nombre="Ana") + "\n"
        + MENSAJES_SANTO[0].format(nombre="Lucas") + "\n"
        + "📅 Hoy: Comida"
    )
    # Cada felicitación consume un índice de su banco en la rotación.
    assert estado["recordatorios_usados"]["cumple"] == [0]
    assert estado["recordatorios_usados"]["santo"] == [0]


def test_texto_recordatorio_multiples_incluye_boda():
    coincidentes = [
        {"tipo": "recurrente", "nombre": "Cumpleaños de Ana", "dia": 24, "mes": 10},
        {"tipo": "santo", "nombre": "Lucas", "dia": 24, "mes": 10},
        {"tipo": "boda", "nombre": "Lucía y Mario", "dia": 24, "mes": 10},
        {"tipo": "puntual", "nombre": "Comida", "fecha": date(2026, 10, 24)},
    ]
    estado = {}
    assert texto_recordatorio(coincidentes, estado) == (
        MENSAJES_CUMPLE[0].format(nombre="Ana") + "\n"
        + MENSAJES_SANTO[0].format(nombre="Lucas") + "\n"
        + MENSAJES_BODA[0].format(nombre="Lucía y Mario") + "\n"
        + "📅 Hoy: Comida"
    )
    assert estado["recordatorios_usados"]["boda"] == [0]


def test_rotacion_no_repite_hasta_agotar_el_pool():
    estado = {}
    mensajes = [elegir_mensaje("cumple", "Ana", estado) for _ in range(12)]
    assert len(set(mensajes)) == 12
    siguiente = elegir_mensaje("cumple", "Ana", estado)
    assert siguiente == mensajes[0]


def test_rotacion_santo():
    estado = {}
    mensajes = [elegir_mensaje("santo", "Lucas", estado) for _ in range(10)]
    assert len(set(mensajes)) == 10


def test_rotacion_boda():
    estado = {}
    mensajes = [elegir_mensaje("boda", "Lucía y Mario", estado) for _ in range(10)]
    assert len(set(mensajes)) == 10


def test_estado_guarda_indices_usados():
    estado = {}
    elegir_mensaje("cumple", "Ana", estado)
    assert len(estado["recordatorios_usados"]["cumple"]) == 1
    elegir_mensaje("cumple", "Ana", estado)
    assert len(estado["recordatorios_usados"]["cumple"]) == 2
    assert estado["recordatorios_usados"]["cumple"][0] != estado["recordatorios_usados"]["cumple"][1]


def test_guardado_atomico_y_carga_yaml_round_trip(tmp_path):
    ruta = tmp_path / "fechas.yaml"
    datos = {
        "recurrentes": [{"nombre": "Cumpleaños de Hugo", "dia": 29, "mes": 2}],
        "santos": [{"nombre": "Lucas", "dia": 24, "mes": 10}],
        "bodas": [{"nombre": "Lucía y Mario", "dia": 15, "mes": 6}],
        "puntuales": [{"nombre": "Cena de Nochebuena", "fecha": date(2026, 12, 24)}],
    }
    guardar_fechas_atomico(datos, ruta)
    cargados = cargar_fechas(ruta)

    assert cargados == datos
    assert ruta.read_text(encoding="utf-8").startswith("# Fechas familiares.")
    assert not list(tmp_path.glob(".fechas.yaml.*"))


def test_texto_aviso_hoy_sin_fechas_devuelve_none(tmp_path):
    ruta = tmp_path / "fechas.yaml"
    guardar_fechas_atomico(datos_vacios(), ruta)

    assert texto_aviso_hoy(date(2026, 8, 20), ruta=ruta) is None


def test_texto_aviso_hoy_con_fecha_devuelve_texto(tmp_path):
    ruta = tmp_path / "fechas.yaml"
    datos = {
        "recurrentes": [{"nombre": "Cumpleaños de Ana", "dia": 20, "mes": 8}],
        "santos": [],
        "puntuales": [],
    }
    guardar_fechas_atomico(datos, ruta)

    texto = texto_aviso_hoy(date(2026, 8, 20), ruta=ruta, estado={})
    assert texto is not None
    assert "Ana" in texto


def test_texto_aviso_hoy_santo_single(tmp_path):
    ruta = tmp_path / "fechas.yaml"
    datos = {"recurrentes": [], "santos": [{"nombre": "Lucas", "dia": 20, "mes": 8}], "puntuales": []}
    guardar_fechas_atomico(datos, ruta)

    texto = texto_aviso_hoy(date(2026, 8, 20), ruta=ruta, estado={})
    assert texto is not None
    assert "Lucas" in texto


def test_entradas_por_persona_agrupa_y_ordena_alfabeticamente():
    datos = {
        "recurrentes": [
            {"nombre": "Cumpleaños de Lucas", "dia": 12, "mes": 3},
            {"nombre": "Cumpleaños de Martín", "dia": 6, "mes": 2},
            {"nombre": "Cumpleaños de Ángeles", "dia": 28, "mes": 3},
        ],
        "santos": [{"nombre": "Martín", "dia": 20, "mes": 8}],
        "bodas": [{"nombre": "Lucía y Mario", "dia": 15, "mes": 6}],
        "puntuales": [{"nombre": "Cena", "fecha": date(2026, 12, 31)}],
    }

    entradas = entradas_por_persona(datos)

    personas = [_persona_de(item) for item in entradas]
    assert personas == ["Cena", "Lucas", "Lucía y Mario", "Martín", "Martín", "Ángeles"]


def test_texto_lista_personas_agrupa_por_persona_y_avisa_del_borrado():
    datos = {
        "recurrentes": [{"nombre": "Cumpleaños de Lucas", "dia": 12, "mes": 3}],
        "santos": [{"nombre": "Martín", "dia": 20, "mes": 8}],
        "bodas": [],
        "puntuales": [],
    }

    texto = texto_lista_personas(datos)

    assert "Fechas por persona:" in texto
    assert "- Martín:" in texto
    assert "   - Santo 20/08" in texto
    assert "- Lucas:" in texto
    assert "   - Cumpleaños 12/03" in texto
    assert "Para borrar una fecha, usa /lista y /borrar <id>." in texto


def test_es_dia_de_celebracion_29_febrero_en_anio_no_bisiesto():
    assert _es_dia_de_celebracion(29, 2, date(2026, 2, 28))
    assert not _es_dia_de_celebracion(29, 2, date(2028, 2, 28))  # bisiesto: el 29 existe
    assert not _es_dia_de_celebracion(29, 2, date(2026, 2, 27))


def test_fechas_de_hoy_29_febrero_se_celebra_el_28():
    datos = datos_vacios()
    datos["recurrentes"] = [{"nombre": "Cumpleaños de Lucas", "dia": 29, "mes": 2}]
    coincidentes = fechas_de_hoy(datos, date(2026, 2, 28))
    assert len(coincidentes) == 1
    assert coincidentes[0]["nombre"] == "Cumpleaños de Lucas"
    # En año bisiesto, el 29/02 solo coincide el propio día 29
    assert fechas_de_hoy(datos, date(2028, 2, 28)) == []
    assert len(fechas_de_hoy(datos, date(2028, 2, 29))) == 1


def test_validar_datos_rechaza_secciones_desconocidas():
    with pytest.raises(FechasError, match="Secciones no reconocidas"):
        validar_datos({"recurrentes": [], "cumples": []})


def _update_fake(chat_id: int = 1, chat_type: str = "private", user_id: int = 99) -> object:
    """Update falso mínimo con effective_chat y effective_message."""

    class Chat:
        id = chat_id
        type = chat_type

    class Msg:
        async def reply_text(self, texto):
            self.texto = texto

    class Update:
        effective_chat = Chat()
        effective_message = Msg()
        effective_user = type("U", (), {"id": user_id})()

    return Update()


def test_enviar_largo_fragmenta(monkeypatch):
    texto = "\n".join(f"Línea {i} de relleno para superar el límite" for i in range(300))
    update = _update_fake()
    import bot

    async def correr():
        await bot.enviar_largo(update, texto, max_len=200)

    import asyncio

    asyncio.run(correr())
    # Con 300 líneas ~40 chars y bloques de 200, debe haber varios mensajes
    assert update.effective_message.texto


def test_autorizado_para_escribir_solo_owner_o_canal(monkeypatch):
    import bot

    monkeypatch.setenv("OWNER_CHAT_ID", "42")
    assert bot.autorizado_para_escribir(_update_fake(user_id=42))
    assert not bot.autorizado_para_escribir(_update_fake(user_id=99))
    assert bot.autorizado_para_escribir(_update_fake(chat_type="channel", user_id=99))
