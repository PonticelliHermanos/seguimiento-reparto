from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Optional

from flask import Flask, g, redirect, render_template_string, request, url_for, flash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "repartos.db"

app = Flask(__name__)
app.secret_key = "cambiar-por-una-clave-segura"


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


def init_db() -> None:
    schema = """
    CREATE TABLE IF NOT EXISTS repartos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        apellido TEXT NOT NULL,
        nombre TEXT,
        calle TEXT NOT NULL,
        numero_pedido TEXT,
        fecha_reparto TEXT NOT NULL,
        va_hoy INTEGER NOT NULL DEFAULT 0,
        franja_horaria TEXT,
        chofer_nombre TEXT,
        chofer_telefono TEXT,
        estado TEXT DEFAULT 'Pendiente',
        observaciones TEXT
    );
    """

    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(schema)
        conn.commit()

        total = conn.execute("SELECT COUNT(*) FROM repartos").fetchone()[0]
        if total == 0:
            hoy = date.today().isoformat()
            ejemplos = [
                (
                    "García",
                    "Ana",
                    "San Martín",
                    "PED-1001",
                    hoy,
                    1,
                    "09:00 a 12:00",
                    "Carlos Gómez",
                    "11-5555-1234",
                    "En preparación",
                    "Llamar al llegar",
                ),
                (
                    "Pérez",
                    "Luis",
                    "Belgrano",
                    "PED-1002",
                    hoy,
                    1,
                    "14:00 a 17:00",
                    "Juan Díaz",
                    "11-4444-5678",
                    "Pendiente",
                    "",
                ),
                (
                    "López",
                    "María",
                    "Rivadavia",
                    "PED-1003",
                    hoy,
                    0,
                    "",
                    "",
                    "",
                    "Reprogramado",
                    "Entrega mañana",
                ),
            ]
            conn.executemany(
                """
                INSERT INTO repartos (
                    apellido, nombre, calle, numero_pedido, fecha_reparto, va_hoy,
                    franja_horaria, chofer_nombre, chofer_telefono, estado, observaciones
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ejemplos,
            )
            conn.commit()


# =========================
# Templates
# =========================
BASE_HTML = """
<!doctype html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ title }}</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background: #f5f6fa;
            margin: 0;
            padding: 0;
            color: #222;
        }
        .container {
            max-width: 1100px;
            margin: 30px auto;
            background: white;
            padding: 24px;
            border-radius: 12px;
            box-shadow: 0 6px 20px rgba(0,0,0,0.08);
        }
        h1, h2 { margin-top: 0; }
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
        .btn-secondary {
            background: #6c757d;
        }
        .grid-2 {
            display: grid;
            grid-template-columns: 1fr 1fr;
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
        .flash {
            padding: 10px;
            border-radius: 8px;
            background: #fff4d6;
            margin-bottom: 16px;
        }
        .mini-form {
            display: grid;
            gap: 8px;
            min-width: 280px;
        }
        @media (max-width: 700px) {
            .grid-2 { grid-template-columns: 1fr; }
            .topbar { flex-direction: column; align-items: stretch; }
        }
    </style>
</head>
<body>
    <div class="container">
        {{ body|safe }}
    </div>
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
# Vista cliente
# =========================
@app.route("/", methods=["GET", "POST"])
def consulta_cliente() -> str:
    resultado = None
    multiples = []

    if request.method == "POST":
        apellido = request.form.get("apellido", "").strip()
        calle = request.form.get("calle", "").strip()
        pedido = request.form.get("numero_pedido", "").strip()
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
            alt = db.execute(
                """
                SELECT * FROM repartos
                WHERE LOWER(apellido) = ?
                ORDER BY fecha_reparto DESC
                LIMIT 5
                """,
                (apellido.lower(),),
            ).fetchall()
            multiples = alt

    body = """
    <div class="topbar">
        <div>
            <h1>Consulta de reparto</h1>
            <p>El cliente puede consultar si su pedido va hoy y ver el teléfono del chofer.</p>
        </div>
        <a class="btn btn-secondary" href="{{ url_for('admin') }}">Panel interno</a>
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
# Actualizar reparto existente
# =========================
@app.route("/admin/actualizar_estado/<int:id>", methods=["POST"])
def actualizar_estado(id):
    db = get_db()

    nuevo_estado = request.form.get("estado", "").strip()
    va_hoy = 1 if request.form.get("va_hoy") == "on" else 0
    franja = request.form.get("franja_horaria", "").strip()
    chofer = request.form.get("chofer_nombre", "").strip()
    telefono = request.form.get("chofer_telefono", "").strip()
    observaciones = request.form.get("observaciones", "").strip()

    db.execute(
        """
        UPDATE repartos
        SET estado = ?,
            va_hoy = ?,
            franja_horaria = ?,
            chofer_nombre = ?,
            chofer_telefono = ?,
            observaciones = ?
        WHERE id = ?
        """,
        (nuevo_estado, va_hoy, franja, chofer, telefono, observaciones, id),
    )
    db.commit()
    flash("Reparto actualizado correctamente.")
    return redirect(url_for("admin", fecha=request.args.get("fecha", "")))


# =========================
# Panel interno
# =========================
@app.route("/admin", methods=["GET", "POST"])
def admin() -> str:
    db = get_db()
    fecha_filtro = request.args.get("fecha", "").strip()

    if request.method == "POST":
        datos = (
            request.form.get("apellido", "").strip(),
            request.form.get("nombre", "").strip(),
            request.form.get("calle", "").strip(),
            request.form.get("numero_pedido", "").strip(),
            request.form.get("fecha_reparto", "").strip(),
            1 if request.form.get("va_hoy") == "on" else 0,
            request.form.get("franja_horaria", "").strip(),
            request.form.get("chofer_nombre", "").strip(),
            request.form.get("chofer_telefono", "").strip(),
            request.form.get("estado", "").strip(),
            request.form.get("observaciones", "").strip(),
        )

        if not datos[0] or not datos[2] or not datos[4]:
            flash("Apellido, calle y fecha de reparto son obligatorios.")
            return redirect(url_for("admin"))

        db.execute(
            """
            INSERT INTO repartos (
                apellido, nombre, calle, numero_pedido, fecha_reparto, va_hoy,
                franja_horaria, chofer_nombre, chofer_telefono, estado, observaciones
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            datos,
        )
        db.commit()
        flash("Reparto guardado correctamente.")
        return redirect(url_for("admin"))

    if fecha_filtro:
        repartos = db.execute(
            "SELECT * FROM repartos WHERE fecha_reparto = ? ORDER BY apellido ASC",
            (fecha_filtro,),
        ).fetchall()
    else:
        repartos = db.execute(
            "SELECT * FROM repartos ORDER BY fecha_reparto DESC, apellido ASC"
        ).fetchall()

    body = """
    <div class="topbar">
        <div>
            <h1>Panel interno</h1>
            <p>Cargá y editá repartos para que los clientes consulten su entrega.</p>
        </div>
        <a class="btn btn-secondary" href="{{ url_for('consulta_cliente') }}">Volver a consulta cliente</a>
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
                <label>Apellido *</label>
                <input name="apellido" required>
            </div>
            <div>
                <label>Nombre</label>
                <input name="nombre">
            </div>
        </div>

        <div class="grid-2">
            <div>
                <label>Calle *</label>
                <input name="calle" required>
            </div>
            <div>
                <label>Número de pedido</label>
                <input name="numero_pedido">
            </div>
        </div>

        <div class="grid-2">
            <div>
                <label>Fecha de reparto *</label>
                <input type="date" name="fecha_reparto" value="{{ hoy }}" required>
            </div>
            <div>
                <label>Franja horaria</label>
                <input name="franja_horaria" placeholder="Ej: 14:00 a 17:00">
            </div>
        </div>

        <div class="grid-2">
            <div>
                <label>Chofer</label>
                <input name="chofer_nombre">
            </div>
            <div>
                <label>Teléfono del chofer</label>
                <input name="chofer_telefono">
            </div>
        </div>

        <div class="grid-2">
            <div>
                <label>Estado</label>
                <select name="estado">
                    <option>Pendiente</option>
                    <option>En preparación</option>
                    <option>En camino</option>
                    <option>Entregado</option>
                    <option>No entregado</option>
                    <option>Reprogramado</option>
                </select>
            </div>
            <div>
                <label>¿Va hoy?</label><br>
                <input type="checkbox" name="va_hoy" checked>
            </div>
        </div>

        <div>
            <label>Observaciones</label>
            <textarea name="observaciones" rows="3"></textarea>
        </div>

        <div>
            <button type="submit">Guardar reparto</button>
        </div>
    </form>

    <h2>Filtrar repartos por fecha</h2>
    <form method="get" style="margin-bottom:20px;">
        <div class="grid-2">
            <div>
                <label>Fecha</label>
                <input type="date" name="fecha" value="{{ fecha_filtro }}">
            </div>
            <div style="display:flex; align-items:end; gap:10px;">
                <button type="submit">Filtrar</button>
                <a class="btn btn-secondary" href="{{ url_for('admin') }}">Limpiar</a>
            </div>
        </div>
    </form>

    <h2>Repartos cargados</h2>
    <table>
        <thead>
            <tr>
                <th>Fecha</th>
                <th>Cliente</th>
                <th>Calle</th>
                <th>Pedido</th>
                <th>Editar reparto</th>
            </tr>
        </thead>
        <tbody>
        {% for row in repartos %}
            <tr>
                <td>{{ row['fecha_reparto'] }}</td>
                <td>{{ row['apellido'] }}, {{ row['nombre'] or '-' }}</td>
                <td>{{ row['calle'] }}</td>
                <td>{{ row['numero_pedido'] or '-' }}</td>
                <td>
                    <form method="post" action="{{ url_for('actualizar_estado', id=row['id'], fecha=fecha_filtro) }}">
                        <div class="mini-form">
                            <label>Estado</label>
                            <select name="estado">
                                <option value="Pendiente" {% if row['estado'] == 'Pendiente' %}selected{% endif %}>Pendiente</option>
                                <option value="En preparación" {% if row['estado'] == 'En preparación' %}selected{% endif %}>En preparación</option>
                                <option value="En camino" {% if row['estado'] == 'En camino' %}selected{% endif %}>En camino</option>
                                <option value="Entregado" {% if row['estado'] == 'Entregado' %}selected{% endif %}>Entregado</option>
                                <option value="No entregado" {% if row['estado'] == 'No entregado' %}selected{% endif %}>No entregado</option>
                                <option value="Reprogramado" {% if row['estado'] == 'Reprogramado' %}selected{% endif %}>Reprogramado</option>
                            </select>

                            <label>Franja horaria</label>
                            <input name="franja_horaria" value="{{ row['franja_horaria'] or '' }}">

                            <label>Chofer</label>
                            <input name="chofer_nombre" value="{{ row['chofer_nombre'] or '' }}">

                            <label>Teléfono</label>
                            <input name="chofer_telefono" value="{{ row['chofer_telefono'] or '' }}">

                            <label style="display:flex; gap:8px; align-items:center;">
                                <input type="checkbox" name="va_hoy" {% if row['va_hoy'] %}checked{% endif %}>
                                ¿Va hoy?
                            </label>

                            <label>Observación</label>
                            <textarea name="observaciones" rows="3">{{ row['observaciones'] or '' }}</textarea>

                            <button type="submit">Guardar cambios</button>
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
        hoy=date.today().isoformat(),
        fecha_filtro=fecha_filtro,
    )


if __name__ == "__main__":
    init_db()
    print("Base inicializada en:", DB_PATH)
    print("Abrí en tu navegador: http://127.0.0.1:5000")
    app.run(debug=True)