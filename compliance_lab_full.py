import socket
import ssl
import re
import json
import sqlite3
import subprocess
import urllib.parse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template_string, request, Response, redirect, url_for, jsonify
import requests
import urllib3

# Desactivar advertencias de certificados autofirmados para pruebas de diagnóstico
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
DB_PATH = "compliance_audit.db"

# Lista oficial de puertos base para etiquetado en homologación
STANDARD_PORTS = {
    22: "SSH (22)",
    23: "Telnet (23)",
    53: "DNS Server (53)",
    80: "HTTP Web (80)",
    443: "HTTPS Web (443)",
    161: "SNMP (161)",
    1900: "UPnP / SSDP (1900)",
    5060: "SIP / VoIP (5060)",
    7547: "TR-069 ACS (7547)"
}

# Lista extendida de comunidades SNMP a verificar en auditoría
SNMP_COMMUNITIES = [
    "$agem001", "1234", "12345", "123456", "EsY87p", "P0rt4lC4utiV0", "abcdef", "access",
    "access123", "admin", "admin1", "admin123", "administrator", "backup", "backup1",
    "backup123", "cablemodem", "cbadmcmrw", "cfg", "cfg123", "cisco", "cisco123",
    "cisco_ro", "cisco_rw", "community", "company", "conf", "config", "config1",
    "config123", "control", "core", "corp", "corp123", "data", "data123", "default",
    "default1", "default123", "dell", "device", "devices", "dlink", "edge", "field",
    "firewall", "firewall123", "guest", "guest1", "guest123", "hd", "host", "hosts",
    "hp", "hp_admin", "hp_ro", "hp_rw", "hpe2016", "ibm", "internet", "intranet", "isp",
    "isp123", "it", "it123", "lan", "letmein", "linux", "manager", "manager1",
    "manager123", "modem", "modem1", "modem123", "monitor", "monitor1", "monitor123",
    "monitoring", "net", "netadmin", "netman", "netmgmt", "network", "network1",
    "network123", "noc", "noc123", "office", "office123", "operation", "operator",
    "ops", "ops123", "oracle", "passwd", "password", "password1", "private", "private1",
    "private123", "private_ro", "private_rw", "public", "public1", "public123",
    "public_ro", "public_rw", "qwerty", "read", "read123", "readonly", "readwrite",
    "remote", "root", "root123", "router", "router1", "router123", "sagemcom",
    "secret", "secret123", "secure", "security", "security123", "server", "servers",
    "service", "services", "snmp", "snmp123", "snmp_ro", "snmp_rw", "snmpadmin",
    "snmpd", "snmptrap", "snmpv1", "snmpv2", "snmpwalk", "sun", "super", "superuser",
    "supervisor", "support", "support1", "support123", "switch", "switch1", "switch123",
    "system", "system1", "system123", "test", "test1", "test123", "tplink", "unix",
    "user", "wan", "windows", "write", "write123"
]


# --- BASE DE DATOS SQLITE ---

def init_db():
    """Inicializa la base de datos para guardar el historial de auditorías."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            ip_address TEXT NOT NULL,
            model_name TEXT DEFAULT 'N/A',
            firmware_version TEXT DEFAULT 'N/A',
            scan_type TEXT DEFAULT 'Completo (1-65535)',
            results_json TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()


def save_audit_log(ip, model, firmware, scan_type, results_dict):
    """Guarda un registro de diagnóstico en la base de datos y retorna el ID insertado."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "INSERT INTO audit_logs (timestamp, ip_address, model_name, firmware_version, scan_type, results_json) VALUES (?, ?, ?, ?, ?, ?)",
        (now_str, ip, model or "Detectado/Desconocido", firmware or "Detectado/Desconocido", scan_type,
         json.dumps(results_dict))
    )
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return new_id


def get_audit_history():
    """Obtiene los últimos 20 diagnósticos guardados."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 20")
    rows = cursor.fetchall()
    conn.close()

    history = []
    for r in rows:
        item = dict(r)
        try:
            item['details'] = json.loads(item['results_json'])
        except Exception:
            item['details'] = {}
        history.append(item)
    return history


def clear_audit_history():
    """Borra todo el historial de auditorías de la base de datos."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM audit_logs")
    conn.commit()
    conn.close()


def delete_single_audit_log(log_id):
    """Borra un único registro del historial de la base de datos por su ID."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM audit_logs WHERE id = ?", (log_id,))
    conn.commit()
    conn.close()


init_db()


# --- MÓDULOS DE VERIFICACIÓN, ESCANEO Y VULNERABILIDADES ---

def check_single_port(ip, port, timeout=0.25):
    """Verifica si un puerto TCP individual responde."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def scan_full_range(ip, start_port=1, end_port=65535, max_threads=150):
    """Escanea estrictamente los 65,535 puertos TCP usando un pool de hilos."""
    open_ports = []

    def test_port(port):
        if check_single_port(ip, port, timeout=0.25):
            open_ports.append(port)

    with ThreadPoolExecutor(max_workers=max_threads) as executor:
        executor.map(test_port, range(start_port, end_port + 1))

    return sorted(open_ports)


