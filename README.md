# Telegram bot de fechas importantes para la familia

Un bot de Telegram que guarda las fechas señaladas de una familia —cumpleaños, santos, aniversarios de boda y eventos puntuales— y avisa en el grupo o canal cuando llega el día. Cada aviso usa un mensaje de felicitación distinto: el bot rota entre varios bancos de mensajes para que no se repitan.

Los datos viven en un único archivo `fechas.yaml` que el bot relee en cada operación. Se pueden añadir fechas por chat con comandos o editando el archivo a mano: ambas vías conviven sin problemas.

## Comandos

| Comando | Función | Ejemplo |
|---|---|---|
| `/start` | Presenta el bot | `/start` |
| `/ayuda` | Muestra la ayuda | `/ayuda` |
| `/cumple <Nombre> <DD/MM>` | Añade un cumpleaños anual | `/cumple María 12/03` |
| `/santo <Nombre> <DD/MM>` | Añade el santo anual de una persona | `/santo Jorge 24/10` |
| `/boda <Nombres> <DD/MM>` | Añade un aniversario de boda anual | `/boda Lucía y Mario 15/06` |
| `/fecha <Nombre> <DD/MM/AAAA>` | Añade un evento puntual | `/fecha Cena Nochebuena 24/12/2026` |
| `/lista` | Lista las fechas ordenadas por calendario (las anuales muestran solo DD/MM) | `/lista` |
| `/listap` | Lista las fechas agrupadas por persona | `/listap` |
| `/borrar <id>` | Borra una fecha por el id que muestra `/lista` | `/borrar 2` |
| `/chatid` | Muestra el id y el tipo del chat actual | `/chatid` |
| `/recargar` | Vuelve a leer `fechas.yaml` | `/recargar` |
| `/avisar` | Envía ahora el aviso del día | `/avisar` |

Los comandos de lectura son públicos. Los de escritura (`/cumple`, `/santo`, `/boda`, `/fecha`, `/borrar`, `/recargar`, `/avisar`) solo los puede usar quien configura `OWNER_CHAT_ID` —o cualquiera desde un canal, donde solo los administradores pueden publicar.

## Aviso diario

Cada día a la hora configurada (`AVISO_HORA`, por defecto 09:00 en `Europe/Madrid`) el bot comprueba si hay fechas ese día y, si las hay, envía un único mensaje al canal de la familia. Si no hay nada, no envía nada.

- Una sola persona: usa un mensaje de su banco (cumpleaños 🎂, santo ⛪, boda 💍, aniversario 📅), sin repetir hasta agotar el banco.
- Varias personas el mismo día: cada una recibe su felicitación individual en el mismo mensaje.
- Eventos puntuales: línea simple `📅 Hoy: ...`, sin felicitación.

## Instalación

Se necesita Python 3.11 o posterior y [`uv`](https://docs.astral.sh/uv/). Desde el directorio del proyecto:

```bash
uv venv
uv pip install --python .venv/bin/python -r requirements.txt
```

### Configuración

1. Crea el bot con [@BotFather](https://t.me/BotFather) (`/newbot`) y copia el token.
2. Copia `.env.example` a `.env` y rellena:
   - `TOKEN` — el token de BotFather.
   - `FAMILY_CHAT_ID` — id del chat donde avisar (puede dejarse vacío: el bot se vincula solo al primer canal que vea y avisa por DM).
   - `OWNER_CHAT_ID` — tu id de Telegram, para autorizar los comandos de escritura.
   - `AVISO_HORA` — hora del aviso diario, en `HH:MM`.
3. Copia `fechas.example.yaml` a `fechas.yaml` y pon tus fechas, o añádelas luego con los comandos.

### Puesta en marcha

```bash
.venv/bin/python bot.py
```

Para dejarlo corriendo de forma permanente como servicio de usuario:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/fechas-familia.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now fechas-familia
loginctl enable-linger $USER   # para que siga vivo sin sesión abierta
```

## Estructura de `fechas.yaml`

```yaml
recurrentes:        # cumpleaños y aniversarios, se repiten todos los años
  - nombre: Cumpleaños de María
    dia: 12
    mes: 3
santos:             # santos anuales
  - nombre: Jorge
    dia: 20
    mes: 8
bodas:              # aniversarios de boda anuales
  - nombre: Lucía y Mario
    dia: 15
    mes: 6
puntuales:          # eventos de un solo día
  - nombre: Cena de Nochebuena
    fecha: 2026-12-24
```

Las anuales admiten el 29 de febrero. Las puntuales deben ser fechas reales en formato `AAAA-MM-DD`.

## Qué no se versiona

- `.env` — token e ids (se crea a partir de `.env.example`).
- `fechas.yaml` — tus fechas reales (se crea a partir de `fechas.example.yaml`).
- `state.json` — estado de la rotación de mensajes y del canal vinculado.

## Tests

```bash
.venv/bin/python -m pytest test_fechas.py
```

## Licencia

MIT
