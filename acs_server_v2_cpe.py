import os
import sqlite3
import datetime
import json
from flask import Flask, request, jsonify, render_template_string, Response, redirect
import requests
from requests.auth import HTTPDigestAuth

app = Flask(__name__)

# Ubicación de la base de datos compartida con el ACS principal
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "acs_database.db")
FW_DIR = "/var/www/html/fw"


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except Exception:
        pass
    return conn


def trigger_connection_request(serial, conn_url):
    """Notifica al CPE directamente si se requiere un Connection Request manual."""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT conn_req_user, conn_req_pass FROM default_config WHERE id=1")
        cfg = cursor.fetchone()
        conn.close()

        user = cfg['conn_req_user'] if cfg else 'sagemcom'
        password = cfg['conn_req_pass'] if cfg else 'sagemcom'

        requests.get(conn_url, auth=HTTPDigestAuth(user, password), timeout=4)
    except Exception:
        pass


# --- RUTAS DE API (LECTURA/ESCRITURA SOBRE LA BD COMPARTIDA) ---

@app.route('/api/devices', methods=['GET'])
def get_devices():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT serial_number, model_name, software_version, ip_address, ip_data, status, last_inform FROM devices ORDER BY last_inform DESC")
    rows = cursor.fetchall()
    devices = [dict(row) for row in rows]
    for d in devices:
        d['is_online'] = (d['status'] == 'Online')
        d['status_badge'] = "Online" if d['is_online'] else "Offline"
    conn.close()
    return jsonify(devices)


@app.route('/api/device/details/<serial>', methods=['GET'])
def get_device_details(serial):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM devices WHERE serial_number=?", (serial,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return jsonify({"status": "error", "message": "Dispositivo no encontrado."}), 404
    dev = dict(row)
    dev['is_online'] = (dev['status'] == 'Online')
    dev['status_badge'] = "Online" if dev['is_online'] else "Offline"
    return jsonify(dev)


@app.route('/api/execute', methods=['POST'])
def execute_rpc():
    serial = request.form.get('serialseleccionado', '').strip()
    rpc = request.form.get('seleccion', '').strip()
    param_name = request.form.get('parametro', '').strip()
    param_val = request.form.get('valor', '').strip()
    param_type = request.form.get('tipo', 'string').strip()

    if not serial or not rpc:
        return jsonify({"status": "error", "message": "Faltan datos requeridos."}), 400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO pending_commands (serial_number, rpc_method, parameter_name, parameter_value, parameter_type, retry_count) VALUES (?, ?, ?, ?, ?, 1)",
        (serial, rpc, param_name, param_val, param_type)
    )
    conn.commit()

    cursor.execute("SELECT connection_request_url FROM devices WHERE serial_number=?", (serial,))
    dev = cursor.fetchone()
    conn.close()

    if dev and dev['connection_request_url']:
        trigger_connection_request(serial, dev['connection_request_url'])

    return jsonify({"status": "success", "message": f"Comando {rpc} encolado para {serial}."})