def get_service_banner(ip, port, timeout=2):
    """Obtiene el banner o versión del servicio."""
    try:
        if port in [80, 443, 7547, 8080, 8443]:
            protocol = "https" if port in [443, 8443] else "http"
            url = f"{protocol}://{ip}:{port}"
            try:
                res = requests.get(url, timeout=timeout, verify=False)
                server_hdr = res.headers.get("Server")
                auth_hdr = res.headers.get("WWW-Authenticate")

                if server_hdr:
                    return f"Web Server: {server_hdr}"
                elif auth_hdr:
                    return f"Auth Realm: {auth_hdr}"
                else:
                    return "HTTP/HTTPS Activo (Header Oculto)"
            except requests.exceptions.RequestException:
                return "Responde HTTP (Sin cabecera Server)"

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))
        initial_data = sock.recv(1024).decode('utf-8', errors='ignore').strip()
        sock.close()

        if initial_data:
            first_line = initial_data.split('\r\n')[0].split('\n')[0]
            return first_line[:60]
        else:
            return "Puerto Abierto (Esperando interacción)"

    except Exception:
        return "Puerto Abierto (Sin banner explícito)"


def test_snmp_community(ip, comm, timeout=0.4):
    """Evalúa una comunidad SNMP individual por paquete UDP BER de alto rendimiento."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        comm_bytes = comm.encode('utf-8')
        oid = bytearray([0x2b, 0x06, 0x01, 0x02, 0x01, 0x01, 0x01, 0x00])
        varbind = bytearray([0x30, len(oid) + 4, 0x06, len(oid), 0x05, 0x00])
        varbind[4:4] = oid
        varbind_list = bytearray([0x30, len(varbind)]) + varbind
        pdu_header = bytearray([0x02, 0x04, 0x00, 0x00, 0x00, 0x01, 0x02, 0x01, 0x00, 0x02, 0x01, 0x00])
        pdu_payload = pdu_header + varbind_list
        pdu = bytearray([0xa0, len(pdu_payload)]) + pdu_payload
        msg_payload = bytearray([0x02, 0x01, 0x01, 0x04, len(comm_bytes)]) + comm_bytes + pdu
        packet = bytearray([0x30, len(msg_payload)]) + msg_payload

        sock.sendto(packet, (ip, 161))
        data, _ = sock.recvfrom(1024)
        sock.close()
        if data and len(data) > 10:
            return comm
    except Exception:
        pass
    return None


def audit_snmp_communities(ip):
    """Prueba de forma paralela y multihilo toda la lista de comunidades SNMP por defecto sobre UDP 161."""
    vulnerable_communities = []

    with ThreadPoolExecutor(max_workers=35) as executor:
        futures = [executor.submit(test_snmp_community, ip, comm) for comm in SNMP_COMMUNITIES]
        for future in futures:
            res = future.result()
            if res and res not in vulnerable_communities:
                vulnerable_communities.append(res)

    if vulnerable_communities:
        return f"❌ ALERTA: Comunidades SNMP por defecto activas: {', '.join(vulnerable_communities)}"
    return "PASS: No responde a comunidades SNMP conocidas en el diccionario."


def audit_dns_open_resolver(ip, timeout=1.5):
    """Verifica si el puerto UDP 53 actúa como un resolvedor DNS abierto a peticiones WAN."""
    try:
        dns_query = (
            b'\xaa\xbb'  # Transaction ID
            b'\x01\x00'  # Flags: Standard Query
            b'\x00\x01'  # Questions: 1
            b'\x00\x00\x00\x00\x00\x00'
            b'\x06google\x03com\x00'  # QNAME
            b'\x00\x01\x00\x01'  # QTYPE A, QCLASS IN
        )
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(dns_query, (ip, 53))
        data, _ = sock.recvfrom(512)
        sock.close()

        if data and len(data) > 12:
            return "❌ FAIL: Resolvedor DNS Abierto Detectado (Open Resolver - Riesgo de Amplificación DDoS)."
    except Exception:
        pass
    return "PASS: No actúa como DNS Open Resolver hacia la interfaz escaneada."


def audit_tls_and_cert(ip, port=443):
    """Audita versiones de TLS y vigencia del certificado SSL."""
    report = {"protocols": {}, "cert_info": {}}

    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLSv1)
        with socket.create_connection((ip, port), timeout=2) as sock:
            with ctx.wrap_socket(sock) as ssock:
                report["protocols"]["TLS_1_0"] = "FAIL (Soportado - Debe deshabilitarse)"
    except Exception:
        report["protocols"]["TLS_1_0"] = "PASS (Deshabilitado / Seguro)"

    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
        with socket.create_connection((ip, port), timeout=2) as sock:
            with ctx.wrap_socket(sock) as ssock:
                report["protocols"]["TLS_1_2"] = "PASS (Activo)"
                cert = ssock.getpeercert()
                if cert:
                    not_after = datetime.strptime(cert['notAfter'], '%b %d %H:%M:%S %Y %Z')
                    days_left = (not_after - datetime.now()).days
                    report["cert_info"] = {
                        "issuer": dict(x[0] for x in cert.get('issuer', [])).get('commonName', 'Desconocido'),
                        "days_valid": days_left,
                        "status": "Válido" if days_left > 0 else "Expirado"
                    }
    except Exception as e:
        report["protocols"]["TLS_1_2"] = f"Error al verificar: {e}"

    return report


def audit_web_vulnerabilities_and_html(ip):
    """Realiza cURL en puertos 80/443, analiza el HTML, prueba Host Header Injection y Cookie Flags."""
    web_report = {}

    for port in [80, 443]:
        proto = "https" if port == 443 else "http"
        url = f"{proto}://{ip}:{port}"

        try:
            res = requests.get(url, timeout=3, verify=False)
            html_text = res.text

            anomalies = []
            if "eval(" in html_text or "document.write(unescape(" in html_text:
                anomalies.append("⚠️ Código JavaScript sospechoso/ofuscado detectado (eval/unescape).")

            if re.search(r'<!--.*?(password|admin|todo|fix|debug|credenciales).*?-->', html_text, re.IGNORECASE):
                anomalies.append("⚠️ Comentarios HTML expuestos con palabras clave sensibles (passwords/debug/admin).")

            if "Index of /" in html_text or "Directory Listing" in html_text:
                anomalies.append("❌ Directory Listing Activo (Exposición pública de archivos del servidor).")

            if re.search(r'(fatal error|stack trace|sqlite3::|exception in thread|warning: mysql)', html_text,
                         re.IGNORECASE):
                anomalies.append("❌ Fuga de información por mensajes de error o modo Debug activado.")

            host_injection_status = "PASS (Protegido)"
            try:
                fake_host_res = requests.get(url, headers={"Host": "evil-attacker-domain.com"}, timeout=2, verify=False)
                if fake_host_res.status_code == 200 and "evil-attacker-domain.com" in fake_host_res.text:
                    host_injection_status = "❌ FAIL: Vulnerable a Host Header Injection / DNS Rebinding."
            except Exception:
                pass

            cookie_flags = []
            for c in res.cookies:
                flags = []
                if not c.secure and port == 443:
                    flags.append("Falta Secure")
                if not c.has_nonstandard_attr('httponly'):
                    flags.append("Falta HttpOnly")
                if flags:
                    cookie_flags.append(f"Cookie '{c.name}': {', '.join(flags)}")

            dangerous_methods_found = []
            for method in ["OPTIONS", "PUT", "DELETE", "TRACE"]:
                try:
                    m_res = requests.request(method, url, timeout=1.5, verify=False)
                    if m_res.status_code in [200, 204]:
                        dangerous_methods_found.append(method)
                except Exception:
                    pass

            sensitive_paths = ["/admin", "/.env", "/.git/HEAD", "/config.json", "/backup.sql", "/phpmyadmin"]
            exposed_paths = []
            for path in sensitive_paths:
                try:
                    p_res = requests.get(f"{url}{path}", timeout=1.5, verify=False)
                    if p_res.status_code == 200:
                        exposed_paths.append(f"{path} (Estado: 200 OK)")
                except Exception:
                    pass

            title_match = re.search(r'<title>(.*?)</title>', html_text, re.IGNORECASE)
            page_title = title_match.group(1).strip() if title_match else "Sin título"

            web_report[f"Port_{port}"] = {
                "url": url,
                "status_code": res.status_code,
                "page_title": page_title,
                "server_header": res.headers.get("Server", "Oculto (Recomendado)"),
                "dns_rebinding_protection": host_injection_status,
                "cookie_security_warnings": cookie_flags if cookie_flags else [
                    "Todas las cookies cumplen con flags de seguridad."],
                "dangerous_http_methods": dangerous_methods_found if dangerous_methods_found else [
                    "Sin métodos peligrosos habilitados."],
                "html_preview": html_text[:800] + ("..." if len(html_text) > 800 else ""),
                "anomalies": anomalies if anomalies else ["Sin anomalías o cosas raras detectadas en HTML."],
                "exposed_paths": exposed_paths if exposed_paths else ["Ninguna ruta crítica sensible expuesta."]
            }

        except Exception as e:
            web_report[f"Port_{port}"] = {"error": f"No responde en {url}: {e}"}

    return web_report


def audit_tr069_cwmp(ip, port=7547):
    """Verifica si el puerto TR-069 exige autenticación HTTP obligatoria en Connection Request."""
    url = f"http://{ip}:{port}"
    try:
        res = requests.get(url, timeout=2.5, verify=False)
        if res.status_code in [401, 403]:
            return "PASS: TR-069 exige autenticación obligatoria (HTTP 401/403)."
        elif res.status_code == 200:
            return "❌ FAIL: TR-069 Connection Request expuesto sin autenticación (HTTP 200 OK)."
        else:
            return f"INFO: Respondió con código de estado {res.status_code}"
    except Exception:
        return "Puerto 7547 TR-069 no responde o está cerrado."


def parse_config_file(content_str):
    """Analiza estáticamente archivos .xml o .cfg en busca de datos sensibles y configuraciones inseguras."""
    findings = []
    patterns = [
        (r'<Password[^>]*>(.*?)</Password>', "Contraseña en etiqueta XML <Password>"),
        (r'<AdminPassword[^>]*>(.*?)</AdminPassword>', "Contraseña Administrador"),
        (r'wpa_passphrase=(.*)', "Clave WPA Wi-Fi en texto plano"),
        (r'PreSharedKey=(.*)', "PreSharedKey Wi-Fi en texto plano"),
        (r'-----BEGIN PRIVATE KEY-----', "❌ LLAVE PRIVADA RSA EXPUESTA EN TEXTO PLANO"),
        (r'<WPS_Enable>1</WPS_Enable>|wps_enable=1', "⚠️ WPS Activo (Recomienda deshabilitar por PIN brute-force)"),
        (r'WEP|WPA-TKIP', "⚠️ Algoritmo Cifrado Wi-Fi Obsoleto (WEP/TKIP)"),
        (r'username=["\']?(admin|support|root|telecomadmin)["\']?', "Cuenta técnica o de mantenimiento detectada")
    ]

    for pattern, label in patterns:
        matches = re.findall(pattern, content_str, re.IGNORECASE)
        for match in matches:
            val = match if isinstance(match, str) else match[0] if isinstance(match, tuple) else ""
            if val.strip() and val.strip() not in ["***", "******"]:
                findings.append(f"{label}: '{val.strip()}'")

    return findings if findings else ["No se detectaron contraseñas ni llaves expuestas evidentes."]


# --- PLANTILLA WEB HTML BOOTSTRAP 5 CON GUI, MODALES Y OVERLAY DE CARGA ---

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Homologación - Suite de Diagnóstico v4.0</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <style>
        body { background-color: #0f172a; color: #f8fafc; font-family: system-ui, -apple-system, sans-serif; }
        .card-custom { border-radius: 10px; border: 1px solid #334155; background-color: #1e293b; color: #f8fafc; }
        .table { color: #f8fafc; }
        .table-hover tbody tr:hover { background-color: #334155; color: #fff; }
        .pass-badge { color: #10b981; font-weight: bold; }
        .fail-badge { color: #ef4444; font-weight: bold; }
        .version-code { font-family: monospace; font-size: 0.85rem; color: #38bdf8; font-weight: 600; }
        .warning-badge { color: #f59e0b; font-weight: bold; }
        .modal-content { background-color: #1e293b; color: #f8fafc; border: 1px solid #334155; }

        /* Loading Overlay Style */
        #loadingOverlay {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(15, 23, 42, 0.93);
            z-index: 9999;
            align-items: center;
            justify-content: center;
            flex-direction: column;
            backdrop-filter: blur(4px);
        }
    </style>
</head>
<body class="p-4">

    <!-- PANTALLA FLOTANTE DE CARGA CON CIRCULO Y TEMPORIZADOR -->
    <div id="loadingOverlay">
        <div class="spinner-border text-info mb-3" style="width: 4.5rem; height: 4.5rem; border-width: 0.35em;" role="status"></div>
        <h4 class="fw-bold text-light mb-2">🔍 Analizando equipo...</h4>
        <p class="text-secondary mb-3 text-center" style="max-width: 500px;">
            Escaneando 65,535 puertos TCP, verificando diccionario SNMP, DNS Open Resolver, TR-069 e inspección Web cURL.
        </p>
        <div class="badge bg-dark border border-secondary px-4 py-2 fs-3 font-monospace text-warning fw-bold shadow" id="scanTimer">00:00</div>
    </div>

    <div class="container" style="max-width: 1150px;">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <div>
                <h3 class="fw-bold mb-1 text-info">🛠️ Laboratorio de Homologación v4.0</h3>
                <p class="text-secondary mb-0">Escaneo Completo (1-65535), Auditoría SNMP/DNS, Web Vulnerabilities & TR-069</p>
            </div>
            <span class="badge bg-primary fs-6">Escaneo Multihilo Avanzado (1-65,535)</span>
        </div>

        <!-- Formulario 1: Auditoría de Red -->
        <div class="card card-custom shadow-sm mb-4">
            <div class="card-header bg-dark text-white fw-bold border-secondary">1. Escaneo Completo de Puertos (1-65535) & Auditoría Global de Seguridad</div>
            <div class="card-body">
                <form id="scanForm" method="POST">
                    <input type="hidden" name="action" value="scan_network">
                    <div class="row g-3 mb-3">
                        <div class="col-md-4">
                            <label class="form-label fw-bold small text-secondary">IP Objetivo:</label>
                            <input type="text" name="ip" class="form-control bg-dark text-white border-secondary" placeholder="Ej: 192.168.35.1" value="{{ target_ip or '' }}" required>
                        </div>
                        <div class="col-md-4">
                            <label class="form-label fw-bold small text-secondary">Modelo del Equipo:</label>
                            <input type="text" name="model" class="form-control bg-dark text-white border-secondary" placeholder="Ej: FAST3890 / F@st 5670" value="{{ target_model or '' }}">
                        </div>
                        <div class="col-md-4">
                            <label class="form-label fw-bold small text-secondary">Versión Firmware:</label>
                            <input type="text" name="firmware" class="form-control bg-dark text-white border-secondary" placeholder="Ej: v1.2.3_TP" value="{{ target_fw or '' }}">
                        </div>
                    </div>
                    <button type="submit" class="btn btn-primary fw-bold w-100 py-2">🚀 Ejecutar Auditoría Completa y Guardar en BD</button>
                </form>
            </div>
        </div>

        <!-- Formulario 2: Auditoría de Configuración -->
        <div class="card card-custom shadow-sm mb-4">
            <div class="card-header bg-secondary text-white fw-bold">2. Auditoría Estática de Archivo de Backup (.xml / .cfg)</div>
            <div class="card-body">
                <form method="POST" enctype="multipart/form-data">
                    <input type="hidden" name="action" value="scan_config">
                    <div class="input-group">
                        <input type="file" name="cfg_file" class="form-control bg-dark text-white border-secondary" required>
                        <button type="submit" class="btn btn-outline-light fw-bold">Analizar Archivo</button>
                    </div>
                </form>
            </div>
        </div>

        <!-- Resultados del Diagnóstico Actual -->
        {% if net_results %}
        <div class="card card-custom shadow-sm mb-4 border-info">
            <div class="card-header bg-info text-dark fw-bold d-flex justify-content-between align-items-center">
                <span>Resultados de Diagnóstico — {{ target_ip }} (Escaneo 1-65535)</span>
                <span class="badge bg-dark text-white">Guardado en BD</span>
            </div>
            <div class="card-body">
                <div class="row mb-3 bg-dark p-3 rounded mx-0 border border-secondary">
                    <div class="col-md-4"><strong>Modelo:</strong> {{ target_model or 'N/A' }}</div>
                    <div class="col-md-4"><strong>Firmware:</strong> {{ target_fw or 'N/A' }}</div>
                    <div class="col-md-4"><strong>Total Puertos Abiertos:</strong> <span class="badge bg-primary fs-6">{{ net_results.total_open }}</span></div>
                </div>

                <h5 class="fw-bold mb-3 text-info">Puertos & Servicios Detectados</h5>
                <table class="table table-dark table-hover table-bordered align-middle mb-4 border-secondary">
                    <thead>
                        <tr class="table-secondary text-dark">
                            <th>Puerto / Servicio</th>
                            <th>Estado</th>
                            <th>Versión / Banner Detectado</th>
                            <th>Evaluación Normativa</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for item in net_results.services %}
                        <tr>
                            <td><strong>{{ item.name }}</strong></td>
                            <td><span class="pass-badge">{{ item.status }}</span></td>
                            <td><code class="version-code">{{ item.version }}</code></td>
                            <td>
                                {% if 'Telnet' in item.name and item.status == 'Abierto' %}
                                    <span class="fail-badge">❌ Telnet debe estar cerrado en producción.</span>
                                {% elif 'UPnP' in item.name and item.status == 'Abierto' %}
                                    <span class="fail-badge">⚠️ UPnP activo (Verificar aislamiento WAN).</span>
                                {% elif item.is_unauthorized %}
                                    <span class="warning-badge">⚠️ Puerto No Estándar Detectado (Revisar depuración).</span>
                                {% else %}
                                    <span class="text-success small">Conforme a perfil.</span>
                                {% endif %}
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>

                <h5 class="fw-bold mb-2 text-info">📡 Protocolos Específicos & Infraestructura (SNMP / DNS / TR-069)</h5>
                <ul class="list-group mb-4">
                    <li class="list-group-item bg-dark text-light border-secondary"><strong>SNMP (UDP 161):</strong> {{ net_results.snmp_audit }}</li>
                    <li class="list-group-item bg-dark text-light border-secondary"><strong>DNS (UDP 53):</strong> {{ net_results.dns_audit }}</li>
                    <li class="list-group-item bg-dark text-light border-secondary"><strong>TR-069 (TCP 7547):</strong> {{ net_results.tr069_audit }}</li>
                </ul>

                <h5 class="fw-bold mb-2 text-info">🌐 Inspección cURL & Vulnerabilidades Web (Puertos 80 / 443)</h5>
                <pre class="bg-dark text-success p-3 border border-secondary rounded font-monospace">{{ net_results.web_audit | tojson(indent=2) }}</pre>

                <h5 class="fw-bold mb-2 text-info">Auditoría TLS & Certificado (Puerto 443)</h5>
                <pre class="bg-dark text-warning p-3 border border-secondary rounded font-monospace">{{ net_results.tls | tojson(indent=2) }}</pre>
            </div>
        </div>
        {% endif %}

        <!-- Resultados de Configuración -->
        {% if cfg_results %}
        <div class="card card-custom shadow-sm mb-4 border-warning">
            <div class="card-header bg-warning text-dark fw-bold">Hallazgos en Archivo de Configuración</div>
            <div class="card-body">
                <ul>
                    {% for item in cfg_results %}
                    <li>{{ item }}</li>
                    {% endfor %}
                </ul>
            </div>
        </div>
        {% endif %}

        <!-- Tabla Historial de Base de Datos -->
        <div class="card card-custom shadow-sm mb-4">
            <div class="card-header bg-dark text-white fw-bold d-flex justify-content-between align-items-center border-secondary">
                <span>📜 Historial de Auditorías Guardadas en Base de Datos</span>
                <form method="POST" onsubmit="return confirm('¿Confirma que desea borrar TODO el historial de la base de datos?');" class="mb-0">
                    <input type="hidden" name="action" value="clear_history">
                    <button type="submit" class="btn btn-sm btn-outline-danger font-monospace fw-bold">🧹 Borrar Historial</button>
                </form>
            </div>
            <div class="card-body p-0 table-responsive" style="max-height: 400px;">
                <table class="table table-dark table-hover align-middle mb-0">
                    <thead>
                        <tr class="table-secondary text-dark">
                            <th>Fecha / Hora</th>
                            <th>IP</th>
                            <th>Modelo</th>
                            <th>Firmware</th>
                            <th>Modo</th>
                            <th>Resumen</th>
                            <th class="text-end">Acciones</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% if history %}
                            {% for log in history %}
                            <tr>
                                <td class="small text-secondary">{{ log.timestamp }}</td>
                                <td class="fw-bold font-monospace">{{ log.ip_address }}</td>
                                <td>{{ log.model_name }}</td>
                                <td><code>{{ log.firmware_version }}</code></td>
                                <td><span class="badge bg-primary">{{ log.scan_type }}</span></td>
                                <td class="small">
                                    <span class="badge bg-info text-dark">Abiertos: {{ log.details.total_open or 0 }}</span>
                                </td>
                                <td class="text-end">
                                    <button type="button" class="btn btn-sm btn-outline-info me-1" onclick="cargarYVerDetalles({{ log.id }})">🔍 Ver Detalles</button>
                                    <a href="/download_report/{{ log.id }}" class="btn btn-sm btn-success fw-bold me-1">📥 Descargar Informe</a>
                                    <form action="/delete_log/{{ log.id }}" method="POST" class="d-inline" onsubmit="return confirm('¿Confirma que desea eliminar este registro individual?');">
                                        <button type="submit" class="btn btn-sm btn-outline-danger font-monospace">🗑️ Eliminar</button>
                                    </form>
                                </td>
                            </tr>
                            {% endfor %}
                        {% else %}
                            <tr><td colspan="7" class="text-center py-4 text-secondary">No hay auditorías registradas en la base de datos.</td></tr>
                        {% endif %}
                    </tbody>
                </table>
            </div>
        </div>

    </div>

    <!-- MODAL ESTILIZADO CON TABLAS BONITAS PARA DETALLES -->
    <div class="modal fade" id="detailsModal" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered modal-xl">
            <div class="modal-content">
                <div class="modal-header bg-dark text-white border-secondary">
                    <h5 class="modal-title fw-bold text-info">📋 Ficha Técnica y Detalles del Diagnóstico</h5>
                    <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body" id="modalFormattedBody">
                    <div class="text-center py-4 text-secondary">Cargando detalles completos desde la base de datos...</div>
                </div>
                <div class="modal-footer border-secondary">
                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cerrar</button>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        // Manejador del Formulario de Escaneo con Temporizador en Vivo
        document.getElementById('scanForm').addEventListener('submit', function() {
            document.getElementById('loadingOverlay').style.display = 'flex';
            let seconds = 0;
            let timerElem = document.getElementById('scanTimer');
            setInterval(() => {
                seconds++;
                let mins = String(Math.floor(seconds / 60)).padStart(2, '0');
                let secs = String(seconds % 60).padStart(2, '0');
                timerElem.textContent = `${mins}:${secs}`;
            }, 1000);
        });

        // Función para renderizar los detalles en tablas bonitas idénticas a la vista principal
        function cargarYVerDetalles(logId) {
            document.getElementById('modalFormattedBody').innerHTML = '<div class="text-center py-4 text-info"><div class="spinner-border spinner-border-sm me-2"></div>Cargando ficha técnica...</div>';
            var modal = new bootstrap.Modal(document.getElementById('detailsModal'));
            modal.show();

            fetch('/api/audit_log/' + logId)
                .then(res => res.json())
                .then(data => {
                    if (data && data.details) {
                        const dt = data.details;
                        const services = dt.services || [];

                        let servicesRows = services.map(s => {
                            let evalBadge = '<span class="text-success small">Conforme a perfil.</span>';
                            if (s.name.includes('Telnet') && s.status === 'Abierto') {
                                evalBadge = '<span class="fail-badge">❌ Telnet debe estar cerrado en producción.</span>';
                            } else if (s.name.includes('UPnP') && s.status === 'Abierto') {
                                evalBadge = '<span class="fail-badge">⚠️ UPnP activo (Verificar aislamiento WAN).</span>';
                            } else if (s.is_unauthorized) {
                                evalBadge = '<span class="warning-badge">⚠️ Puerto No Estándar Detectado (Revisar depuración).</span>';
                            }
                            return `
                                <tr>
                                    <td><strong>${s.name}</strong></td>
                                    <td><span class="pass-badge">${s.status}</span></td>
                                    <td><code class="version-code">${s.version}</code></td>
                                    <td>${evalBadge}</td>
                                </tr>
                            `;
                        }).join('');

                        if (!servicesRows) {
                            servicesRows = '<tr><td colspan="4" class="text-center text-secondary">No se detectaron puertos TCP abiertos.</td></tr>';
                        }

                        const htmlContent = `
                            <div class="row mb-3 bg-dark p-3 rounded mx-0 border border-secondary">
                                <div class="col-md-3"><strong>IP:</strong> <span class="font-monospace text-info">${data.ip_address}</span></div>
                                <div class="col-md-3"><strong>Modelo:</strong> ${data.model_name || 'N/A'}</div>
                                <div class="col-md-3"><strong>Firmware:</strong> <code>${data.firmware_version || 'N/A'}</code></div>
                                <div class="col-md-3"><strong>Total Puertos Abiertos:</strong> <span class="badge bg-primary fs-6">${dt.total_open || 0}</span></div>
                            </div>

                            <h5 class="fw-bold mb-3 text-info">Puertos & Servicios Detectados</h5>
                            <table class="table table-dark table-hover table-bordered align-middle mb-4 border-secondary">
                                <thead>
                                    <tr class="table-secondary text-dark">
                                        <th>Puerto / Servicio</th>
                                        <th>Estado</th>
                                        <th>Versión / Banner Detectado</th>
                                        <th>Evaluación Normativa</th>
                                    </tr>
                                </thead>
                                <tbody>${servicesRows}</tbody>
                            </table>

                            <h5 class="fw-bold mb-2 text-info">📡 Protocolos Específicos & Infraestructura</h5>
                            <ul class="list-group mb-4">
                                <li class="list-group-item bg-dark text-light border-secondary"><strong>SNMP (UDP 161):</strong> ${dt.snmp_audit || 'N/A'}</li>
                                <li class="list-group-item bg-dark text-light border-secondary"><strong>DNS (UDP 53):</strong> ${dt.dns_audit || 'N/A'}</li>
                                <li class="list-group-item bg-dark text-light border-secondary"><strong>TR-069 (TCP 7547):</strong> ${dt.tr069_audit || 'N/A'}</li>
                            </ul>

                            <h5 class="fw-bold mb-2 text-info">🌐 Inspección cURL & Vulnerabilidades Web (Puertos 80 / 443)</h5>
                            <pre class="bg-dark text-success p-3 border border-secondary rounded font-monospace">${JSON.stringify(dt.web_audit || {}, null, 2)}</pre>

                            <h5 class="fw-bold mb-2 text-info">Auditoría TLS & Certificado (Puerto 443)</h5>
                            <pre class="bg-dark text-warning p-3 border border-secondary rounded font-monospace">${JSON.stringify(dt.tls || {}, null, 2)}</pre>

                            <div class="mt-3">
                                <button class="btn btn-sm btn-outline-secondary font-monospace" type="button" data-bs-toggle="collapse" data-bs-target="#rawJsonCollapse">
                                    📄 Ver Estructura JSON Cruda
                                </button>
                                <div class="collapse mt-2" id="rawJsonCollapse">
                                    <pre class="bg-dark text-light p-3 border border-secondary rounded font-monospace small" style="max-height: 300px; overflow-y: auto;">${JSON.stringify(dt, null, 2)}</pre>
                                </div>
                            </div>
                        `;

                        document.getElementById('modalFormattedBody').innerHTML = htmlContent;
                    } else {
                        document.getElementById('modalFormattedBody').innerHTML = '<div class="text-danger p-3">No se encontraron detalles válidos para este diagnóstico.</div>';
                    }
                })
                .catch(err => {
                    document.getElementById('modalFormattedBody').innerHTML = '<div class="text-danger p-3">Error al cargar la información: ' + err + '</div>';
                });
        }
    </script>
</body>
</html>
"""


