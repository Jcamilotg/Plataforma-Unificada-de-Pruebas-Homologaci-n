import socket
import subprocess
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

# --- DIRECCIÓN IP BASE DEL SERVIDOR PRINCIPAL ---
SERVER_IP = "192.168.35.9"

# --- DEFINICIÓN DE SERVICIOS Y NODOS DE RED SAGEMCOM ---
SERVICES = [
    {
        "id": "acs",
        "name": "Servidor ACS TR-069",
        "host": SERVER_IP,
        "port": 9034,
        "url": f"http://{SERVER_IP}:9034",
        "icon": "bi-router-fill",
        "color": "primary",
        "user": None,
        "pass": None,
        "description": "Gestión, provisión, informes y Connection Request de CPEs bajo protocolo TR-069/CWMP."
    },
    {
        "id": "monitoreo",
        "name": "Monitoreo de CPE",
        "host": SERVER_IP,
        "port": 9035,
        "url": f"http://{SERVER_IP}:9035",
        "icon": "bi-activity",
        "color": "success",
        "user": None,
        "pass": None,
        "description": "Telemetría en tiempo real, salud de red, métricas Wi-Fi, DOCSIS, SNMP y estado de línea."
    },
    {
        "id": "auditoria",
        "name": "Suite de Auditoría v4.0",
        "host": SERVER_IP,
        "port": 9036,
        "url": f"http://{SERVER_IP}:9036/",
        "icon": "bi-shield-lock-fill",
        "color": "info",
        "user": None,
        "pass": None,
        "description": "Escaneo 1-65535, fuerza bruta SNMP, prueba Open Resolver DNS, TR-069 y auditoría de backups."
    },
    {
        "id": "firmware",
        "name": "Servidor de Firmware",
        "host": SERVER_IP,
        "port": 80,
        "url": f"http://{SERVER_IP}/fw/",
        "icon": "bi-file-earmark-binary-fill",
        "color": "warning",
        "user": None,
        "pass": None,
        "description": "Repositorio central de imágenes binarias (.bin, .img), notas de versión y archivos de actualización."
    },
    {
        "id": "cmts",
        "name": "CMTS (Listado Cable Modems)",
        "host": "192.168.35.4",
        "port": 80,
        "url": "http://192.168.35.4/pages/CmList/CmList.html",
        "icon": "bi-hdd-rack-fill",
        "color": "danger",
        "user": "admin",
        "pass": "admin",
        "description": "Monitoreo de estado DOCSIS, potencias Rx/Tx, SNR y listado de Cable Modems conectados."
    },
    {
        "id": "olt",
        "name": "OLT GPON",
        "host": "192.168.35.10",
        "port": 443,
        "url": "https://192.168.35.10",
        "icon": "bi-ethernet",
        "color": "secondary",
        "user": "sagemcom",
        "pass": "sagemcom",
        "description": "Gestión de ONTs/ONUs, enlaces ópticos de fibra GPON, perfiles de tráfico y VLANs."
    },
    {
        "id": "mikrotik",
        "name": "Router Principal MikroTik",
        "host": "192.168.35.1",
        "port": 80,
        "url": "http://192.168.35.1/webfig/#IP:DHCP_Server.Leases",
        "icon": "bi-diagram-3-fill",
        "color": "light",
        "user": "admin",
        "pass": "$agem001",
        "description": "CCR2004 WebFig: DHCP Server Leases, mapeo de IPs/MACs activas y control general de red."
    },
    {
        "id": "provisioner",
        "name": "Aprovisionador DOCSIS & eMTA",
        "host": SERVER_IP,
        "port": 9037,
        "url": f"http://{SERVER_IP}:9037",
        "icon": "bi-cpu-fill",
        "color": "warning",
        "user": None,
        "pass": None,
        "description": "Gestor grafico de reservaciones DHCP (MAC/IP), compilador DOCSIS (docsis -p) y repositorio TFTP."
    },
    {
        "id": "provisioner",
        "name": "SSH LOG",
        "host": SERVER_IP,
        "port": 9001,
        "url": f"http://{SERVER_IP}:9001",
        "icon": "bi-cpu-fill",
        "color": "warning",
        "user": None,
        "pass": None,
        "description": "Monitor SSH de logs con palabras claves"
    }
]

