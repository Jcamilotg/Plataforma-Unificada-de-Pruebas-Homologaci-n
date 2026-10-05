# Iniciar con: sudo python3 /home/sagemcom/script/docsis_provisioner.py &
#instalar docsis:
# sudo apt-get install -y libglib2.0-dev libglib2.0-dev-bin gettext
#apt-get install docsis
#sudo apt-get install -y libsnmp-dev
#sudo apt-get update
'''cd /tmp/docsis_src
./autogen.sh
./configure
make
sudo make install'''

import os
import re
import sqlite3
import subprocess
import shutil
from datetime import datetime
from flask import Flask, request, jsonify, Response, send_from_directory

app = Flask(__name__)

# --- RUTAS ABSOLUTAS Y BD ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DHCP_CONF_PATH = "/etc/dhcp/dhcpd.conf"
TFTP_DIR = "/srv/tftp"
DB_PATH = os.path.join(BASE_DIR, "docsis_provisioning.db")

os.makedirs(TFTP_DIR, exist_ok=True)

TEMPLATE_BASICO_TXT = """Main 
{
	NetworkAccess 1;
	GlobalPrivacyEnable 1;
	BaselinePrivacy
	{
		AuthTimeout 10;
		ReAuthTimeout 10;
		AuthGraceTime 600;
		OperTimeout 10;
		ReKeyTimeout 10;
		TEKGraceTime 3600;
		AuthRejectTimeout 60;
		SAMapWaitTimeout 1;
		SAMapMaxRetries 4;
	}
	SnmpMibObject iso.3.6.1.4.1.4413.2.2.2.1.9.1.2.1.0 Integer 2;
	MaxCPE 5;
	UsServiceFlow
	{
		UsServiceFlowRef 3;
		QosParamSetType 7;
		TrafficPriority 0;
		MaxRateSustained 2000000000;
		MaxTrafficBurst 20000000;
		MaxConcatenatedBurst 65000;
		SchedulingType 2;
	}
	DsServiceFlow
	{
		DsServiceFlowRef 1;
		QosParamSetType 7;
		TrafficPriority 0;
		MaxRateSustained 2000000000;
		MaxTrafficBurst 20000000;
	}
	SnmpMibObject iso.3.6.1.2.1.69.1.2.1.2.1 IPAddress 0.0.0.0;
	SnmpMibObject iso.3.6.1.2.1.69.1.2.1.3.1 IPAddress 0.0.0.0;
	SnmpMibObject iso.3.6.1.2.1.69.1.2.1.4.1 String "private";
	SnmpMibObject iso.3.6.1.2.1.69.1.2.1.5.1 Integer 3;
	SnmpMibObject iso.3.6.1.2.1.69.1.2.1.6.1 String "@";
	SnmpMibObject iso.3.6.1.2.1.69.1.2.1.7.1 Integer 4;
	MfgCVCData 0x3082037f30820267a00302010202103a528e551c3357efa8d6380a1619c49e300d06092a864886f70d0101050500306f310b3009060355040613024245311f301d060355040a131674436f6d4c616273202d204575726f2d444f4353495331153013060355040b130c4361626c65204d6f64656d73312830260603550403131f4575726f2d444f43534953204361626c65204d6f64656d20526f6f74204341301e170d3131303132363030303030305a170d3330303932323233353935395a306c310b3009060355040613024652311f301d060355040a1316534147454d434f4d2042726f616462616e642053415331143012060355040b130b4575726f;
	MfgCVCData 0x2d444f43534953312630240603550403131d436f646520566572696669636174696f6e20436572746966696361746530820122300d06092a864886f70d01010105000382010f003082010a0282010100dda0fc7aaacca49877435116c2cc9ca1bfb0428d7be3f8cd41af54700fa636cb09ff3e71ff28d05c29a3d724bfa7c1c47f7a79fff1db038d48c79b9a07decbf6a0fad1814238841e28944e8b7231e63d61ac57afe6ac83e3caec8ebe3e46f392d3fa4c3139d60c695c65e858d0ea24945a354116b9902921c10e04e00b51c38c50ae04ab958f3a1e934aa82a997976585c98d3c569c4653e7ddd63300e8401ff648067652bb24d981aaebdba01f3;
	MfgCVCData 0xe340f2c2bc5bd279767e2b5252dc724f6402d83cba1af9a7316da4c58ad880ffbabcd1032594c61b68dfd978fa6a2f1d9b63ff4d36aa776ba34a704bab6623ff45f42a6f984033e20134789a16cc4fa731e30203010001a31a301830160603551d250101ff040c300a06082b06010505070303300d06092a864886f70d010105050003820101007ee4949fecde994e1e5a4ec4a0a916bbbb639e7ac5c2926d80faa9c926008953b4af7e6ef2513d8d78a53bff85ab575316c62136bb2b325ad3ec27f9ae36f4437bb0da46e51273297fbd1449abfb89d47a59bcfea08c3475c54a30991152cfaa61927a87b0ed7db0551e0247410683949a554495501808;
	MfgCVCData 0x8940da44e75746715d74b80d92c74eecaedc4b66631847304555bf624530eb20c5beb8e9aa8d9e8659a8befe2158a65c6586cf874110970fc01aa1f682c32b6a4751f042a7ead0974552430f7b1dff05e3c18e7db2c2c648341170d37b5d17eb312029e0fd9c67ac1b45ce537bdc9fb7cde537aaf7499635409e049b24b25d5d90f79f802b15d6cdcf;
	/* CmMic 3c2aa4c8e2f14489e5d3598ff498b5c5; */
	/* CmtsMic 88d970585b85d0fc346e19e48bc087f7; */
	/*EndOfDataMkr*/
	/* Pad */
}
"""

