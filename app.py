from __future__ import annotations

import json
import math
import os
import secrets
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
from flask import (
    Flask,
    flash,
    g,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)

try:
    from twilio.rest import Client as TwilioClient
except Exception:
    TwilioClient = None


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "repartos.db"
UPLOAD_DIR = BASE_DIR / "uploads"
STATIC_DIR = BASE_DIR / "static"
AUTO_IMPORT_DIR = BASE_DIR / "auto_import"

UPLOAD_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)
AUTO_IMPORT_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = "cambiar-por-una-clave-segura"
ADMIN_PASSWORD = "Ponticelli2026"

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:5000")
GOOGLE_SHEET_URL = os.environ.get("GOOGLE_SHEET_URL", "")


# =========================
# Base de datos
# =========================
def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception: Optional[BaseException]) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row[1] == column_name for row in rows)


def init_db() -> None:
    schema = """
    CREATE TABLE IF NOT EXISTS repartos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        apellido TEXT NOT NULL,
        nombre TEXT,
        calle TEXT NOT NULL,
        direccion_completa TEXT,
        numero_pedido TEXT,
        telefono_cliente TEXT,
        email_cliente TEXT,
        fecha_reparto TEXT NOT NULL,
        va_hoy INTEGER NOT NULL DEFAULT 0,
        franja_horaria TEXT,
        chofer_nombre TEXT,
        chofer_telefono TEXT,
        estado TEXT DEFAULT 'Pendiente',
        observaciones TEXT,
        latitud REAL,
        longitud REAL,
        orden_ruta INTEGER DEFAULT 0,
        token_seguimiento TEXT UNIQUE,
        ultimo_envio TEXT,
        chofer_latitud REAL,
        chofer_longitud REAL,
        chofer_ultima_actualizacion TEXT,
        importado_desde TEXT
    );
    """

    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(schema)
        conn.commit()

        extra_columns = [
            ("direccion_completa", "TEXT"),
            ("telefono_cliente", "TEXT"),
            ("email_cliente", "TEXT"),
            ("latitud", "REAL"),
            ("longitud", "REAL"),
            ("orden_ruta", "INTEGER DEFAULT 0"),
            ("token_seguimiento", "TEXT"),
            ("ultimo_envio", "TEXT"),
            ("chofer_latitud", "REAL"),
            ("chofer_longitud", "REAL"),
            ("chofer_ultima_actualizacion", "TEXT"),
            ("importado_desde", "TEXT"),
        ]
        for col_name, col_type in extra_columns:
            if not column_exists(conn, "repartos", col_name):
                conn.execute(f"ALTER TABLE repartos ADD COLUMN {col_name} {col_type}")
        conn.commit()

        total = conn.execute("SELECT COUNT(*) FROM repartos").fetchone()[0]
        if total == 0:
            hoy = date.today().isoformat()
            ejemplos = [
                (
                    "García",
                    "Ana",
                    "San Martín",
                    "San Martín 123",
                    "PED-1001",
                    "5491155551234",
                    "",
                    hoy,
                    1,
                    "09:00 a 12:00",
                    "Carlos Gómez",
                    "11-5555-1234",
                    "En preparación",
                    "Llamar al llegar",
                    -34.6037,
                    -58.3816,
                    1,
                    secrets.token_urlsafe(16),
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
                (
                    "Pérez",
                    "Luis",
                    "Belgrano",
                    "Belgrano 456",
                    "PED-1002",
                    "5491144445678",
                    "",
                    hoy,
                    1,
                    "14:00 a 17:00",
                    "Carlos Gómez",
                    "11-5555-1234",
                    "Pendiente",
                    "",
                    -34.6090,
                    -58.3920,
                    2,
                    secrets.token_urlsafe(16),
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
            ]
            conn.executemany(
                """
                INSERT INTO repartos (
                    apellido, nombre, calle, direccion_completa, numero_pedido,
                    telefono_cliente, email_cliente, fecha_reparto, va_hoy,
                    franja_horaria, chofer_nombre, chofer_telefono, estado,
                    observaciones, latitud, longitud, orden_ruta,
                    token_seguimiento, ultimo_envio, chofer_latitud,
                    chofer_longitud, chofer_ultima_actualizacion, importado_desde
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ejemplos,
            )
            conn.commit()


# =========================
# Utilidades
# =========================
def admin_required():
    if not session.get("admin_ok"):
        return redirect(url_for("admin_login"))
    return None


def normalizar_texto(valor: Any) -> str:
    if valor is None:
        return ""
    return str(valor).strip()


def generar_token() -> str:
    return secrets.token_urlsafe(16)


def tracking_url(token: str) -> str:
    return f"{PUBLIC_BASE_URL.rstrip('/')}/seguimiento/{token}"


def chofer_url(token: str) -> str:
    return f"{PUBLIC_BASE_URL.rstrip('/')}/chofer/{token}"


def construir_mensaje_cliente(row: sqlite3.Row) -> str:
    return (
        f"Hola {row['nombre'] or row['apellido']}, "
        f"podés seguir tu pedido {row['numero_pedido'] or ''} acá: "
        f"{tracking_url(row['token_seguimiento'])}"
    )


def whatsapp_deep_link(numero: str, mensaje: str) -> str:
    numero_limpio = "".join(ch for ch in str(numero) if ch.isdigit())
    texto = requests.utils.quote(mensaje)
    return f"https://wa.me/{numero_limpio}?text={texto}"


def twilio_disponible() -> bool:
    return bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM and TwilioClient)


def enviar_whatsapp_automatico(numero: str, mensaje: str) -> tuple[bool, str]:
    if not twilio_disponible():
        return False, "Twilio no configurado"

    try:
        client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        to_number = numero if str(numero).startswith("whatsapp:") else f"whatsapp:+{''.join(ch for ch in str(numero) if ch.isdigit())}"
        client.messages.create(
            body=mensaje,
            from_=TWILIO_WHATSAPP_FROM,
            to=to_number,
        )
        return True, "Enviado"
    except Exception as exc:
        return False, f"Error Twilio: {exc}"


def obtener_ruta_osrm(puntos: List[Dict[str, float]]) -> List[List[float]]:
    if len(puntos) < 2:
        return [[p["lat"], p["lon"]] for p in puntos]

    try:
        coords = ";".join(f"{p['lon']},{p['lat']}" for p in puntos)
        url = f"https://router.project-osrm.org/route/v1/driving/{coords}?overview=full&geometries=geojson"
        respuesta = requests.get(url, timeout=10)
        data = respuesta.json()
        if data.get("routes"):
            geometry = data["routes"][0]["geometry"]["coordinates"]
            return [[coord[1], coord[0]] for coord in geometry]
    except Exception:
        pass

    return [[p["lat"], p["lon"]] for p in puntos]


def distancia_haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radio = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radio * c


def optimizar_ruta_simple(paradas: List[sqlite3.Row]) -> List[sqlite3.Row]:
    validas = [p for p in paradas if p["latitud"] is not None and p["longitud"] is not None]
    if len(validas) < 2:
        return paradas

    restantes = validas[:]
    actual = restantes.pop(0)
    ordenadas = [actual]

    while restantes:
        siguiente = min(
            restantes,
            key=lambda p: distancia_haversine(
                float(actual["latitud"]),
                float(actual["longitud"]),
                float(p["latitud"]),
                float(p["longitud"]),
            ),
        )
        restantes.remove(siguiente)
        ordenadas.append(siguiente)
        actual = siguiente

    ids_ordenados = [row["id"] for row in ordenadas]
    mapa = {row["id"]: row for row in paradas}
    return [mapa[i] for i in ids_ordenados]


def leer_excel_o_csv(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)

    # En este Excel los encabezados reales están en la fila 9
    return pd.read_excel(path, header=8)

def importar_desde_archivo(path: Path, fuente_automatica: bool = False) -> str:
    df = leer_excel_o_csv(path)
    db = get_db()
    insertados = 0

    df.columns = [str(col).strip() for col in df.columns]

    localidad_actual = ""

    for _, row in df.iterrows():
        cliente = normalizar_texto(row.get("Cliente"))
        domicilio = normalizar_texto(row.get("Domicilio Entrega"))
        telefono = normalizar_texto(row.get("Telefono"))
        repartidor = normalizar_texto(row.get("Repartidor"))
        estado_entrega = normalizar_texto(row.get("Estado Entrega"))

        # Detectar filas de localidad
        if cliente.startswith("Localidad:"):
            texto = cliente.upper()
            if "RECONQUISTA" in texto:
                localidad_actual = "RECONQUISTA"
            elif "AVELLANEDA" in texto:
                localidad_actual = "AVELLANEDA"
            else:
                localidad_actual = ""
            continue

        # Ignorar filas vacías o auxiliares
        if not cliente or cliente.startswith("# Repartos"):
            continue

        if not domicilio:
            continue

        fecha_reparto = date.today().isoformat()

        # Evitar duplicados usando cliente + domicilio + fecha
        existe = db.execute(
            """
            SELECT id FROM repartos
            WHERE apellido = ?
              AND direccion_completa = ?
              AND fecha_reparto = ?
            """,
            (cliente, domicilio, fecha_reparto),
        ).fetchone()

        if existe:
            continue

        estado_final = "En reparto"
        if "SIN ENTREGAR" in estado_entrega.upper():
            estado_final = "Sin entregar"
        elif "EN REPARTO" in estado_entrega.upper():
            estado_final = "En reparto"

        observaciones = f"Localidad: {localidad_actual}" if localidad_actual else ""

        token = generar_token()

        db.execute(
            """
            INSERT INTO repartos (
                apellido, nombre, calle, direccion_completa, numero_pedido,
                telefono_cliente, email_cliente, fecha_reparto, va_hoy,
                franja_horaria, chofer_nombre, chofer_telefono, estado,
                observaciones, latitud, longitud, orden_ruta,
                token_seguimiento, importado_desde
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cliente,              # apellido: usamos este campo para guardar cliente completo
                "",                   # nombre
                domicilio,            # calle
                domicilio,            # dirección completa
                "",                   # número de pedido
                telefono,             # teléfono cliente
                "",                   # email cliente
                fecha_reparto,        # fecha
                1,                    # va_hoy
                "",                   # franja horaria
                repartidor,           # chofer
                "",                   # teléfono chofer
                estado_final,         # estado
                observaciones,        # observaciones
                None,                 # latitud
                None,                 # longitud
                0,                    # orden ruta
                token,                # token seguimiento
                path.name if fuente_automatica else "import_manual",
            ),
        )
        insertados += 1

    db.commit()
    return f"Importación terminada. Se cargaron {insertados} repartos desde {path.name}."

    for _, row in df.iterrows():
        apellido = normalizar_texto(row.get("apellido"))
        calle = normalizar_texto(row.get("calle"))
        fecha_reparto = normalizar_texto(row.get("fecha_reparto"))

        if not apellido or not calle or not fecha_reparto:
            continue

        numero_pedido = normalizar_texto(row.get("numero_pedido"))

        existe = db.execute(
            """
            SELECT id FROM repartos
            WHERE numero_pedido = ?
              AND fecha_reparto = ?
            """,
            (numero_pedido, fecha_reparto),
        ).fetchone()

        if numero_pedido and existe:
            continue

        token = generar_token()
        db.execute(
            """
            INSERT INTO repartos (
                apellido, nombre, calle, direccion_completa, numero_pedido,
                telefono_cliente, email_cliente, fecha_reparto, va_hoy,
                franja_horaria, chofer_nombre, chofer_telefono, estado,
                observaciones, latitud, longitud, orden_ruta,
                token_seguimiento, importado_desde
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                apellido,
                normalizar_texto(row.get("nombre")),
                calle,
                normalizar_texto(row.get("direccion_completa")),
                numero_pedido,
                normalizar_texto(row.get("telefono_cliente")),
                normalizar_texto(row.get("email_cliente")),
                fecha_reparto,
                1 if str(row.get("va_hoy", "")).strip().lower() in {"1", "si", "sí", "true", "x"} else 0,
                normalizar_texto(row.get("franja_horaria")),
                normalizar_texto(row.get("chofer_nombre")),
                normalizar_texto(row.get("chofer_telefono")),
                normalizar_texto(row.get("estado")) or "Pendiente",
                normalizar_texto(row.get("observaciones")),
                float(row.get("latitud")) if pd.notna(row.get("latitud")) else None,
                float(row.get("longitud")) if pd.notna(row.get("longitud")) else None,
                int(row.get("orden_ruta")) if pd.notna(row.get("orden_ruta")) else 0,
                token,
                path.name if fuente_automatica else "import_manual",
            ),
        )
        insertados += 1

    db.commit()
    return f"Importación terminada. Se cargaron {insertados} repartos desde {path.name}."


def auto_importar_archivo_si_existe() -> Optional[str]:
    posibles = [
        AUTO_IMPORT_DIR / "repartos_hoy.xlsx",
        AUTO_IMPORT_DIR / "repartos_hoy.xls",
        AUTO_IMPORT_DIR / "repartos_hoy.csv",
    ]
    for archivo in posibles:
        if archivo.exists():
            try:
                return importar_desde_archivo(archivo, fuente_automatica=True)
            except Exception as exc:
                return f"No se pudo autoimportar {archivo.name}: {exc}"
    return None


def importar_desde_google_sheets() -> str:
    if not GOOGLE_SHEET_URL:
        return "Google Sheets no configurado."

    df = pd.read_csv(GOOGLE_SHEET_URL)
    db = get_db()
    insertados = 0

    for _, row in df.iterrows():
        apellido = normalizar_texto(row.get("apellido"))
        calle = normalizar_texto(row.get("calle"))
        fecha_reparto = normalizar_texto(row.get("fecha_reparto"))

        if not apellido or not calle or not fecha_reparto:
            continue

        numero_pedido = normalizar_texto(row.get("numero_pedido"))

        existe = db.execute(
            """
            SELECT id FROM repartos
            WHERE numero_pedido = ?
              AND fecha_reparto = ?
            """,
            (numero_pedido, fecha_reparto),
        ).fetchone()

        if numero_pedido and existe:
            continue

        token = generar_token()

        db.execute(
            """
            INSERT INTO repartos (
                apellido, nombre, calle, direccion_completa, numero_pedido,
                telefono_cliente, email_cliente, fecha_reparto, va_hoy,
                franja_horaria, chofer_nombre, chofer_telefono, estado,
                observaciones, latitud, longitud, orden_ruta,
                token_seguimiento, importado_desde
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                apellido,
                normalizar_texto(row.get("nombre")),
                calle,
                normalizar_texto(row.get("direccion_completa")),
                numero_pedido,
                normalizar_texto(row.get("telefono_cliente")),
                normalizar_texto(row.get("email_cliente")),
                fecha_reparto,
                1 if str(row.get("va_hoy", "")).strip().lower() in {"1", "si", "sí", "true", "x"} else 0,
                normalizar_texto(row.get("franja_horaria")),
                normalizar_texto(row.get("chofer_nombre")),
                normalizar_texto(row.get("chofer_telefono")),
                normalizar_texto(row.get("estado")) or "Pendiente",
                normalizar_texto(row.get("observaciones")),
                float(row.get("latitud")) if pd.notna(row.get("latitud")) else None,
                float(row.get("longitud")) if pd.notna(row.get("longitud")) else None,
                int(row.get("orden_ruta")) if pd.notna(row.get("orden_ruta")) else 0,
                token,
                "google_sheets",
            ),
        )
        insertados += 1

    db.commit()
    return f"Se importaron {insertados} repartos desde Google Sheets."


def notificar_si_en_camino(row: sqlite3.Row) -> Optional[str]:
    if row["estado"] != "En camino" or not row["telefono_cliente"]:
        return None

    mensaje = construir_mensaje_cliente(row)
    enviado, detalle = enviar_whatsapp_automatico(row["telefono_cliente"], mensaje)
    if enviado:
        db = get_db()
        db.execute(
            "UPDATE repartos SET ultimo_envio = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), row["id"]),
        )
        db.commit()
        return "Cliente notificado automáticamente."
    return f"No se pudo enviar automático. {detalle}"


# =========================
# Plantilla base
# =========================
BASE_HTML = """
<!doctype html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ title }}</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <style>
        body {
            font-family: Arial, sans-serif;
            background: #f5f6fa;
            margin: 0;
            padding: 0;
            color: #222;
        }
        .container {
            max-width: 1250px;
            margin: 30px auto;
            background: white;
            padding: 24px;
            border-radius: 12px;
            box-shadow: 0 6px 20px rgba(0,0,0,0.08);
        }
        h1, h2, h3 { margin-top: 0; }
        form {
            display: grid;
            gap: 12px;
            margin-bottom: 20px;
        }
        input, select, textarea {
            padding: 10px;
            border: 1px solid #ccc;
            border-radius: 8px;
            font-size: 14px;
            width: 100%;
            box-sizing: border-box;
        }
        button, .btn {
            background: #1f6feb;
            color: white;
            border: none;
            padding: 10px 16px;
            border-radius: 8px;
            cursor: pointer;
            text-decoration: none;
            display: inline-block;
        }
        .btn-secondary { background: #6c757d; }
        .btn-success { background: #157347; }
        .btn-warning { background: #b78103; }
        .btn-danger { background: #b42318; }
        .grid-2 {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
        }
        .grid-3 {
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 12px;
        }
        .card {
            border: 1px solid #e5e7eb;
            border-radius: 10px;
            padding: 16px;
            margin-top: 16px;
            background: #fafafa;
        }
        .ok { color: #0a7f2e; font-weight: bold; }
        .no { color: #b42318; font-weight: bold; }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 16px;
        }
        th, td {
            border-bottom: 1px solid #e5e7eb;
            text-align: left;
            padding: 10px 8px;
            vertical-align: top;
        }
        .topbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 12px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }
        .brand {
            display: flex;
            align-items: center;
            gap: 16px;
            flex-wrap: wrap;
        }
        .brand img {
            height: 64px;
            width: auto;
            object-fit: contain;
        }
        .flash {
            padding: 10px;
            border-radius: 8px;
            background: #fff4d6;
            margin-bottom: 16px;
        }
        .mini-form {
            display: grid;
            gap: 8px;
            min-width: 330px;
        }
        #map {
            height: 480px;
            border-radius: 12px;
            overflow: hidden;
            margin-top: 10px;
        }
        .actions {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }
        code {
            background: #f1f3f5;
            padding: 2px 5px;
            border-radius: 4px;
        }
        .small { font-size: 12px; color: #555; }
        @media (max-width: 900px) {
            .grid-2, .grid-3 { grid-template-columns: 1fr; }
            .topbar { flex-direction: column; align-items: stretch; }
            .container { margin: 10px; }
        }
    </style>
</head>
<body>
    <div class="container">
        {{ body|safe }}
    </div>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
</body>
</html>
"""


def page(title: str, body: str, **context: object) -> str:
    return render_template_string(
        BASE_HTML,
        title=title,
        body=render_template_string(body, **context),
    )


# =========================
# Cliente
# =========================
@app.route("/", methods=["GET", "POST"])
def consulta_cliente() -> str:
    resultado = None
    multiples = []

    if request.method == "POST":
        apellido = normalizar_texto(request.form.get("apellido"))
        calle = normalizar_texto(request.form.get("calle"))
        pedido = normalizar_texto(request.form.get("numero_pedido"))
        hoy = date.today().isoformat()

        if not apellido:
            flash("Ingresá al menos el apellido.")
            return redirect(url_for("consulta_cliente"))

        db = get_db()
        params = [apellido.lower(), hoy]
        sql = """
            SELECT *
            FROM repartos
            WHERE LOWER(apellido) = ?
              AND fecha_reparto = ?
        """

        if calle:
            sql += " AND LOWER(calle) = ?"
            params.append(calle.lower())

        if pedido:
            sql += " AND LOWER(numero_pedido) = ?"
            params.append(pedido.lower())

        rows = db.execute(sql, params).fetchall()

        if len(rows) == 1:
            resultado = rows[0]
        elif len(rows) > 1:
            multiples = rows
        else:
            multiples = db.execute(
                """
                SELECT * FROM repartos
                WHERE LOWER(apellido) = ?
                ORDER BY fecha_reparto DESC
                LIMIT 5
                """,
                (apellido.lower(),),
            ).fetchall()

    body = """
    <div class="topbar">
        <div class="brand">
            <img src="{{ url_for('static', filename='logo.png') }}" alt="Logo empresa">
            <div>
                <h1>Consulta de reparto</h1>
                <p>Consultá si tu pedido va hoy y el contacto del chofer.</p>
            </div>
        </div>
    </div>

    {% with messages = get_flashed_messages() %}
      {% if messages %}
        {% for message in messages %}
          <div class="flash">{{ message }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}

    <form method="post">
        <div class="grid-2">
            <div>
                <label>Apellido</label>
                <input name="apellido" placeholder="Ej: García" required>
            </div>
            <div>
                <label>Calle (recomendado)</label>
                <input name="calle" placeholder="Ej: San Martín">
            </div>
        </div>

        <div>
            <label>Número de pedido (opcional)</label>
            <input name="numero_pedido" placeholder="Ej: PED-1001">
        </div>

        <div>
            <button type="submit">Consultar</button>
        </div>
    </form>

    {% if resultado %}
        <div class="card">
            <h2>Resultado</h2>
            <p><strong>Cliente:</strong> {{ resultado['apellido'] }}, {{ resultado['nombre'] or '-' }}</p>
            <p><strong>Pedido:</strong> {{ resultado['numero_pedido'] or '-' }}</p>
            <p><strong>Fecha de reparto:</strong> {{ resultado['fecha_reparto'] }}</p>
            <p><strong>¿Va hoy?:</strong>
                {% if resultado['va_hoy'] %}
                    <span class="ok">Sí</span>
                {% else %}
                    <span class="no">No</span>
                {% endif %}
            </p>
            <p><strong>Franja horaria:</strong> {{ resultado['franja_horaria'] or '-' }}</p>
            <p><strong>Chofer:</strong> {{ resultado['chofer_nombre'] or '-' }}</p>
            <p><strong>Teléfono del chofer:</strong> {{ resultado['chofer_telefono'] or '-' }}</p>
            <p><strong>Estado:</strong> {{ resultado['estado'] or '-' }}</p>
            <p><strong>Observaciones:</strong> {{ resultado['observaciones'] or '-' }}</p>
        </div>
    {% elif multiples %}
        <div class="card">
            <h2>Resultados encontrados</h2>
            <table>
                <thead>
                    <tr>
                        <th>Fecha</th>
                        <th>Cliente</th>
                        <th>Calle</th>
                        <th>Pedido</th>
                        <th>¿Va hoy?</th>
                        <th>Chofer</th>
                        <th>Teléfono</th>
                        <th>Estado</th>
                        <th>Observaciones</th>
                    </tr>
                </thead>
                <tbody>
                {% for row in multiples %}
                    <tr>
                        <td>{{ row['fecha_reparto'] }}</td>
                        <td>{{ row['apellido'] }}, {{ row['nombre'] or '-' }}</td>
                        <td>{{ row['calle'] }}</td>
                        <td>{{ row['numero_pedido'] or '-' }}</td>
                        <td>{{ 'Sí' if row['va_hoy'] else 'No' }}</td>
                        <td>{{ row['chofer_nombre'] or '-' }}</td>
                        <td>{{ row['chofer_telefono'] or '-' }}</td>
                        <td>{{ row['estado'] or '-' }}</td>
                        <td>{{ row['observaciones'] or '-' }}</td>
                    </tr>
                {% endfor %}
                </tbody>
            </table>
        </div>
    {% endif %}
    """
    return page("Consulta de reparto", body, resultado=resultado, multiples=multiples)


# =========================
# Login admin
# =========================
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = normalizar_texto(request.form.get("password"))
        if password == ADMIN_PASSWORD:
            session["admin_ok"] = True
            return redirect(url_for("admin"))
        flash("Contraseña incorrecta.")

    body = """
    <div class="topbar">
        <div class="brand">
            <img src="{{ url_for('static', filename='logo.png') }}" alt="Logo empresa">
            <div>
                <h1>Ingreso al panel interno</h1>
                <p>Acceso restringido.</p>
            </div>
        </div>
    </div>

    {% with messages = get_flashed_messages() %}
      {% if messages %}
        {% for message in messages %}
          <div class="flash">{{ message }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}

    <form method="post" style="max-width:400px;">
        <label>Contraseña</label>
        <input type="password" name="password" required>
        <button type="submit">Ingresar</button>
    </form>
    """
    return page("Login admin", body)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_ok", None)
    return redirect(url_for("admin_login"))


# =========================
# Seguimiento cliente
# =========================
@app.route("/seguimiento/<token>")
def seguimiento_cliente(token: str) -> str:
    db = get_db()
    row = db.execute("SELECT * FROM repartos WHERE token_seguimiento = ?", (token,)).fetchone()

    if not row:
        return page("Seguimiento", "<h1>Seguimiento no encontrado</h1><p>El enlace no es válido.</p>")

    body = """
    <div class="topbar">
        <div class="brand">
            <img src="{{ url_for('static', filename='logo.png') }}" alt="Logo empresa">
            <div>
                <h1>Seguimiento de pedido</h1>
                <p>Estado actualizado del reparto.</p>
            </div>
        </div>
    </div>

    <div class="card">
        <p><strong>Cliente:</strong> {{ row['apellido'] }}, {{ row['nombre'] or '-' }}</p>
        <p><strong>Pedido:</strong> {{ row['numero_pedido'] or '-' }}</p>
        <p><strong>Fecha:</strong> {{ row['fecha_reparto'] }}</p>
        <p><strong>Estado:</strong> {{ row['estado'] or '-' }}</p>
        <p><strong>Franja:</strong> {{ row['franja_horaria'] or '-' }}</p>
        <p><strong>Chofer:</strong> {{ row['chofer_nombre'] or '-' }}</p>
        <p><strong>Teléfono chofer:</strong> {{ row['chofer_telefono'] or '-' }}</p>
        <p><strong>Observaciones:</strong> {{ row['observaciones'] or '-' }}</p>
        {% if row['chofer_latitud'] and row['chofer_longitud'] %}
            <p><strong>Ubicación estimada del chofer:</strong> actualizada {{ row['chofer_ultima_actualizacion'] or '-' }}</p>
            <div id="map"></div>
            <script>
                const map = L.map('map').setView([{{ row['chofer_latitud'] }}, {{ row['chofer_longitud'] }}], 14);
                L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                    attribution: '&copy; OpenStreetMap'
                }).addTo(map);
                L.marker([{{ row['chofer_latitud'] }}, {{ row['chofer_longitud'] }}]).addTo(map)
                    .bindPopup('Chofer en ruta');
            </script>
        {% endif %}
    </div>
    """
    return page("Seguimiento", body, row=row)


# =========================
# Vista chofer + GPS
# =========================
@app.route("/chofer/<token>", methods=["GET", "POST"])
def vista_chofer(token: str) -> str:
    db = get_db()
    row = db.execute("SELECT * FROM repartos WHERE token_seguimiento = ?", (token,)).fetchone()

    if not row:
        return page("Chofer", "<h1>Reparto no encontrado</h1>")

    if request.method == "POST":
        estado = normalizar_texto(request.form.get("estado"))
        observaciones = normalizar_texto(request.form.get("observaciones"))
        lat = normalizar_texto(request.form.get("chofer_latitud"))
        lon = normalizar_texto(request.form.get("chofer_longitud"))

        db.execute(
            """
            UPDATE repartos
            SET estado = ?,
                observaciones = ?,
                chofer_latitud = ?,
                chofer_longitud = ?,
                chofer_ultima_actualizacion = ?
            WHERE token_seguimiento = ?
            """,
            (
                estado,
                observaciones,
                float(lat) if lat else row["chofer_latitud"],
                float(lon) if lon else row["chofer_longitud"],
                datetime.now().isoformat(timespec="seconds"),
                token,
            ),
        )
        db.commit()
        flash("Estado actualizado desde celular.")
        return redirect(url_for("vista_chofer", token=token))

    body = """
    <div class="topbar">
        <div class="brand">
            <img src="{{ url_for('static', filename='logo.png') }}" alt="Logo empresa">
            <div>
                <h1>Panel chofer</h1>
                <p>Actualización rápida del reparto.</p>
            </div>
        </div>
    </div>

    {% with messages = get_flashed_messages() %}
      {% if messages %}
        {% for message in messages %}
          <div class="flash">{{ message }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}

    <div class="card">
        <p><strong>Cliente:</strong> {{ row['apellido'] }}, {{ row['nombre'] or '-' }}</p>
        <p><strong>Pedido:</strong> {{ row['numero_pedido'] or '-' }}</p>
        <p><strong>Dirección:</strong> {{ row['direccion_completa'] or row['calle'] }}</p>
        <p><strong>Estado actual:</strong> {{ row['estado'] or '-' }}</p>
    </div>

    <form method="post" id="formChofer">
        <label>Estado</label>
        <select name="estado">
            <option value="En camino" {% if row['estado'] == 'En camino' %}selected{% endif %}>En camino</option>
            <option value="Entregado" {% if row['estado'] == 'Entregado' %}selected{% endif %}>Entregado</option>
            <option value="No entregado" {% if row['estado'] == 'No entregado' %}selected{% endif %}>No entregado</option>
            <option value="Reprogramado" {% if row['estado'] == 'Reprogramado' %}selected{% endif %}>Reprogramado</option>
        </select>

        <label>Observaciones</label>
        <textarea name="observaciones" rows="4">{{ row['observaciones'] or '' }}</textarea>

        <input type="hidden" name="chofer_latitud" id="chofer_latitud">
        <input type="hidden" name="chofer_longitud" id="chofer_longitud">

        <div class="actions">
            <button class="btn btn-success" type="submit">Guardar</button>
            <button class="btn btn-secondary" type="button" onclick="capturarUbicacion()">Actualizar ubicación</button>
        </div>
    </form>

    <div class="actions">
        <form method="post" style="display:inline;">
            <input type="hidden" name="estado" value="Entregado">
            <input type="hidden" name="observaciones" value="{{ row['observaciones'] or '' }}">
            <input type="hidden" name="chofer_latitud" id="entrega_lat">
            <input type="hidden" name="chofer_longitud" id="entrega_lon">
            <button class="btn btn-success" type="submit" onclick="llenarEntregaCoords()">Marcar entregado</button>
        </form>

        <form method="post" style="display:inline;">
            <input type="hidden" name="estado" value="No entregado">
            <input type="hidden" name="observaciones" value="Cliente ausente">
            <button class="btn btn-danger" type="submit">No entregado</button>
        </form>
    </div>

    <script>
        function capturarUbicacion() {
            if (!navigator.geolocation) {
                alert('Tu navegador no permite GPS');
                return;
            }
            navigator.geolocation.getCurrentPosition(function(pos) {
                document.getElementById('chofer_latitud').value = pos.coords.latitude;
                document.getElementById('chofer_longitud').value = pos.coords.longitude;
                alert('Ubicación cargada');
            }, function() {
                alert('No se pudo obtener la ubicación');
            });
        }

        function llenarEntregaCoords() {
            if (!navigator.geolocation) {
                return;
            }
            navigator.geolocation.getCurrentPosition(function(pos) {
                document.getElementById('entrega_lat').value = pos.coords.latitude;
                document.getElementById('entrega_lon').value = pos.coords.longitude;
            });
        }
    </script>
    """
    return page("Panel chofer", body, row=row)


# =========================
# Envío de link
# =========================
@app.route("/admin/enviar_link/<int:id>", methods=["POST"])
def enviar_link_cliente(id: int):
    acceso = admin_required()
    if acceso:
        return acceso

    db = get_db()
    row = db.execute("SELECT * FROM repartos WHERE id = ?", (id,)).fetchone()

    if not row:
        flash("Reparto no encontrado.")
        return redirect(url_for("admin"))

    if not row["telefono_cliente"]:
        flash("El cliente no tiene teléfono cargado.")
        return redirect(url_for("admin", fecha=request.args.get("fecha", ""), chofer=request.args.get("chofer", ""), estado=request.args.get("estado", "")))

    mensaje = construir_mensaje_cliente(row)
    enviado, detalle = enviar_whatsapp_automatico(row["telefono_cliente"], mensaje)

    if enviado:
        db.execute(
            "UPDATE repartos SET ultimo_envio = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), id),
        )
        db.commit()
        flash("Link enviado automáticamente por WhatsApp.")
    else:
        flash(f"No se pudo enviar automático. {detalle}")

    return redirect(url_for("admin", fecha=request.args.get("fecha", ""), chofer=request.args.get("chofer", ""), estado=request.args.get("estado", "")))


# =========================
# Importaciones
# =========================
@app.route("/admin/importar_excel", methods=["POST"])
def importar_excel():
    acceso = admin_required()
    if acceso:
        return acceso

    archivo = request.files.get("archivo_excel")
    if not archivo or not archivo.filename:
        flash("Seleccioná un archivo Excel o CSV.")
        return redirect(url_for("admin"))

    destino = UPLOAD_DIR / archivo.filename
    archivo.save(destino)

    try:
        mensaje = importar_desde_archivo(destino)
    except Exception as exc:
        flash(f"No se pudo leer el archivo: {exc}")
        return redirect(url_for("admin"))

    flash(mensaje)
    return redirect(url_for("admin"))


@app.route("/admin/importar_google_sheets", methods=["POST"])
def importar_google_sheets_manual():
    acceso = admin_required()
    if acceso:
        return acceso

    try:
        mensaje = importar_desde_google_sheets()
        flash(mensaje)
    except Exception as exc:
        flash(f"Error al importar Google Sheets: {exc}")

    return redirect(url_for("admin"))


# =========================
# Optimizar ruta
# =========================
@app.route("/admin/optimizar_ruta", methods=["POST"])
def optimizar_ruta():
    acceso = admin_required()
    if acceso:
        return acceso

    fecha = normalizar_texto(request.form.get("fecha")) or date.today().isoformat()
    chofer = normalizar_texto(request.form.get("chofer"))
    if not chofer:
        flash("Elegí un chofer.")
        return redirect(url_for("admin", fecha=fecha))

    db = get_db()
    rows = db.execute(
        """
        SELECT * FROM repartos
        WHERE fecha_reparto = ? AND chofer_nombre = ?
        ORDER BY orden_ruta ASC, id ASC
        """,
        (fecha, chofer),
    ).fetchall()

    optimizadas = optimizar_ruta_simple(list(rows))
    for idx, row in enumerate(optimizadas, start=1):
        db.execute("UPDATE repartos SET orden_ruta = ? WHERE id = ?", (idx, row["id"]))
    db.commit()
    flash("Ruta optimizada automáticamente.")
    return redirect(url_for("admin", fecha=fecha, chofer=chofer))


# =========================
# Editar reparto
# =========================
@app.route("/admin/actualizar_reparto/<int:id>", methods=["POST"])
def actualizar_reparto(id: int):
    acceso = admin_required()
    if acceso:
        return acceso

    db = get_db()

    estado = normalizar_texto(request.form.get("estado"))
    va_hoy = 1 if request.form.get("va_hoy") == "on" else 0
    franja = normalizar_texto(request.form.get("franja_horaria"))
    chofer = normalizar_texto(request.form.get("chofer_nombre"))
    telefono_chofer = normalizar_texto(request.form.get("chofer_telefono"))
    observaciones = normalizar_texto(request.form.get("observaciones"))
    telefono_cliente = normalizar_texto(request.form.get("telefono_cliente"))
    latitud = normalizar_texto(request.form.get("latitud"))
    longitud = normalizar_texto(request.form.get("longitud"))
    orden_ruta = normalizar_texto(request.form.get("orden_ruta"))

    db.execute(
        """
        UPDATE repartos
        SET estado = ?,
            va_hoy = ?,
            franja_horaria = ?,
            chofer_nombre = ?,
            chofer_telefono = ?,
            observaciones = ?,
            telefono_cliente = ?,
            latitud = ?,
            longitud = ?,
            orden_ruta = ?
        WHERE id = ?
        """,
        (
            estado,
            va_hoy,
            franja,
            chofer,
            telefono_chofer,
            observaciones,
            telefono_cliente,
            float(latitud) if latitud else None,
            float(longitud) if longitud else None,
            int(orden_ruta) if orden_ruta else 0,
            id,
        ),
    )
    db.commit()

    row = db.execute("SELECT * FROM repartos WHERE id = ?", (id,)).fetchone()
    aviso = notificar_si_en_camino(row)
    if aviso:
        flash(aviso)

    flash("Reparto actualizado correctamente.")
    return redirect(url_for("admin", fecha=request.args.get("fecha", ""), chofer=request.args.get("chofer", ""), estado=request.args.get("estado", "")))


# =========================
# Mapa chofer
# =========================
@app.route("/admin/mapa")
def mapa_chofer() -> str:
    acceso = admin_required()
    if acceso:
        return acceso

    fecha = normalizar_texto(request.args.get("fecha")) or date.today().isoformat()
    chofer = normalizar_texto(request.args.get("chofer"))

    db = get_db()
    if not chofer:
        flash("Elegí un chofer para ver el mapa.")
        return redirect(url_for("admin", fecha=fecha))

    rows = db.execute(
        """
        SELECT *
        FROM repartos
        WHERE fecha_reparto = ?
          AND chofer_nombre = ?
          AND latitud IS NOT NULL
          AND longitud IS NOT NULL
        ORDER BY orden_ruta ASC, id ASC
        """,
        (fecha, chofer),
    ).fetchall()

    puntos = [{"lat": row["latitud"], "lon": row["longitud"]} for row in rows]
    ruta = obtener_ruta_osrm(puntos)

    body = """
    <div class="topbar">
        <div class="brand">
            <img src="{{ url_for('static', filename='logo.png') }}" alt="Logo empresa">
            <div>
                <h1>Mapa de ruta</h1>
                <p>Chofer: <strong>{{ chofer }}</strong> | Fecha: <strong>{{ fecha }}</strong></p>
            </div>
        </div>
        <a class="btn btn-secondary" href="{{ url_for('admin', fecha=fecha, chofer=chofer) }}">Volver al panel</a>
    </div>

    {% if not rows %}
        <div class="card">
            <p>No hay puntos con latitud/longitud para este chofer y fecha.</p>
        </div>
    {% else %}
        <div id="map"></div>

        <script>
            const stops = {{ stops|safe }};
            const routeCoords = {{ route_coords|safe }};

            const map = L.map('map').setView([stops[0].lat, stops[0].lon], 12);

            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '&copy; OpenStreetMap'
            }).addTo(map);

            stops.forEach((stop, index) => {
                L.marker([stop.lat, stop.lon]).addTo(map)
                    .bindPopup(
                        '<b>Parada ' + (index + 1) + '</b><br>' +
                        stop.cliente + '<br>' +
                        (stop.direccion || '') + '<br>' +
                        'Pedido: ' + (stop.pedido || '-')
                    );
            });

            const poly = L.polyline(routeCoords, {weight: 5}).addTo(map);
            map.fitBounds(poly.getBounds());
        </script>
    {% endif %}
    """

    stops = [
        {
            "lat": row["latitud"],
            "lon": row["longitud"],
            "cliente": f"{row['apellido']}, {row['nombre'] or ''}",
            "direccion": row["direccion_completa"] or row["calle"],
            "pedido": row["numero_pedido"] or "",
        }
        for row in rows
    ]

    return page(
        "Mapa de ruta",
        body,
        rows=rows,
        chofer=chofer,
        fecha=fecha,
        stops=json.dumps(stops),
        route_coords=json.dumps(ruta),
    )


# =========================
# Panel admin
# =========================
@app.route("/admin", methods=["GET", "POST"])
def admin() -> str:
    acceso = admin_required()
    if acceso:
        return acceso

    db = get_db()

    try:
        mensaje_auto = auto_importar_archivo_si_existe()
        if mensaje_auto:
            flash(mensaje_auto)
    except Exception as exc:
        flash(f"Error en auto importación: {exc}")

    try:
        if GOOGLE_SHEET_URL:
            mensaje_gs = importar_desde_google_sheets()
            if "Se importaron" in mensaje_gs:
                flash(mensaje_gs)
    except Exception as exc:
        flash(f"Error en Google Sheets: {exc}")

    hoy = date.today().isoformat()
    fecha_filtro = normalizar_texto(request.args.get("fecha")) or hoy
    chofer_filtro = normalizar_texto(request.args.get("chofer"))
    estado_filtro = normalizar_texto(request.args.get("estado"))

    if request.method == "POST":
        token = generar_token()
        datos = (
            normalizar_texto(request.form.get("apellido")),
            normalizar_texto(request.form.get("nombre")),
            normalizar_texto(request.form.get("calle")),
            normalizar_texto(request.form.get("direccion_completa")),
            normalizar_texto(request.form.get("numero_pedido")),
            normalizar_texto(request.form.get("telefono_cliente")),
            normalizar_texto(request.form.get("email_cliente")),
            normalizar_texto(request.form.get("fecha_reparto")),
            1 if request.form.get("va_hoy") == "on" else 0,
            normalizar_texto(request.form.get("franja_horaria")),
            normalizar_texto(request.form.get("chofer_nombre")),
            normalizar_texto(request.form.get("chofer_telefono")),
            normalizar_texto(request.form.get("estado")),
            normalizar_texto(request.form.get("observaciones")),
            request.form.get("latitud") or None,
            request.form.get("longitud") or None,
            int(request.form.get("orden_ruta") or 0),
            token,
        )

        if not datos[0] or not datos[2] or not datos[7]:
            flash("Apellido, calle y fecha de reparto son obligatorios.")
            return redirect(url_for("admin", fecha=fecha_filtro, chofer=chofer_filtro, estado=estado_filtro))

        db.execute(
            """
            INSERT INTO repartos (
                apellido, nombre, calle, direccion_completa, numero_pedido,
                telefono_cliente, email_cliente, fecha_reparto, va_hoy,
                franja_horaria, chofer_nombre, chofer_telefono, estado,
                observaciones, latitud, longitud, orden_ruta, token_seguimiento
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            datos,
        )
        db.commit()
        flash("Reparto guardado correctamente.")
        return redirect(url_for("admin", fecha=fecha_filtro, chofer=chofer_filtro, estado=estado_filtro))

    sql = "SELECT * FROM repartos WHERE fecha_reparto = ?"
    params: List[Any] = [fecha_filtro]

    if chofer_filtro:
        sql += " AND chofer_nombre = ?"
        params.append(chofer_filtro)

    if estado_filtro:
        sql += " AND estado = ?"
        params.append(estado_filtro)

    sql += " ORDER BY chofer_nombre ASC, orden_ruta ASC, apellido ASC"
    repartos = db.execute(sql, params).fetchall()

    choferes = db.execute(
        "SELECT DISTINCT chofer_nombre FROM repartos WHERE chofer_nombre IS NOT NULL AND chofer_nombre != '' ORDER BY chofer_nombre ASC"
    ).fetchall()

    estados = ["Pendiente", "En preparación", "En camino", "Entregado", "No entregado", "Reprogramado"]

    body = """
    <div class="topbar">
        <div class="brand">
            <img src="{{ url_for('static', filename='logo.png') }}" alt="Logo empresa">
            <div>
                <h1>Panel interno</h1>
                <p>Cargá, filtrá y editá repartos.</p>
            </div>
        </div>
        <div class="actions">
            <a class="btn btn-secondary" href="{{ url_for('admin_logout') }}">Salir</a>
            {% if chofer_filtro %}
                <a class="btn btn-secondary" href="{{ url_for('mapa_chofer', fecha=fecha_filtro, chofer=chofer_filtro) }}">Ver mapa del chofer</a>
            {% endif %}
        </div>
    </div>

    {% with messages = get_flashed_messages() %}
      {% if messages %}
        {% for message in messages %}
          <div class="flash">{{ message }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}

    <div class="card">
        <h2>Carga automática del día</h2>
        <p>Si querés importar automáticamente, dejá un archivo llamado <code>repartos_hoy.xlsx</code>, <code>repartos_hoy.xls</code> o <code>repartos_hoy.csv</code> dentro de la carpeta <code>auto_import</code>.</p>
    </div>

    <div class="card">
        <h2>Cargar reparto manual</h2>
        <form method="post">
            <div class="grid-3">
                <div>
                    <label>Apellido *</label>
                    <input name="apellido" required>
                </div>
                <div>
                    <label>Nombre</label>
                    <input name="nombre">
                </div>
                <div>
                    <label>Pedido</label>
                    <input name="numero_pedido">
                </div>
            </div>

            <div class="grid-3">
                <div>
                    <label>Calle *</label>
                    <input name="calle" required>
                </div>
                <div>
                    <label>Dirección completa</label>
                    <input name="direccion_completa">
                </div>
                <div>
                    <label>Teléfono cliente</label>
                    <input name="telefono_cliente" placeholder="54911...">
                </div>
            </div>

            <div class="grid-3">
                <div>
                    <label>Email cliente</label>
                    <input name="email_cliente">
                </div>
                <div>
                    <label>Fecha de reparto *</label>
                    <input type="date" name="fecha_reparto" value="{{ fecha_filtro }}" required>
                </div>
                <div>
                    <label>Franja horaria</label>
                    <input name="franja_horaria">
                </div>
            </div>

            <div class="grid-3">
                <div>
                    <label>Chofer</label>
                    <input name="chofer_nombre">
                </div>
                <div>
                    <label>Teléfono chofer</label>
                    <input name="chofer_telefono">
                </div>
                <div>
                    <label>Estado</label>
                    <select name="estado">
                        {% for estado in estados %}
                            <option>{{ estado }}</option>
                        {% endfor %}
                    </select>
                </div>
            </div>

            <div class="grid-3">
                <div>
                    <label>Orden ruta</label>
                    <input name="orden_ruta" type="number" min="0" value="0">
                </div>
                <div>
                    <label>Latitud</label>
                    <input name="latitud">
                </div>
                <div>
                    <label>Longitud</label>
                    <input name="longitud">
                </div>
            </div>

            <div>
                <label>¿Va hoy?</label><br>
                <input type="checkbox" name="va_hoy" checked>
            </div>

            <div>
                <label>Observaciones</label>
                <textarea name="observaciones" rows="3"></textarea>
            </div>

            <div>
                <button type="submit">Guardar reparto</button>
            </div>
        </form>
    </div>

    <div class="card">
        <h2>Importar desde Excel o CSV</h2>
        <p>Columnas sugeridas: <code>apellido, nombre, calle, direccion_completa, numero_pedido, telefono_cliente, email_cliente, fecha_reparto, va_hoy, franja_horaria, chofer_nombre, chofer_telefono, estado, observaciones, latitud, longitud, orden_ruta</code></p>
        <form method="post" action="{{ url_for('importar_excel') }}" enctype="multipart/form-data">
            <input type="file" name="archivo_excel" accept=".xlsx,.xls,.csv" required>
            <button type="submit">Importar archivo</button>
        </form>
    </div>

    <div class="card">
        <h2>Importar desde Google Sheets</h2>
        <p>Trae repartos desde una planilla publicada como CSV.</p>
        <form method="post" action="{{ url_for('importar_google_sheets_manual') }}">
            <button type="submit">Importar ahora</button>
        </form>
    </div>

    <div class="card">
        <h2>Filtros</h2>
        <form method="get">
            <div class="grid-3">
                <div>
                    <label>Fecha</label>
                    <input type="date" name="fecha" value="{{ fecha_filtro }}">
                </div>
                <div>
                    <label>Chofer</label>
                    <select name="chofer">
                        <option value="">Todos</option>
                        {% for ch in choferes %}
                            <option value="{{ ch['chofer_nombre'] }}" {% if chofer_filtro == ch['chofer_nombre'] %}selected{% endif %}>{{ ch['chofer_nombre'] }}</option>
                        {% endfor %}
                    </select>
                </div>
                <div>
                    <label>Estado</label>
                    <select name="estado">
                        <option value="">Todos</option>
                        {% for item in estados %}
                            <option value="{{ item }}" {% if estado_filtro == item %}selected{% endif %}>{{ item }}</option>
                        {% endfor %}
                    </select>
                </div>
            </div>
            <div class="actions">
                <button type="submit">Filtrar</button>
                <a class="btn btn-secondary" href="{{ url_for('admin') }}">Limpiar</a>
            </div>
        </form>

        {% if chofer_filtro %}
        <form method="post" action="{{ url_for('optimizar_ruta') }}">
            <input type="hidden" name="fecha" value="{{ fecha_filtro }}">
            <input type="hidden" name="chofer" value="{{ chofer_filtro }}">
            <button class="btn btn-warning" type="submit">Optimizar ruta</button>
        </form>
        {% endif %}
    </div>

    <h2>Repartos cargados</h2>
    <table>
        <thead>
            <tr>
                <th>Fecha</th>
                <th>Cliente</th>
                <th>Pedido</th>
                <th>Editar reparto</th>
            </tr>
        </thead>
        <tbody>
        {% for row in repartos %}
            <tr>
                <td>{{ row['fecha_reparto'] }}</td>
                <td>
                    <strong>{{ row['apellido'] }}, {{ row['nombre'] or '-' }}</strong><br>
                    {{ row['direccion_completa'] or row['calle'] }}
                </td>
                <td>{{ row['numero_pedido'] or '-' }}</td>
                <td>
                    <form method="post" action="{{ url_for('actualizar_reparto', id=row['id'], fecha=fecha_filtro, chofer=chofer_filtro, estado=estado_filtro) }}">
                        <div class="mini-form">
                            <label>Estado</label>
                            <select name="estado">
                                {% for item in estados %}
                                    <option value="{{ item }}" {% if row['estado'] == item %}selected{% endif %}>{{ item }}</option>
                                {% endfor %}
                            </select>

                            <label>Franja horaria</label>
                            <input name="franja_horaria" value="{{ row['franja_horaria'] or '' }}">

                            <label>Chofer</label>
                            <input name="chofer_nombre" value="{{ row['chofer_nombre'] or '' }}">

                            <label>Teléfono chofer</label>
                            <input name="chofer_telefono" value="{{ row['chofer_telefono'] or '' }}">

                            <label>Teléfono cliente</label>
                            <input name="telefono_cliente" value="{{ row['telefono_cliente'] or '' }}">

                            <label>Orden ruta</label>
                            <input name="orden_ruta" type="number" min="0" value="{{ row['orden_ruta'] or 0 }}">

                            <div class="grid-2">
                                <div>
                                    <label>Latitud</label>
                                    <input name="latitud" value="{{ row['latitud'] or '' }}">
                                </div>
                                <div>
                                    <label>Longitud</label>
                                    <input name="longitud" value="{{ row['longitud'] or '' }}">
                                </div>
                            </div>

                            <label style="display:flex; gap:8px; align-items:center;">
                                <input type="checkbox" name="va_hoy" {% if row['va_hoy'] %}checked{% endif %}>
                                ¿Va hoy?
                            </label>

                            <label>Observación</label>
                            <textarea name="observaciones" rows="3">{{ row['observaciones'] or '' }}</textarea>

                            <div class="actions">
                                <button type="submit">Guardar cambios</button>
                                <a class="btn btn-secondary" href="{{ url_for('seguimiento_cliente', token=row['token_seguimiento']) }}" target="_blank">Ver seguimiento</a>
                                <a class="btn btn-warning" href="{{ url_for('vista_chofer', token=row['token_seguimiento']) }}" target="_blank">Vista chofer</a>

                                {% if row['telefono_cliente'] %}
                                    <a class="btn btn-success"
                                       href="{{ whatsapp_deep_link(row['telefono_cliente'], construir_mensaje_cliente(row)) }}"
                                       target="_blank">
                                       WhatsApp
                                    </a>

                                    <button class="btn btn-success"
                                            formaction="{{ url_for('enviar_link_cliente', id=row['id'], fecha=fecha_filtro, chofer=chofer_filtro, estado=estado_filtro) }}"
                                            formmethod="post"
                                            type="submit">
                                        Envío automático
                                    </button>
                                {% endif %}
                            </div>

                            <small class="small">
                                Seguimiento: {{ tracking_url(row['token_seguimiento']) }}<br>
                                Chofer: {{ chofer_url(row['token_seguimiento']) }}<br>
                                Último envío: {{ row['ultimo_envio'] or 'Nunca' }}<br>
                                GPS chofer: {{ row['chofer_ultima_actualizacion'] or 'Sin datos' }}
                            </small>
                        </div>
                    </form>
                </td>
            </tr>
        {% endfor %}
        </tbody>
    </table>
    """

    return page(
        "Panel interno",
        body,
        repartos=repartos,
        fecha_filtro=fecha_filtro,
        chofer_filtro=chofer_filtro,
        estado_filtro=estado_filtro,
        choferes=choferes,
        estados=estados,
        whatsapp_deep_link=whatsapp_deep_link,
        construir_mensaje_cliente=construir_mensaje_cliente,
        tracking_url=tracking_url,
        chofer_url=chofer_url,
    )


if __name__ == "__main__":
    init_db()
    print("Base inicializada en:", DB_PATH)
    print("Abrí en tu navegador:", PUBLIC_BASE_URL)
    app.run(debug=True)