# --- PLANTILLA HTML5 / BOOTSTRAP 5 / NEON DARK THEME ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sagemcom - Hub Central de Laboratorio</title>
    <!-- Bootstrap 5 CSS & Icons -->
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css">
    <style>
        body {
            background: linear-gradient(135deg, #0b132b 0%, #1c2541 50%, #0f172a 100%);
            color: #f8fafc;
            min-height: 100vh;
            font-family: system-ui, -apple-system, sans-serif;
        }
        .navbar-brand-custom {
            font-weight: 800;
            letter-spacing: 1.5px;
            color: #38bdf8;
            font-size: 1.6rem;
        }
        .card-tool {
            background: rgba(30, 41, 59, 0.7);
            border: 1px solid #334155;
            border-radius: 14px;
            backdrop-filter: blur(8px);
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }
        .card-tool:hover {
            transform: translateY(-6px);
            border-color: #38bdf8;
            box-shadow: 0 10px 25px -5px rgba(56, 189, 248, 0.25);
        }
        .icon-box {
            width: 55px;
            height: 55px;
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.8rem;
        }
        .status-dot {
            height: 10px;
            width: 10px;
            border-radius: 50%;
            display: inline-block;
            margin-right: 6px;
        }
        .dot-checking { background-color: #f59e0b; box-shadow: 0 0 8px #f59e0b; }
        .dot-online { background-color: #10b981; box-shadow: 0 0 8px #10b981; }
        .dot-offline { background-color: #ef4444; box-shadow: 0 0 8px #ef4444; }
        .quick-ip-btn {
            background: rgba(15, 23, 42, 0.8);
            border: 1px solid #334155;
            color: #cbd5e1;
            transition: all 0.2s;
        }
        .quick-ip-btn:hover {
            background: #38bdf8;
            color: #0f172a;
            font-weight: bold;
        }
        .cred-badge {
            background: rgba(15, 23, 42, 0.9);
            border: 1px dashed #475569;
            font-size: 0.8rem;
        }
    </style>
</head>
<body class="d-flex flex-column justify-content-between">

    <!-- NAVBAR SUPERIOR -->
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark bg-opacity-50 border-bottom border-secondary px-4">
        <div class="container-fluid">
            <a class="navbar-brand navbar-brand-custom d-flex align-items-center gap-2" href="#">
                <i class="bi bi-cpu-fill text-info"></i>
                <span>SAGEMCOM</span>
                <span class="badge bg-info text-dark fs-6 ms-2">Lab Central</span>
            </a>
            <div class="d-flex align-items-center gap-3">
                <span class="text-secondary small font-monospace d-none d-md-inline"><i class="bi bi-hdd-network me-1"></i>Server: {{ server_ip }}</span>
                <span class="badge bg-dark border border-secondary text-warning font-monospace px-3 py-2" id="liveClock">00:00:00</span>
            </div>
        </div>
    </nav>

    <!-- CONTENIDO PRINCIPAL -->
    <div class="container my-5" style="max-width: 1300px;">

        <!-- ENCABEZADO -->
        <div class="text-center mb-5">
            <h1 class="fw-bold display-5 mb-2 text-white">Plataforma Unificada de Pruebas & Homologación</h1>
            <p class="text-secondary lead fs-6">Acceso centralizado a servidores TR-069, telemétrica de CPEs, CMTS, OLT, MikroTik Router y auditorías.</p>
        </div>

        <!-- TARJETAS DE HERRAMIENTAS Y NODOS DE RED -->
        <div class="row g-4 mb-5">
            {% for s in services %}
            <div class="col-md-6 col-lg-4">
                <div class="card card-tool h-100 p-3 d-flex flex-column justify-content-between">
                    <div>
                        <div class="d-flex justify-content-between align-items-start mb-3">
                            <div class="icon-box bg-{{ s.color }} bg-opacity-20 text-{{ s.color }}">
                                <i class="bi {{ s.icon }}"></i>
                            </div>
                            <span class="badge bg-dark border border-secondary px-2 py-1 font-monospace" id="status-badge-{{ s.id }}">
                                <span class="status-dot dot-checking" id="dot-{{ s.id }}"></span>
                                <span id="text-{{ s.id }}">Verificando...</span>
                            </span>
                        </div>
                        <h5 class="fw-bold text-white mb-1">{{ s.name }}</h5>
                        <p class="text-secondary small mb-2">{{ s.description }}</p>

                        <!-- CREDENCIALES DE ACCESO EN CASO DE EXISTIR -->
                        {% if s.user %}
                        <div class="cred-badge p-2 rounded mb-3 text-warning font-monospace d-flex justify-content-between align-items-center">
                            <span><i class="bi bi-person-fill text-secondary me-1"></i><strong>{{ s.user }}</strong></span>
                            <span><i class="bi bi-key-fill text-secondary me-1"></i><strong>{{ s.pass }}</strong></span>
                        </div>
                        {% endif %}
                    </div>
                    <div>
                        <hr class="border-secondary opacity-25 my-2">
                        <div class="d-flex justify-content-between align-items-center">
                            <span class="text-info font-monospace small"><i class="bi bi-hdd-stack me-1"></i>{{ s.host }}:{{ s.port }}</span>
                            <a href="{{ s.url }}" target="_blank" class="btn btn-sm btn-outline-{{ s.color }} fw-bold px-3">
                                Abrir <i class="bi bi-box-arrow-up-right ms-1"></i>
                            </a>
                        </div>
                    </div>
                </div>
            </div>
            {% endfor %}
        </div>

        <!-- PANEL DE UTILIDADES Y ACCESOS RÁPIDOS -->
        <div class="row g-4">

            <!-- ACCESO RÁPIDO A CPEs -->
            <div class="col-md-7">
                <div class="card card-tool p-4 h-100">
                    <h5 class="fw-bold text-info mb-3"><i class="bi bi-router me-2"></i>Gateways de Prueba LAN / DOCSIS</h5>
                    <p class="text-secondary small mb-3">Puertas de enlace predeterminadas para ingresar a la GUI local de los módems/routers en prueba:</p>
                    <div class="d-flex flex-wrap gap-2">
                        <a href="http://192.168.1.1" target="_blank" class="btn quick-ip-btn btn-sm font-monospace">🔗 192.168.1.1 (LAN Standard)</a>
                        <a href="http://192.168.0.1" target="_blank" class="btn quick-ip-btn btn-sm font-monospace">🔗 192.168.0.1 (DOCSIS Default)</a>
                        <a href="http://192.168.100.1" target="_blank" class="btn quick-ip-btn btn-sm font-monospace">🔗 192.168.100.1 (CableModem Status)</a>
                        <a href="http://192.168.35.1" target="_blank" class="btn quick-ip-btn btn-sm font-monospace">🔗 192.168.35.1 (Lab Gateway)</a>
                    </div>
                </div>
            </div>

            <!-- TEST DE PING RÁPIDO -->
            <div class="col-md-5">
                <div class="card card-tool p-4 h-100">
                    <h5 class="fw-bold text-info mb-2"><i class="bi bi-terminal me-2"></i>Ping de Conectividad Rápido</h5>
                    <p class="text-secondary small mb-3">Comprueba respuesta ICMP hacia cualquier dispositivo del laboratorio:</p>
                    <form id="pingForm" class="input-group">
                        <input type="text" id="pingIp" class="form-control bg-dark text-white border-secondary font-monospace" placeholder="Ej: 192.168.35.4" required>
                        <button type="submit" class="btn btn-info fw-bold">Probar</button>
                    </form>
                    <div id="pingResult" class="mt-3 font-monospace small text-warning" style="display: none;"></div>
                </div>
            </div>

        </div>

    </div>

    <!-- FOOTER -->
    <footer class="text-center py-3 border-top border-secondary bg-dark bg-opacity-50 text-secondary small">
        Sagemcom Broadband Lab Hub &copy; 2026 — Plataforma Unificada de Homologación & Telemetría
    </footer>

    <!-- JS BOOTSTRAP Y LÓGICA DE MONITOREO EN TIEMPO REAL -->
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        // Reloj en tiempo real
        function updateClock() {
            const now = new Date();
            document.getElementById('liveClock').textContent = now.toLocaleTimeString('es-MX');
        }
        setInterval(updateClock, 1000);
        updateClock();

        // Verificación de estado de los servicios (HealthCheck multihost/puerto)
        function checkServiceStatus(id, host, port) {
            fetch('/api/check_port?host=' + encodeURIComponent(host) + '&port=' + port)
                .then(res => res.json())
                .then(data => {
                    const dot = document.getElementById('dot-' + id);
                    const text = document.getElementById('text-' + id);

                    if (data.online) {
                        dot.className = "status-dot dot-online";
                        text.textContent = "ONLINE";
                        text.style.color = "#10b981";
                    } else {
                        dot.className = "status-dot dot-offline";
                        text.textContent = "OFFLINE";
                        text.style.color = "#ef4444";
                    }
                })
                .catch(() => {
                    document.getElementById('dot-' + id).className = "status-dot dot-offline";
                    document.getElementById('text-' + id).textContent = "ERROR";
                });
        }

        // Ejecutar chequeo de estado de todos los equipos al cargar y cada 15 segundos
        function runAllStatusChecks() {
            {% for s in services %}
            checkServiceStatus('{{ s.id }}', '{{ s.host }}', {{ s.port }});
            {% endfor %}
        }
        runAllStatusChecks();
        setInterval(runAllStatusChecks, 15000);

        // Lógica para prueba de Ping Rápido
        document.getElementById('pingForm').addEventListener('submit', function(e) {
            e.preventDefault();
            const ip = document.getElementById('pingIp').value.trim();
            const resDiv = document.getElementById('pingResult');
            resDiv.style.display = 'block';
            resDiv.className = 'mt-3 font-monospace small text-info';
            resDiv.textContent = '⏳ Enviando paquetes ICMP ping a ' + ip + '...';

            fetch('/api/ping?ip=' + encodeURIComponent(ip))
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        resDiv.className = 'mt-3 font-monospace small text-success';
                        resDiv.textContent = '✅ ' + data.message;
                    } else {
                        resDiv.className = 'mt-3 font-monospace small text-danger';
                        resDiv.textContent = '❌ ' + data.message;
                    }
                })
                .catch(err => {
                    resDiv.className = 'mt-3 font-monospace small text-danger';
                    resDiv.textContent = 'Error al ejecutar la prueba de ping.';
                });
        });
    </script>
</body>
</html>
"""


# --- RUTAS Y APIS DEL SERVIDOR FLASK ---

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, server_ip=SERVER_IP, services=SERVICES)


@app.route('/api/check_port')
def api_check_port():
    """Verifica la conectividad TCP a cualquier IP/puerto de la red."""
    host = request.args.get('host', SERVER_IP).strip()
    port = request.args.get('port', type=int)

    if not port:
        return jsonify({"error": "Puerto invalido"}), 400

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.2)
        result = sock.connect_ex((host, port))
        sock.close()
        return jsonify({"host": host, "port": port, "online": (result == 0)})
    except Exception:
        return jsonify({"host": host, "port": port, "online": False})


@app.route('/api/ping')
def api_ping():
    """Ejecuta un comando ping rápido hacia una IP objetivo."""
    ip = request.args.get('ip', '').strip()
    if not ip:
        return jsonify({"success": False, "message": "Dirección IP vacía"})

    try:
        cmd = ["ping", "-c", "1", "-W", "2", ip]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)

        if res.returncode == 0:
            return jsonify({"success": True, "message": f"El equipo {ip} responde correctamente."})
        else:
            return jsonify({"success": False, "message": f"Sin respuesta ICMP de {ip} (Host inalcanzable)."})
    except Exception as e:
        return jsonify({"success": False, "message": f"Error ejecutando ping: {e}"})


if __name__ == '__main__':
    print(f"🚀 Iniciando Portal Principal SAGEMCOM en puerto 80 (http://{SERVER_IP})...")
    app.run(host='0.0.0.0', port=9000, debug=True)