@app.route('/api/pending_commands', methods=['GET'])
def get_pending_commands():
    serial = request.args.get('serial', '').strip()
    conn = get_db()
    cursor = conn.cursor()
    if serial:
        cursor.execute(
            "SELECT id, serial_number, rpc_method, parameter_name, parameter_value, created_at FROM pending_commands WHERE status='PENDING' AND serial_number=? ORDER BY id ASC",
            (serial,))
    else:
        cursor.execute(
            "SELECT id, serial_number, rpc_method, parameter_name, parameter_value, created_at FROM pending_commands WHERE status='PENDING' ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])


@app.route('/api/pending_commands/cancel/<int:cmd_id>', methods=['POST'])
def cancel_command(cmd_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM pending_commands WHERE id=? AND status='PENDING'", (cmd_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})


@app.route('/api/logs/stream')
def stream_logs():
    target_serial = request.args.get('serial', '').strip()
    conn = get_db()
    cursor = conn.cursor()
    if target_serial:
        cursor.execute(
            "SELECT timestamp, serial_number as serial, rpc_method as rpc, status, log_detail as detail FROM execution_logs WHERE serial_number=? ORDER BY id DESC LIMIT 20",
            (target_serial,))
    else:
        cursor.execute(
            "SELECT timestamp, serial_number as serial, rpc_method as rpc, status, log_detail as detail FROM execution_logs ORDER BY id DESC LIMIT 20")
    rows = cursor.fetchall()[::-1]
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/logs/download', methods=['GET'])
def download_logs():
    target_serial = request.args.get('serial', '').strip()
    conn = get_db()
    cursor = conn.cursor()
    if target_serial:
        cursor.execute(
            "SELECT timestamp, serial_number, rpc_method, status, log_detail FROM execution_logs WHERE serial_number=? ORDER BY id ASC",
            (target_serial,))
    else:
        cursor.execute(
            "SELECT timestamp, serial_number, rpc_method, status, log_detail FROM execution_logs ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    text = "".join([
                       f"[{r['timestamp']}] [{r['serial_number']}] [{r['rpc_method']}] [{r['status']}]\n{r['log_detail']}\n" + "-" * 80 + "\n"
                       for r in rows])
    return Response(text, mimetype="text/plain",
                    headers={"Content-Disposition": f"attachment;filename=logs_{target_serial or 'all'}.txt"})


@app.route('/api/logs/clear', methods=['POST'])
def clear_logs():
    target_serial = request.args.get('serial', '').strip()
    conn = get_db()
    cursor = conn.cursor()
    if target_serial:
        cursor.execute("DELETE FROM execution_logs WHERE serial_number=?", (target_serial,))
    else:
        cursor.execute("DELETE FROM execution_logs")
    conn.commit()
    conn.close()
    return jsonify({"status": "success", "message": "Logs eliminados correctamente."})


# --- PLANTILLA HTML (VISTA INDIVIDUAL POR SELECCIÓN CON CLICK) ---

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Consola Individual CPE TR-069</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <style>
        body { background-color: #0f172a; color: #f8fafc; font-family: system-ui, -apple-system, sans-serif; }
        .acs-header { background-color: #1e293b; padding: 16px 24px; border-radius: 8px; border-bottom: 2px solid #38bdf8; }
        .card { background-color: #1e293b; border: 1px solid #334155; color: #f8fafc; }
        #terminal { background-color: #020617; color: #10b981; font-family: 'Consolas', monospace; padding: 15px; height: 500px; overflow-y: auto; border-radius: 6px; font-size: 0.85rem; }
        .log-header { color: #38bdf8; font-weight: bold; margin-top: 10px; }
        .xml-box { background-color: #0f172a; color: #ffffff; padding: 10px; border-left: 3px solid #3b82f6; margin: 6px 0; white-space: pre-wrap; word-break: break-all; font-family: monospace; }
        .xml-box-in { border-left-color: #10b981; }
        .xml-box-out { border-left-color: #f59e0b; }
        .device-card { cursor: pointer; transition: all 0.2s; background: #1e293b; border: 1px solid #334155; }
        .device-card:hover { transform: translateY(-3px); border-color: #38bdf8; }
        .param-row { background: #0f172a; border: 1px dashed #475569; padding: 8px; border-radius: 6px; }
        .form-control, .form-select { background-color: #0f172a; border-color: #475569; color: #ffffff; }
        .form-control:focus, .form-select:focus { background-color: #020617; color: #ffffff; border-color: #38bdf8; }
    </style>
</head>
<body>
<div class="container-fluid py-4 px-4">
    <header class="acs-header d-flex justify-content-between align-items-center mb-4">
        <div>
            <h1 class="h4 mb-0 fw-bold text-info">🖥️ Consola Individual por Equipo TR-069</h1>
            <small class="text-muted">Servidor ACS Principal ejecutándose en puerto 9034</small>
        </div>
        <div>
            <a href="/cpe" class="btn btn-sm btn-outline-light">📋 Lista General de Equipos</a>
        </div>
    </header>

    {% if not target_serial %}
    <!-- SELECCIÓN DE EQUIPO REPORTADO -->
    <div class="row g-4">
        <div class="col-12">
            <div class="card p-4">
                <h4 class="fw-bold text-info mb-3">Toca un equipo para abrir su consola individual:</h4>
                <div class="row g-3" id="devicesGrid">
                    <div class="col-12 text-center py-5">Cargando equipos...</div>
                </div>
            </div>
        </div>
    </div>
    {% else %}
    <!-- CONSOLA EXCLUSIVA PARA EL EQUIPO SELECCIONADO -->
    <div class="row g-3 mb-3">
        <div class="col-12">
            <div class="card p-3 border-info d-flex flex-row justify-content-between align-items-center">
                <div>
                    <span class="badge bg-success fs-6 me-2" id="cpe_status">Online</span>
                    <span class="fs-5 fw-bold text-white me-3" id="cpe_model">Cargando...</span>
                    <span class="font-monospace text-info me-3">Serial: <strong>{{ target_serial }}</strong></span>
                    <span class="text-muted small">IP: <span id="cpe_ip">...</span></span>
                </div>
                <button class="btn btn-sm btn-outline-danger" onclick="rebootTarget()">⚡ Reboot Equipo</button>
            </div>
        </div>
    </div>

    <div class="row g-4">
        <!-- FORMULARIO DE COMANDOS RPC -->
        <div class="col-lg-5">
            <div class="card p-3 mb-3">
                <h5 class="text-info fw-bold mb-3">Enviar Comando RPC</h5>
                <form id="acsForm">
                    <input type="hidden" id="serialseleccionado" value="{{ target_serial }}">
                    <div class="mb-3">
                        <label class="form-label small text-muted">Operación TR-069:</label>
                        <select id="seleccion" class="form-select" onchange="bloquearCampos()" required>
                            <option value="">Seleccione una opción</option>
                            <option value="GetParameterValues">GetParameterValues</option>
                            <option value="SetParameterValues">SetParameterValues</option>
                            <option value="GetParameterAttributes">GetParameterAttributes</option>
                            <option value="AddObject">AddObject</option>
                            <option value="DeleteObject">DeleteObject</option>
                            <option value="FirmwareUpgrade">FirmwareUpgrade</option>
                            <option value="Reboot">Reboot</option>
                            <option value="FactoryDefault">FactoryDefault</option>
                        </select>
                    </div>

                    <div class="mb-3">
                        <div class="d-flex justify-content-between mb-1">
                            <label class="form-label small text-muted mb-0">Parámetros:</label>
                            <button type="button" class="btn btn-sm btn-outline-success py-0 px-2 fw-bold" onclick="agregarFilaParametro()">+ Parámetro</button>
                        </div>
                        <div id="paramContainer"></div>
                    </div>
                    <button type="submit" class="btn btn-primary w-100 fw-bold">Ejecutar en {{ target_serial }}</button>
                </form>
            </div>

            <div class="card p-3">
                <h6 class="text-muted mb-2">Comandos Pendientes en Cola</h6>
                <div class="table-responsive" style="max-height: 180px;">
                    <table class="table table-dark table-sm small mb-0">
                        <thead><tr><th>ID</th><th>Método</th><th>Detalle</th><th>Acción</th></tr></thead>
                        <tbody id="pendingBody"><tr><td colspan="4" class="text-center">Sin pendientes</td></tr></tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- CONSOLA / TERMINAL EXCLUSIVA -->
        <div class="col-lg-7">
            <div class="card p-3">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <h5 class="text-info fw-bold mb-0">Consola & XML Traza Exclusiva</h5>
                    <div>
                        <button class="btn btn-sm btn-outline-danger me-2" onclick="limpiarLogs()">Limpiar Log</button>
                        <a href="/api/logs/download?serial={{ target_serial }}" class="btn btn-sm btn-outline-success" download>Descargar Log (.txt)</a>
                    </div>
                </div>
                <div id="terminal"></div>
            </div>
        </div>
    </div>
    {% endif %}
</div>

<script>
    const targetSerial = "{{ target_serial or '' }}";

    {% if not target_serial %}
    function cargarEquipos() {
        fetch('/api/devices')
            .then(res => res.json())
            .then(devices => {
                const container = document.getElementById('devicesGrid');
                container.innerHTML = '';
                if (devices.length === 0) {
                    container.innerHTML = '<div class="col-12 text-center text-warning">No hay equipos reportados en el ACS.</div>';
                    return;
                }
                devices.forEach(d => {
                    container.innerHTML += `
                        <div class="col-md-4 col-lg-3">
                            <div class="card device-card p-3 h-100" onclick="window.location.href='/cpe?serial=${d.serial_number}'">
                                <div class="d-flex justify-content-between mb-2">
                                    <span class="badge ${d.is_online ? 'bg-success' : 'bg-danger'}">${d.status_badge}</span>
                                    <small class="text-muted">${d.last_inform.split(' ')[1] || ''}</small>
                                </div>
                                <h6 class="fw-bold text-info mb-1">${d.model_name}</h6>
                                <div class="font-monospace fw-bold text-white mb-1">${d.serial_number}</div>
                                <small class="text-muted">IP: ${d.ip_data || d.ip_address || 'N/A'}</small>
                            </div>
                        </div>
                    `;
                });
            });
    }
    cargarEquipos();
    setInterval(cargarEquipos, 4000);
    {% else %}

    function agregarFilaParametro() {
        const container = document.getElementById('paramContainer');
        const rowId = 'prow_' + Date.now();
        const row = document.createElement('div');
        row.className = 'param-row mb-2';
        row.id = rowId;
        row.innerHTML = `
            <div class="row g-2 align-items-center">
                <div class="col-6"><input type="text" class="form-control form-control-sm param-input" placeholder="Device.WiFi.SSID.1.SSID"></div>
                <div class="col-3">
                    <select class="form-select form-select-sm type-input">
                        <option value="string">string</option>
                        <option value="unsignedInt">unsignedInt</option>
                        <option value="boolean">boolean</option>
                    </select>
                </div>
                <div class="col-3"><input type="text" class="form-control form-control-sm val-input" placeholder="Valor"></div>
            </div>
        `;
        container.appendChild(row);
    }

    function cargarDetalles() {
        fetch(`/api/device/details/${targetSerial}`)
            .then(res => res.json())
            .then(d => {
                document.getElementById('cpe_status').className = `badge ${d.is_online ? 'bg-success' : 'bg-danger'} fs-6 me-2`;
                document.getElementById('cpe_status').textContent = d.status_badge;
                document.getElementById('cpe_model').textContent = d.model_name;
                document.getElementById('cpe_ip').textContent = d.ip_data || d.ip_address || 'N/A';
            });
    }

    function cargarPendientes() {
        fetch(`/api/pending_commands?serial=${targetSerial}`)
            .then(res => res.json())
            .then(cmds => {
                const tbody = document.getElementById('pendingBody');
                tbody.innerHTML = cmds.length === 0 ? '<tr><td colspan="4" class="text-center">Sin pendientes</td></tr>' : '';
                cmds.forEach(c => {
                    tbody.innerHTML += `<tr><td>#${c.id}</td><td>${c.rpc_method}</td><td>${c.parameter_name || ''}</td><td><button class="btn btn-sm btn-outline-danger py-0" onclick="cancelarCmd(${c.id})">x</button></td></tr>`;
                });
            });
    }

    function cancelarCmd(id) {
        fetch(`/api/pending_commands/cancel/${id}`, { method: 'POST' }).then(() => cargarPendientes());
    }

    document.getElementById('acsForm').addEventListener('submit', function(e) {
        e.preventDefault();
        const rpc = document.getElementById('seleccion').value;
        const params = [], types = [], values = [];

        document.querySelectorAll('.param-row').forEach(row => {
            params.push(row.querySelector('.param-input')?.value.trim() || '');
            types.push(row.querySelector('.type-input')?.value.trim() || 'string');
            values.push(row.querySelector('.val-input')?.value.trim() || '');
        });

        const formData = new URLSearchParams();
        formData.append('serialseleccionado', targetSerial);
        formData.append('seleccion', rpc);
        formData.append('parametro', params.join('|'));
        formData.append('tipo', types.join('|'));
        formData.append('valor', values.join('|'));

        fetch('/api/execute', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: formData })
            .then(res => res.json())
            .then(data => { cargarPendientes(); });
    });

    function escapeHtml(text) {
        if (!text) return '';
        return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    function cargarLogs() {
        fetch(`/api/logs/stream?serial=${targetSerial}`)
            .then(res => res.json())
            .then(logs => {
                const term = document.getElementById('terminal');
                term.innerHTML = '';
                logs.forEach(l => {
                    term.innerHTML += `<div class="log-header">[${l.timestamp}] [${l.serial}] [${l.rpc}] [${l.status}]</div>`;
                    if (l.detail) {
                        term.innerHTML += `<pre class="xml-box">${escapeHtml(l.detail)}</pre>`;
                    }
                });
                term.scrollTop = term.scrollHeight;
            })
            .catch(err => console.error("Error cargando logs:", err));
    }

    function limpiarLogs() {
        if (!confirm('¿Desea limpiar el historial de logs de este equipo?')) return;
        fetch(`/api/logs/clear?serial=${targetSerial}`, { method: 'POST' })
            .then(res => res.json())
            .then(data => {
                document.getElementById('terminal').innerHTML = '';
                cargarLogs();
            });
    }

    function rebootTarget() {
        if (!confirm(`¿Reiniciar equipo ${targetSerial}?`)) return;
        const formData = new URLSearchParams();
        formData.append('serialseleccionado', targetSerial);
        formData.append('seleccion', 'Reboot');
        fetch('/api/execute', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: formData })
            .then(() => alert('Reboot encolado.'));
    }

    agregarFilaParametro();
    cargarDetalles();
    cargarPendientes();
    cargarLogs();
    setInterval(() => { cargarDetalles(); cargarPendientes(); cargarLogs(); }, 3000);
    {% endif %}
</script>
</body>
</html>
"""


@app.route('/cpe')
@app.route('/')
def cpe_view():
    target_serial = request.args.get('serial', '').strip()
    return render_template_string(HTML_TEMPLATE, target_serial=target_serial)


if __name__ == '__main__':
    print("Iniciando Consola Individual de CPEs en el puerto 9038...")
    print("Acceso WEB: http://192.168.35.9:9038/cpe")
    app.run(host='0.0.0.0', port=9038, debug=True, use_reloader=False, threaded=True)