@app.route('/', methods=['GET', 'POST'])
def index():
    net_results = None
    cfg_results = None
    target_ip = None
    target_model = None
    target_fw = None

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'scan_network':
            target_ip = request.form.get('ip', '').strip()
            target_model = request.form.get('model', '').strip() or 'No especificado'
            target_fw = request.form.get('firmware', '').strip() or 'No especificado'
            scan_mode = 'Completo (1-65535)'

            services_summary = []
            has_https = False

            raw_open_ports = scan_full_range(target_ip, start_port=1, end_port=65535, max_threads=150)

            for p in raw_open_ports:
                label = STANDARD_PORTS.get(p, f"Puerto Alto/No Estándar ({p})")
                banner = get_service_banner(target_ip, p)

                if p == 443:
                    has_https = True

                services_summary.append({
                    "name": label,
                    "status": "Abierto",
                    "version": banner,
                    "is_unauthorized": (p not in STANDARD_PORTS)
                })

            tls_data = audit_tls_and_cert(target_ip) if has_https else "Puerto 443 cerrado"
            web_audit = audit_web_vulnerabilities_and_html(target_ip)
            snmp_audit = audit_snmp_communities(target_ip)
            dns_audit = audit_dns_open_resolver(target_ip)
            tr069_audit = audit_tr069_cwmp(target_ip)

            open_count = len(services_summary)

            net_results = {
                "total_open": open_count,
                "services": services_summary,
                "snmp_audit": snmp_audit,
                "dns_audit": dns_audit,
                "tr069_audit": tr069_audit,
                "web_audit": web_audit,
                "tls": tls_data
            }

            log_id = save_audit_log(target_ip, target_model, target_fw, scan_mode, net_results)
            return redirect(url_for('index', log_id=log_id))

        elif action == 'scan_config':
            file = request.files.get('cfg_file')
            if file:
                content = file.read().decode('utf-8', errors='ignore')
                cfg_results = parse_config_file(content)

        elif action == 'clear_history':
            clear_audit_history()
            return redirect(url_for('index'))

    log_id = request.args.get('log_id', type=int)
    if log_id:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM audit_logs WHERE id = ?", (log_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            log_dict = dict(row)
            target_ip = log_dict['ip_address']
            target_model = log_dict['model_name']
            target_fw = log_dict['firmware_version']
            try:
                net_results = json.loads(log_dict['results_json'])
            except Exception:
                net_results = None

    history = get_audit_history()

    return render_template_string(
        HTML_TEMPLATE,
        net_results=net_results,
        cfg_results=cfg_results,
        target_ip=target_ip,
        target_model=target_model,
        target_fw=target_fw,
        history=history
    )