TEMPLATE_TELEFONIA_TXT = """Main 
{
	MtaConfigDelimiter 1;
	SnmpMibObject iso.3.6.1.4.1.4491.2.2.1.1.1.7.0 Integer 1;
	SnmpMibObject iso.3.6.1.4.1.4491.2.2.2.1.2.1.1.1.9 String "lab.net";
	SnmpMibObject iso.3.6.1.4.1.4491.2.2.2.1.2.1.1.2.9 Integer 2727;
	SnmpMibObject iso.3.6.1.4.1.4491.2.2.2.1.2.1.1.1.10 String "lab.net";
	SnmpMibObject iso.3.6.1.4.1.4491.2.2.2.1.2.1.1.2.10 Integer 2727;
	SnmpMibObject iso.3.6.1.4.1.4491.2.2.2.1.1.10.0 Integer 1;
	SnmpMibObject iso.3.6.1.4.1.4491.2.2.2.1.1.9.0 Integer 46;
	SnmpMibObject iso.3.6.1.4.1.4491.2.2.2.1.1.12.0 Integer 2427;
	SnmpMibObject iso.3.6.1.4.1.4491.2.2.2.1.1.8.0 Integer 24;
	SnmpMibObject iso.3.6.1.4.1.4491.2.2.2.1.2.1.1.18.9 Integer 10;
	SnmpMibObject iso.3.6.1.4.1.4491.2.2.2.1.2.1.1.18.10 Integer 10;
	SnmpMibObject iso.3.6.1.4.1.4491.2.2.1.1.3.16.1.4.108.97.98.46.110.101.116 String "lab.net";
	SnmpMibObject iso.3.6.1.4.1.4491.2.2.1.1.3.17.1.2.108.97.98.46.110.101.116 String "lab.net";
	SnmpMibObject iso.3.6.1.4.1.4491.2.2.1.1.3.17.1.10.108.97.98.46.110.101.116 Integer 2;
	SnmpMibObject iso.3.6.1.4.1.4413.2.2.2.1.6.1.19.0 Integer 1;
	SnmpMibObject iso.3.6.1.6.3.18.1.1.1.2.97.100.109.105.110 String "private";
	SnmpMibObject iso.3.6.1.6.3.18.1.1.1.2.111.112.101.114.97.116.111.114 String "private";
	MtaConfigDelimiter 255;
}
"""


def format_mac(mac_str):
    clean = re.sub(r'[^a-fA-F0-9]', '', mac_str).lower()
    if len(clean) == 12:
        return ":".join([clean[i:i + 2] for i in range(0, 12, 2)])
    return mac_str.lower()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS dhcp_hosts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            host_name TEXT UNIQUE NOT NULL,
            mac_address TEXT NOT NULL,
            fixed_ip TEXT NOT NULL,
            device_type TEXT NOT NULL,
            bootfile TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS docsis_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT UNIQUE NOT NULL,
            raw_text TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Insertar o actualizar plantilla básica
    cursor.execute("SELECT id FROM docsis_templates WHERE filename='plantilla.cfg.txt'")
    row = cursor.fetchone()
    if row:
        cursor.execute('''
            UPDATE docsis_templates 
            SET raw_text = ?, updated_at = CURRENT_TIMESTAMP 
            WHERE filename = 'plantilla.cfg.txt'
        ''', (TEMPLATE_BASICO_TXT,))
    else:
        cursor.execute('''
            INSERT INTO docsis_templates (filename, raw_text) 
            VALUES (?, ?)
        ''', ("plantilla.cfg.txt", TEMPLATE_BASICO_TXT))

    # Insertar o actualizar plantilla telefonía
    cursor.execute("SELECT id FROM docsis_templates WHERE filename='telefonia.bin.txt'")
    row_tel = cursor.fetchone()
    if row_tel:
        cursor.execute('''
            UPDATE docsis_templates 
            SET raw_text = ?, updated_at = CURRENT_TIMESTAMP 
            WHERE filename = 'telefonia.bin.txt'
        ''', (TEMPLATE_TELEFONIA_TXT,))
    else:
        cursor.execute('''
            INSERT INTO docsis_templates (filename, raw_text) 
            VALUES (?, ?)
        ''', ("telefonia.bin.txt", TEMPLATE_TELEFONIA_TXT))

    conn.commit()
    conn.close()


def sync_dhcp_file():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM dhcp_hosts ORDER BY id ASC")
    hosts = cursor.fetchall()
    conn.close()

    hosts_emta = []
    hosts_cm = []
    hosts_erouter = []

    for h in hosts:
        host_str = f'    host {h["host_name"]} {{\n'
        host_str += f'        hardware ethernet {h["mac_address"]};\n'
        host_str += f'        fixed-address {h["fixed_ip"]};\n'
        host_str += f'        filename "{h["bootfile"]}";\n'
        if h["device_type"] == "CM":
            host_str += '        option dhcp-parameter-request-list 2,3,4,7,58,122;\n'
            host_str += '        option vendor-class-identifier "DHCP-server";\n'
        host_str += '    }\n'

        if h["device_type"] == "EMTA":
            hosts_emta.append(host_str)
        elif h["device_type"] == "CM":
            hosts_cm.append(host_str)
        elif h["device_type"] == "eRouter":
            hosts_erouter.append(host_str)

    content = ""
    if os.path.exists(DHCP_CONF_PATH):
        with open(DHCP_CONF_PATH, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

    # Limpiar bloques anteriores
    content = re.sub(r'# BEGIN AUTOGEN_EMTA.*?# END AUTOGEN_EMTA', '', content, flags=re.DOTALL)
    content = re.sub(r'# BEGIN AUTOGEN_CM.*?# END AUTOGEN_CM', '', content, flags=re.DOTALL)
    content = re.sub(r'# BEGIN AUTOGEN_EROUTER.*?# END AUTOGEN_EROUTER', '', content, flags=re.DOTALL)
    content = re.sub(r'# BEGIN SAGEMCOM_AUTOGEN_HOSTS.*?# END SAGEMCOM_AUTOGEN_HOSTS', '', content, flags=re.DOTALL)

    block_emta = "# BEGIN AUTOGEN_EMTA\n" + "".join(hosts_emta) + "    # END AUTOGEN_EMTA"
    block_cm = "# BEGIN AUTOGEN_CM\n" + "".join(hosts_cm) + "    # END AUTOGEN_CM"
    block_erouter = "# BEGIN AUTOGEN_EROUTER\n" + "".join(hosts_erouter) + "    # END AUTOGEN_EROUTER"

    # Inyectar DENTRO del pool de la subred 172.16.8.0 (EMTA)
    content = re.sub(
        r'(subnet 172\.16\.8\.0 netmask 255\.255\.255\.0\s*\{[^}]*pool\s*\{)(.*?)(\s*\})(\s*\})',
        r'\1\2\n    ' + block_emta + r'\n\3\4',
        content,
        flags=re.DOTALL
    )

    # Inyectar DENTRO del pool de la subred 172.16.6.0 (CM)
    content = re.sub(
        r'(subnet 172\.16\.6\.0 netmask 255\.255\.255\.0\s*\{[^}]*pool\s*\{)(.*?)(\s*\})(\s*\})',
        r'\1\2\n    ' + block_cm + r'\n\3\4',
        content,
        flags=re.DOTALL
    )

    # Inyectar DENTRO del primer pool de la subred 172.16.7.0 (eRouter)
    content = re.sub(
        r'(subnet 172\.16\.7\.0 netmask 255\.255\.255\.0\s*\{[^}]*pool\s*\{)(.*?)(\s*\})(\s*pool|\s*\})',
        r'\1\2\n    ' + block_erouter + r'\n\3\4',
        content,
        flags=re.DOTALL
    )

    with open(DHCP_CONF_PATH, "w", encoding="utf-8") as f:
        f.write(content)


def reload_dhcp_server():
    try:
        test_cmd = ["dhcpd", "-t", "-cf", DHCP_CONF_PATH]
        res_test = subprocess.run(test_cmd, capture_output=True, text=True)

        if res_test.returncode != 0 and "DHCPDISCOVER" not in res_test.stderr:
            return False, f"Error de sintaxis en DHCP: {res_test.stderr.strip()}"

        restart_cmd = ["systemctl", "restart", "isc-dhcp-server"]
        res_restart = subprocess.run(restart_cmd, capture_output=True, text=True)
        if res_restart.returncode == 0:
            return True, "DHCP reiniciado correctamente y sincronizado."
        else:
            return False, f"Fallo al reiniciar isc-dhcp-server: {res_restart.stderr.strip()}"
    except Exception as e:
        return False, f"Excepción ejecutando reload DHCP: {e}"


init_db()

# --- HTML / JS INTERFAZ ---
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>DOCSIS & eMTA Provisioner - Port 9037</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css">
    <style>
        body { background: #0f172a; color: #f8fafc; font-family: system-ui, -apple-system, sans-serif; }
        .card-custom { background: #1e293b; border: 1px solid #334155; border-radius: 12px; }
        .nav-tabs .nav-link { color: #94a3b8; border: none; font-weight: 600; }
        .nav-tabs .nav-link.active { color: #38bdf8; background: #1e293b; border-bottom: 3px solid #38bdf8; }
        .table { color: #f8fafc; }
        .badge-emta { background-color: #d97706; color: white; }
        .badge-cm { background-color: #0284c7; color: white; }
        .badge-erouter { background-color: #16a34a; color: white; }
        .code-editor { font-family: 'Consolas', monospace; background: #020617; color: #38bdf8; border: 1px solid #334155; border-radius: 8px; font-size: 0.88rem; }
    </style>
</head>
<body class="p-4">

    <div class="container" style="max-width: 1250px;">

        <div class="d-flex justify-content-between align-items-center mb-4">
            <div>
                <h3 class="fw-bold text-info mb-1"><i class="bi bi-cpu-fill me-2"></i>DOCSIS & eMTA Provisioner Suite</h3>
                <p class="text-secondary mb-0">Gestor de Reservas DHCP, Compilador DOCSIS (docsis -p / -e) y Repositorio TFTP (/srv/tftp)</p>
            </div>
            <div>
                <span class="badge bg-dark border border-secondary px-3 py-2 text-warning font-monospace fs-6">Puerto: 9037</span>
            </div>
        </div>

        <ul class="nav nav-tabs mb-4 border-secondary" id="mainTabs" role="tablist">
            <li class="nav-item">
                <button class="nav-link active" id="hosts-tab" data-bs-toggle="tab" data-bs-target="#hosts-pane" onclick="cargarHosts()"><i class="bi bi-hdd-network me-2"></i>Reservas DHCP (MACs & IPs)</button>
            </li>
            <li class="nav-item">
                <button class="nav-link" id="compiler-tab" data-bs-toggle="tab" data-bs-target="#compiler-pane"><i class="bi bi-tools me-2"></i>Compilador DOCSIS (docsis -p / -e)</button>
            </li>
            <li class="nav-item">
                <button class="nav-link" id="tftp-tab" data-bs-toggle="tab" data-bs-target="#tftp-pane" onclick="cargarArchivosTFTP()"><i class="bi bi-folder-fill me-2"></i>Archivos TFTP (/srv/tftp)</button>
            </li>
            <li class="nav-item">
                <button class="nav-link" id="viewer-tab" data-bs-toggle="tab" data-bs-target="#viewer-pane" onclick="cargarListaArchivosViewer()"><i class="bi bi-file-earmark-text me-2"></i>Ver Contenido de Archivos TFTP</button>
            </li>
            <li class="nav-item">
                <button class="nav-link" id="raw-dhcp-tab" data-bs-toggle="tab" data-bs-target="#raw-dhcp-pane" onclick="cargarRawDHCP()"><i class="bi bi-file-earmark-code me-2"></i>Ver /etc/dhcp/dhcpd.conf</button>
            </li>
            <li class="nav-item">
                <button class="nav-link" id="dhcp-logs-tab" data-bs-toggle="tab" data-bs-target="#dhcp-logs-pane" onclick="cargarLogsDHCP()"><i class="bi bi-activity me-2"></i>Logs DHCP (Offers/Procesos)</button>
            </li>
        </ul>

        <div class="tab-content" id="mainTabsContent">

            <div class="tab-pane fade show active" id="hosts-pane">
                <div class="row g-4">
                    <div class="col-md-4">
                        <div class="card card-custom p-3 shadow-sm">
                            <h5 class="fw-bold text-info mb-3"><i class="bi bi-plus-circle me-2"></i>Agregar / Editar Dispositivo</h5>
                            <form id="hostForm">
                                <input type="hidden" id="host_id">
                                <div class="mb-3">
                                    <label class="form-label small text-secondary fw-bold">Nombre del Dispositivo / Modelo:</label>
                                    <input type="text" id="host_name" class="form-control bg-dark text-white border-secondary" placeholder="Ej: Sag3890-EMTA-2" required>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label small text-secondary fw-bold">Dirección MAC:</label>
                                    <input type="text" id="mac_address" class="form-control bg-dark text-white border-secondary font-monospace" placeholder="Ej: 5C:FA:25:B1:EF:FD" required>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label small text-secondary fw-bold">Dirección IP Fija:</label>
                                    <input type="text" id="fixed_ip" class="form-control bg-dark text-white border-secondary font-monospace" placeholder="Ej: 172.16.8.2" required>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label small text-secondary fw-bold">Tipo de Interfaz / Subred:</label>
                                    <select id="device_type" class="form-select bg-dark text-white border-secondary" required>
                                        <option value="EMTA">eMTA (VLAN 172.16.8.x)</option>
                                        <option value="CM">Cable Módem (VLAN 172.16.6.x)</option>
                                        <option value="eRouter">eRouter (VLAN 172.16.7.x)</option>
                                        <option value="GPON">GPON / MNGT (VLAN 10.1.0.x)</option>
                                    </select>
                                </div>
                                <div class="mb-3">
                                    <label class="form-label small text-secondary fw-bold">Bootfile / Config File (.cfg o .bin):</label>
                                    <input type="text" id="bootfile" class="form-control bg-dark text-white border-secondary font-monospace" placeholder="Ej: telefonia.bin o docsis.cfg" required>
                                </div>
                                <button type="submit" class="btn btn-primary fw-bold w-100 py-2 mb-2"><i class="bi bi-save me-1"></i>Guardar Dispositivo</button>
                                <button type="button" class="btn btn-outline-secondary w-100 btn-sm" onclick="limpiarFormHost()">Limpiar Campos</button>
                            </form>
                        </div>
                    </div>

                    <div class="col-md-8">
                        <div class="card card-custom p-3 shadow-sm">
                            <div class="d-flex justify-content-between align-items-center mb-3">
                                <h5 class="fw-bold text-info mb-0"><i class="bi bi-list-task me-2"></i>Dispositivos Aprovisionados en DHCP</h5>
                                <button class="btn btn-success fw-bold px-3 btn-sm" onclick="aplicarDHCP()"><i class="bi bi-arrow-repeat me-1"></i>Sincronizar y Reiniciar DHCP</button>
                            </div>
                            <div class="table-responsive" style="max-height: 500px;">
                                <table class="table table-dark table-hover align-middle mb-0 border-secondary">
                                    <thead>
                                        <tr class="table-secondary text-dark">
                                            <th>Tipo</th>
                                            <th>Nombre</th>
                                            <th>MAC</th>
                                            <th>IP Fija</th>
                                            <th>Bootfile</th>
                                            <th class="text-end">Acciones</th>
                                        </tr>
                                    </thead>
                                    <tbody id="hostsTableBody">
                                        <tr><td colspan="6" class="text-center py-4 text-secondary">Cargando reservas...</td></tr>
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="tab-pane fade" id="compiler-pane">
                <div class="card card-custom p-4 shadow-sm">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <h5 class="fw-bold text-info mb-0"><i class="bi bi-code-square me-2"></i>Compilador de Archivos de Configuración DOCSIS</h5>
                        <div class="d-flex gap-2 align-items-center">
                            <select id="select_template_selector" class="form-select form-select-sm bg-dark text-info border-secondary font-monospace" style="width: 220px;" onchange="cambiarPlantillaEditor(this.value)">
                                <option value="">Seleccionar Plantilla...</option>
                            </select>
                            <input type="text" id="output_filename" class="form-control form-control-sm bg-dark text-white border-secondary font-monospace" placeholder="Nombre salida (ej: telefonia.bin o sagem_tr69.cfg)" value="sagem_tr69.cfg" style="width: 200px;">
                            <button class="btn btn-warning fw-bold text-dark px-3 btn-sm text-nowrap" onclick="compilarDocsis()"><i class="bi bi-gear-fill me-1"></i>Compilador</button>
                        </div>
                    </div>
                    <textarea id="docsis_code" class="form-control code-editor p-3 mb-3" rows="18" spellcheck="false"></textarea>
                    <div id="compiler_console" class="p-3 bg-dark border border-secondary rounded font-monospace small text-warning" style="display: none;"></div>
                </div>
            </div>

            <div class="tab-pane fade" id="tftp-pane">
                <div class="card card-custom p-4 shadow-sm">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <h5 class="fw-bold text-info mb-0"><i class="bi bi-folder-check me-2"></i>Archivos Binarios en Servidor TFTP (/srv/tftp/)</h5>
                        <button class="btn btn-sm btn-outline-light" onclick="cargarArchivosTFTP()"><i class="bi bi-arrow-clockwise me-1"></i>Refrescar Lista</button>
                    </div>
                    <div class="table-responsive">
                        <table class="table table-dark table-hover align-middle mb-0 border-secondary">
                            <thead>
                                <tr class="table-secondary text-dark">
                                    <th>Nombre de Archivo</th>
                                    <th>Tamaño (Bytes)</th>
                                    <th>Última Modificación</th>
                                    <th class="text-end">Acciones</th>
                                </tr>
                            </thead>
                            <tbody id="tftpTableBody">
                                <tr><td colspan="4" class="text-center py-4 text-secondary">Cargando archivos en /srv/tftp...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <div class="tab-pane fade" id="viewer-pane">
                <div class="card card-custom p-4 shadow-sm">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <h5 class="fw-bold text-info mb-0"><i class="bi bi-file-text me-2"></i>Visor de Texto / Configuración en /srv/tftp/</h5>
                        <div class="d-flex gap-2 align-items-center">
                            <select id="select_tftp_file" class="form-select form-select-sm bg-dark text-white border-secondary font-monospace" style="width: 250px;" onchange="cargarContenidoArchivoViewer()">
                                <option value="">Seleccione un archivo...</option>
                            </select>
                            <button class="btn btn-sm btn-outline-info" onclick="cargarContenidoArchivoViewer()"><i class="bi bi-arrow-clockwise me-1"></i>Actualizar</button>
                        </div>
                    </div>
                    <textarea id="tftp_file_content" class="form-control code-editor p-3 mb-3 font-monospace" rows="20" readonly placeholder="Seleccione un archivo de la lista superior para ver su contenido..."></textarea>
                </div>
            </div>

            <div class="tab-pane fade" id="raw-dhcp-pane">
                <div class="card card-custom p-4 shadow-sm">
                    <h5 class="fw-bold text-info mb-3"><i class="bi bi-file-text me-2"></i>Contenido Actual de /etc/dhcp/dhcpd.conf</h5>
                    <textarea id="raw_dhcp_content" class="form-control code-editor p-3 mb-3" rows="20" readonly></textarea>
                    <button class="btn btn-sm btn-outline-info" onclick="cargarRawDHCP()">Refrescar Vista de DHCP</button>
                </div>
            </div>

            <div class="tab-pane fade" id="dhcp-logs-pane">
                <div class="card card-custom p-4 shadow-sm">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <h5 class="fw-bold text-info mb-0"><i class="bi bi-journal-text me-2"></i>Monitoreo de Procesos DHCP (Offers, Discovers, Acks)</h5>
                        <button class="btn btn-sm btn-outline-info" onclick="cargarLogsDHCP()"><i class="bi bi-arrow-clockwise me-1"></i>Refrescar Logs</button>
                    </div>
                    <textarea id="dhcp_logs_content" class="form-control code-editor p-3 mb-3 font-monospace" rows="20" readonly></textarea>
                </div>
            </div>

        </div>

    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js" async></script>    

    <script>        
        var allHosts = [];
        var templatesData = {};

        document.addEventListener("DOMContentLoaded", function() {            
            cargarHosts();            
            cargarArchivosTFTP();            
            cargarRawDHCP();            
            cargarPlantillasSelect();        
        });

        function cargarHosts() {            
            fetch('/api/hosts')                
                .then(function(res) { return res.json(); })                
                .then(function(data) {                    
                    allHosts = data || [];                    
                    var tbody = document.getElementById('hostsTableBody');                    
                    tbody.innerHTML = '';                    
                    if (!Array.isArray(data) || data.length === 0) {                        
                        tbody.innerHTML = '<tr><td colspan="6" class="text-center py-4 text-secondary">No hay reservas configuradas aún.</td></tr>';                        
                        return;                    
                    }                    
                    data.forEach(function(h) {                        
                        var badgeClass = 'badge-cm';                        
                        if (h.device_type === 'EMTA') badgeClass = 'badge-emta';                        
                        if (h.device_type === 'eRouter') badgeClass = 'badge-erouter';

                        var tr = document.createElement('tr');                        
                        tr.innerHTML = `
                            <td><span class="badge ${badgeClass}">${h.device_type}</span></td>
                            <td class="fw-bold">${h.host_name}</td>
                            <td class="font-monospace text-info">${h.mac_address}</td>
                            <td class="font-monospace">${h.fixed_ip}</td>
                            <td><code class="text-warning">${h.bootfile}</code></td>
                            <td class="text-end">
                                <button class="btn btn-sm btn-outline-info me-1" onclick="editarHostById(${h.id})"><i class="bi bi-pencil"></i></button>
                                <button class="btn btn-sm btn-outline-danger" onclick="eliminarHost(${h.id})"><i class="bi bi-trash"></i></button>
                            </td>
                        `;
                        tbody.appendChild(tr);                    
                    });                
                })                
                .catch(function(err) {                    
                    document.getElementById('hostsTableBody').innerHTML = `<tr><td colspan="6" class="text-center py-4 text-danger">❌ Error API: ${err.message}</td></tr>`;                
                });        
        }

        function editarHostById(id) {            
            var h = allHosts.find(function(x) { return x.id === id; });            
            if (!h) return;            
            document.getElementById('host_id').value = h.id;            
            document.getElementById('host_name').value = h.host_name;            
            document.getElementById('mac_address').value = h.mac_address;            
            document.getElementById('fixed_ip').value = h.fixed_ip;            
            document.getElementById('device_type').value = h.device_type;            
            document.getElementById('bootfile').value = h.bootfile;        
        }

        function cargarRawDHCP() {            
            var txtArea = document.getElementById('raw_dhcp_content');            
            txtArea.value = "⏳ Cargando /etc/dhcp/dhcpd.conf...";            
            fetch('/api/dhcp/raw')                
                .then(function(res) { return res.json(); })                
                .then(function(data) {                    
                    txtArea.value = data.content || "# El archivo está vacío.";                
                })                
                .catch(function(err) {                    
                    txtArea.value = "❌ Error leyendo API: " + err.message;                
                });        
        }

        function cargarLogsDHCP() {            
            var txtArea = document.getElementById('dhcp_logs_content');            
            txtArea.value = "⏳ Cargando procesos y offers de DHCP (journalctl)...";            
            fetch('/api/dhcp/logs')                
                .then(function(res) { return res.json(); })                
                .then(function(data) {                    
                    txtArea.value = data.logs || "# No hay registros disponibles.";
                    txtArea.scrollTop = txtArea.scrollHeight;
                })                
                .catch(function(err) {                    
                    txtArea.value = "❌ Error leyendo API de logs: " + err.message;                
                });        
        }

        function cargarListaArchivosViewer() {
            fetch('/api/tftp/files')
                .then(function(res) { return res.json(); })
                .then(function(files) {
                    var select = document.getElementById('select_tftp_file');
                    var currentVal = select.value;
                    select.innerHTML = '<option value="">Seleccione un archivo...</option>';
                    if (Array.isArray(files)) {
                        files.forEach(function(f) {
                            var opt = document.createElement('option');
                            opt.value = f.name;
                            opt.textContent = f.name + ' (' + f.size + ' bytes)';
                            select.appendChild(opt);
                        });
                        select.value = currentVal;
                    }
                });
        }

        function cargarContenidoArchivoViewer() {
            var filename = document.getElementById('select_tftp_file').value;
            var txtArea = document.getElementById('tftp_file_content');
            if (!filename) {
                txtArea.value = "Seleccione un archivo de la lista superior.";
                return;
            }
            txtArea.value = "⏳ Leyendo archivo " + filename + "...";
            fetch('/api/tftp/read/' + encodeURIComponent(filename))
                .then(function(res) { return res.json(); })
                .then(function(data) {
                    txtArea.value = data.content || "# Archivo vacío.";
                })
                .catch(function(err) {
                    txtArea.value = "❌ Error al leer el archivo: " + err.message;
                });
        }

        document.getElementById('hostForm').addEventListener('submit', function(e) {            
            e.preventDefault();            
            var payload = {                
                id: document.getElementById('host_id').value,                
                host_name: document.getElementById('host_name').value.trim(),                
                mac_address: document.getElementById('mac_address').value.trim(),                
                fixed_ip: document.getElementById('fixed_ip').value.trim(),                
                device_type: document.getElementById('device_type').value,                
                bootfile: document.getElementById('bootfile').value.trim()            
            };

            fetch('/api/hosts/save', {                
                method: 'POST',                
                headers: {'Content-Type': 'application/json'},                
                body: JSON.stringify(payload)            
            })            
            .then(function(res) { return res.json(); })            
            .then(function(data) {                
                alert(data.message);                
                limpiarFormHost();                
                cargarHosts();                
                cargarRawDHCP();            
            });        
        });

        function limpiarFormHost() {            
            document.getElementById('host_id').value = '';            
            document.getElementById('hostForm').reset();        
        }

        function eliminarHost(id) {            
            if (!confirm('¿Desea eliminar esta reserva DHCP?')) return;            
            fetch('/api/hosts/delete/' + id, { method: 'POST' })                
                .then(function(res) { return res.json(); })                
                .then(function(data) {                    
                    alert(data.message);                    
                    cargarHosts();                    
                    cargarRawDHCP();                
                });        
        }

        function aplicarDHCP() {            
            fetch('/api/dhcp/apply', { method: 'POST' })                
                .then(function(res) { return res.json(); })                
                .then(function(data) {                    
                    alert((data.success ? '✅ ' : '❌ ') + data.message);                    
                    cargarRawDHCP();                
                });        
        }

        function cargarPlantillasSelect() {            
            fetch('/api/docsis/templates')                
                .then(function(res) { return res.json(); })                
                .then(function(data) {                    
                    templatesData = {};
                    var select = document.getElementById('select_template_selector');
                    select.innerHTML = '<option value="">Seleccionar Plantilla...</option>';
                    if (Array.isArray(data)) {
                        data.forEach(function(t) {
                            templatesData[t.filename] = t.raw_text;
                            var opt = document.createElement('option');
                            opt.value = t.filename;
                            opt.textContent = t.filename;
                            select.appendChild(opt);
                        });
                        if (data.length > 0) {
                            select.value = data[0].filename;
                            document.getElementById('docsis_code').value = data[0].raw_text;
                            document.getElementById('output_filename').value = data[0].filename.replace('.txt', '');
                        }
                    }
                });        
        }

        function cambiarPlantillaEditor(filename) {
            if (!filename) return;
            if (templatesData[filename]) {
                document.getElementById('docsis_code').value = templatesData[filename];
                document.getElementById('output_filename').value = filename.replace('.txt', '');
            }
        }

        function compilarDocsis() {            
            var filename = document.getElementById('output_filename').value.trim();            
            var raw_text = document.getElementById('docsis_code').value;            
            var consoleDiv = document.getElementById('compiler_console');

            if (!filename) { alert('Especifique un nombre de archivo binario'); return; }

            consoleDiv.style.display = 'block';            
            consoleDiv.textContent = '⏳ Compilando...';

            fetch('/api/docsis/compile', {                
                method: 'POST',                
                headers: {'Content-Type': 'application/json'},                
                body: JSON.stringify({ filename: filename, raw_text: raw_text })            
            })            
            .then(function(res) { return res.json(); })            
            .then(function(data) {                
                if (data.success) {                    
                    consoleDiv.className = 'p-3 bg-dark border border-success rounded font-monospace small text-success';                    
                    consoleDiv.textContent = '✅ ' + data.message + '\\n\\nDetalles:\\n' + data.output;                    
                    cargarArchivosTFTP();                
                } else {                    
                    consoleDiv.className = 'p-3 bg-dark border border-danger rounded font-monospace small text-danger';                    
                    consoleDiv.textContent = '❌ ' + data.message + '\\n\\nError:\\n' + data.output;                
                }            
            });        
        }

        function cargarArchivosTFTP() {            
            fetch('/api/tftp/files')                
                .then(function(res) { return res.json(); })                
                .then(function(files) {                    
                    var tbody = document.getElementById('tftpTableBody');                    
                    tbody.innerHTML = '';                    
                    if (!Array.isArray(files) || files.length === 0) {                        
                        tbody.innerHTML = '<tr><td colspan="4" class="text-center py-4 text-secondary">No se encontraron archivos en /srv/tftp.</td></tr>';                        
                        return;                    
                    }                    
                    files.forEach(function(f) {                        
                        var tr = document.createElement('tr');                        
                        tr.innerHTML = `
                            <td class="fw-bold font-monospace text-info">${f.name}</td>
                            <td class="font-monospace">${f.size}</td>
                            <td class="small text-secondary">${f.modified}</td>
                            <td class="text-end">
                                <a href="/api/tftp/download/${f.name}" class="btn btn-sm btn-outline-success me-1"><i class="bi bi-download"></i> Descargar</a>
                                <button class="btn btn-sm btn-outline-danger" onclick="eliminarArchivoTFTP('${f.name}')"><i class="bi bi-trash"></i></button>
                            </td>
                        `;
                        tbody.appendChild(tr);                    
                    });                
                })                
                .catch(function(err) {                    
                    document.getElementById('tftpTableBody').innerHTML = `<tr><td colspan="4" class="text-center py-4 text-danger">❌ Error API TFTP: ${err.message}</td></tr>`;                
                });        
        }

        function eliminarArchivoTFTP(filename) {            
            if (!confirm('¿Confirma eliminar el archivo ' + filename + ' de /srv/tftp?')) return;            
            fetch('/api/tftp/delete/' + encodeURIComponent(filename), { method: 'POST' })                
                .then(function(res) { return res.json(); })                
                .then(function(data) {                    
                    alert(data.message);                    
                    cargarArchivosTFTP();                
                });        
        }    
    </script>
</body>
</html>
"""


# --- RUTAS DE API REST ---
@app.route('/')
def index():
    return Response(HTML_TEMPLATE, mimetype='text/html')


@app.route('/api/dhcp/raw', methods=['GET'])
def get_raw_dhcp():
    try:
        if not os.path.exists(DHCP_CONF_PATH):
            return jsonify({"content": "# El archivo /etc/dhcp/dhcpd.conf no existe."})
        with open(DHCP_CONF_PATH, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        return jsonify({"content": content})
    except Exception as e:
        return jsonify({"content": "❌ Error de lectura en servidor: " + str(e)}), 500


@app.route('/api/dhcp/logs', methods=['GET'])
def get_dhcp_logs():
    try:
        cmd = ["journalctl", "-u", "isc-dhcp-server", "-n", "100", "--no-pager"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        logs = res.stdout if res.returncode == 0 else f"Error leyendo journalctl: {res.stderr}"
        return jsonify({"logs": logs})
    except Exception as e:
        return jsonify({"logs": f"❌ Excepción ejecutando journalctl: {str(e)}"}), 500


@app.route('/api/hosts', methods=['GET'])
def get_hosts():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM dhcp_hosts ORDER BY id DESC")
        rows = cursor.fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/hosts/save', methods=['POST'])
def save_host():
    data = request.json or {}
    host_id = data.get('id')
    name = data.get('host_name', '').strip()
    mac = format_mac(data.get('mac_address', '').strip())
    ip = data.get('fixed_ip', '').strip()
    dev_type = data.get('device_type', 'EMTA').strip()
    bootfile = data.get('bootfile', '').strip()

    if not name or not mac or not ip or not bootfile:
        return jsonify({"success": False, "message": "Faltan campos obligatorios."}), 400

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        if host_id:
            cursor.execute('''
                UPDATE dhcp_hosts 
                SET host_name=?, mac_address=?, fixed_ip=?, device_type=?, bootfile=?
                WHERE id=?
            ''', (name, mac, ip, dev_type, bootfile, host_id))
            msg = "Dispositivo '" + name + "' actualizado."
        else:
            cursor.execute('''
                INSERT INTO dhcp_hosts (host_name, mac_address, fixed_ip, device_type, bootfile)
                VALUES (?, ?, ?, ?, ?)
            ''', (name, mac, ip, dev_type, bootfile))
            msg = "Dispositivo '" + name + "' registrado."

        conn.commit()
        conn.close()
        sync_dhcp_file()
        return jsonify({"success": True, "message": msg})
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"success": False, "message": "El nombre del dispositivo ya existe en la BD."}), 400


@app.route('/api/hosts/delete/<int:host_id>', methods=['POST'])
def delete_host(host_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM dhcp_hosts WHERE id=?", (host_id,))
    conn.commit()
    conn.close()
    sync_dhcp_file()
    return jsonify({"success": True, "message": "Reserva eliminada y DHCP actualizado."})


@app.route('/api/dhcp/apply', methods=['POST'])
def apply_dhcp():
    sync_dhcp_file()
    success, message = reload_dhcp_server()
    return jsonify({"success": success, "message": message})


@app.route('/api/docsis/templates', methods=['GET'])
def get_templates():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT filename, raw_text FROM docsis_templates ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/docsis/compile', methods=['POST'])
def compile_docsis():
    data = request.json or {}
    filename = data.get('filename', '').strip()
    raw_text = data.get('raw_text', '')

    if not filename:
        return jsonify({"success": False, "message": "Nombre de archivo faltante"}), 400

    # 1. Sanitizar espacios no-separables web (\xa0), BOM y saltos Windows (\r\n)
    raw_text = raw_text.replace('\xa0', ' ').replace('\u00a0', ' ').replace('\r\n', '\n')
    if raw_text.startswith('\ufeff'):
        raw_text = raw_text[1:]

    txt_tmp_path = "/tmp/" + filename + ".txt"
    bin_output_path = os.path.join(TFTP_DIR, filename)

    try:
        with open(txt_tmp_path, "w", encoding="utf-8") as f:
            f.write(raw_text)

        # Buscar binario docsis
        docsis_bin = "docsis"
        for candidate in ["/usr/bin/docsis", "/usr/local/bin/docsis", "docsis"]:
            res_check = subprocess.run(["which", candidate], capture_output=True, text=True)
            if res_check.returncode == 0:
                docsis_bin = candidate
                break

        # 2. Copiamos entorno sin bloquear MIBs para que resuelva 'iso.' correctamente
        env = os.environ.copy()

        # Sintaxis por tipo de archivo: .bin (eMTA) -> -p | .cfg (Cable Modem) -> -e /dev/null
        if filename.lower().endswith(".bin"):
            cmd = [docsis_bin, "-p", txt_tmp_path, bin_output_path]
        else:
            cmd = [docsis_bin, "-e", txt_tmp_path, "/dev/null", bin_output_path]

        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10, env=env)

        # Validación de creación de binario
        if os.path.exists(bin_output_path) and os.path.getsize(bin_output_path) > 0:
            try:
                shutil.chown(bin_output_path, user="sagemcom", group="sagemcom")
                os.chmod(bin_output_path, 0o664)
            except Exception:
                pass

            return jsonify({
                "success": True,
                "message": "Archivo '" + filename + "' compilado exitosamente en /srv/tftp/",
                "output": "Compilación limpia ejecutada con " + ("docsis -p" if filename.lower().endswith(".bin") else "docsis -e con /dev/null") + "."
            })
        else:
            return jsonify({
                "success": False,
                "message": "Fallo la compilacion con el paquete docsis.",
                "output": res.stderr or res.stdout or "Error de sintaxis en el archivo .txt."
            }), 400
    except Exception as e:
        return jsonify({"success": False, "message": "Excepcion durante compilacion: " + str(e), "output": str(e)}), 500

@app.route('/api/tftp/files', methods=['GET'])
def list_tftp_files():
    files = []
    try:
        if os.path.exists(TFTP_DIR):
            for entry in os.listdir(TFTP_DIR):
                full_p = os.path.join(TFTP_DIR, entry)
                if os.path.isfile(full_p):
                    stat = os.stat(full_p)
                    files.append({
                        "name": entry,
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                    })
        return jsonify(sorted(files, key=lambda x: x['name']))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/tftp/read/<filename>')
def read_tftp_file(filename):
    full_p = os.path.join(TFTP_DIR, filename)
    if not os.path.exists(full_p):
        return jsonify({"content": "❌ Archivo no encontrado en /srv/tftp/"}), 404
    try:
        # Si es un archivo de texto plano (.txt), leerlo directamente
        if filename.endswith('.txt'):
            with open(full_p, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            return jsonify({"content": content})

        # Para archivos compilados (.cfg o .bin), utilizar docsis -d para decompilarlos y verlos en texto plano
        docsis_bin = "docsis"
        for candidate in ["/usr/bin/docsis", "/usr/local/bin/docsis", "docsis"]:
            res_check = subprocess.run(["which", candidate], capture_output=True, text=True)
            if res_check.returncode == 0:
                docsis_bin = candidate
                break

        # Copiamos entorno sin bloquear MIBs para decodificar correctamente
        env = os.environ.copy()

        cmd = [docsis_bin, "-d", full_p]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10, env=env)

        if res.returncode == 0 and res.stdout.strip():
            return jsonify({"content": res.stdout})
        else:
            # Fallback a lectura de texto plano si no es un binario docsis estándar
            with open(full_p, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if content.strip():
                return jsonify({"content": content})
            return jsonify({"content": res.stderr or "# El archivo binario no pudo ser decodificado a texto."})
    except Exception as e:
        return jsonify({"content": "❌ Error al leer el archivo: " + str(e)}), 500


@app.route('/api/tftp/download/<filename>')
def download_tftp_file(filename):
    return send_from_directory(TFTP_DIR, filename, as_attachment=True)


@app.route('/api/tftp/delete/<filename>', methods=['POST'])
def delete_tftp_file(filename):
    full_p = os.path.join(TFTP_DIR, filename)
    if os.path.exists(full_p):
        os.remove(full_p)
        return jsonify({"message": "Archivo '" + filename + "' eliminado de /srv/tftp/"})
    return jsonify({"message": "Archivo no encontrado"}), 404


if __name__ == '__main__':
    print("🚀 Iniciando DOCSIS & eMTA Provisioner Suite en puerto 9037...")
    app.run(host='0.0.0.0', port=9037, debug=False)