@app.route('/delete_log/<int:log_id>', methods=['POST'])
def delete_log(log_id):
    """Ruta para eliminar un registro individual del historial."""
    delete_single_audit_log(log_id)
    return redirect(url_for('index'))


@app.route('/api/audit_log/<int:log_id>')
def api_audit_log(log_id):
    """API Endpoint que retorna los detalles completos JSON de un registro específico."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM audit_logs WHERE id = ?", (log_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "No encontrado"}), 404

    log_data = dict(row)
    try:
        log_data['details'] = json.loads(log_data['results_json'])
    except Exception:
        log_data['details'] = {}

    return jsonify(log_data)


@app.route('/download_report/<int:log_id>')
def download_report(log_id):
    """Genera y descarga un archivo de texto formateado con el informe completo de la auditoría."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM audit_logs WHERE id = ?", (log_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return "Informe no encontrado en la base de datos", 404

    log_data = dict(row)
    try:
        details = json.loads(log_data['results_json'])
    except Exception:
        details = log_data['results_json']

    report_content = f"""========================================================================
LABORATORIO DE HOMOLOGACIÓN - INFORME TÉCNICO DE AUDITORÍA DE SEGURIDAD
========================================================================
ID Registro:        #{log_data['id']}
Fecha / Hora:       {log_data['timestamp']}
Dirección IP:       {log_data['ip_address']}
Modelo Dispositivo: {log_data['model_name']}
Versión Firmware:   {log_data['firmware_version']}
Tipo de Escaneo:    {log_data['scan_type']}
========================================================================

[RESUMEN DE RESULTADOS EN FORMATO ESTRUCTURADO JSON]
{json.dumps(details, indent=4, ensure_ascii=False)}

========================================================================
Fin del Informe de Homologación v4.0
========================================================================
"""
    filename = f"informe_auditoria_{log_data['ip_address'].replace('.', '_')}_{log_data['id']}.txt"

    return Response(
        report_content,
        mimetype="text/plain; charset=utf-8",
        headers={"Content-disposition": f"attachment; filename={filename}"}
    )


if __name__ == '__main__':
    print("Iniciando Suite de Diagnóstico v4.0 en puerto 9036...")
    app.run(host='0.0.0.0', port=9036, debug=True)