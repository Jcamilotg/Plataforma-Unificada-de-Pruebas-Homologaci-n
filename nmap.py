#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AUDITORÍA DE RED - ETHICAL HACKING LABS
========================================
Script autocontenido para auditorías de red de bajo nivel con Nmap y Searchsploit.

PRERREQUISITOS E INSTALACIÓN (Linux/Debian/Kali):
------------------------------------------------
# 1. Actualizar repositorios e instalar dependencias del sistema:
sudo apt update && sudo apt install -y nmap searchsploit python3-flask

# 2. Instalar dependencias Python adicionales si no están incluidas en Flask:
pip3 install requests

# 3. Ejecutar con privilegios elevados para escaneo de raw sockets (-sS / -O):
sudo python3 app.py
"""

import os
import sys
import re
import json
import time
import subprocess
import io
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify, send_file

# Configuración del servidor Flask
app = Flask(__name__)
app.secret_key = 'cyberpunk_ethical_hacking_lab_2026'

# Variables globales para almacenar resultados
scan_results = {}
current_target = None

# Expresiones regulares para sanitización de entrada
IPV4_PATTERN = re.compile(
    r'^((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$'
)

IPV6_PATTERN = re.compile(
    r'^([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$|'
    r'^([0-9a-fA-F]{1,4}:){1,7}:$|'
    r'^:([0-9a-fA-F]{1,4}:){1,6}$|'
    r'^([0-9a-fA-F]{1,4}:){1,5}(:[0-9a-fA-F]{1,4}){1,2}$|'
    r'^::ffff:([0-9]{1,3}\.){3}[0-9]{1,3}$'
)

DOMAIN_PATTERN = re.compile(
    r'^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z]{2,})+$'
)

VALID_HOST_PATTERN = re.compile(
    f"({IPV4_PATTERN.pattern})|({IPV6_PATTERN.pattern})|({DOMAIN_PATTERN.pattern})"
)


def sanitize_host(host):
    """Sanitiza y valida la entrada del host objetivo."""
    if not host or not isinstance(host, str):
        return None

    normalized_host = host.strip().lower()

    if IPV4_PATTERN.match(normalized_host) or \
            IPV6_PATTERN.match(normalized_host) or \
            DOMAIN_PATTERN.match(normalized_host):
        return normalized_host
    return None


def extract_version_from_service(service_line, version_pattern=r'Version: ([^\s]+)'):
    """Extrae la versión de un servicio Nmap."""
    match = re.search(version_pattern, service_line, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def extract_os_from_nmap(os_match):
    """Extrae información del SO detectado por Nmap evitando errores de NoneType."""
    if os_match and os_match.groups():
        groups = os_match.groups()
        if len(groups) >= 2 and groups[0]:
            return {
                "name": groups[0].strip(),
                "version": groups[1].strip() if len(groups) > 1 else None,
                "accuracy": (
                    int(groups[2])
                    if len(groups) > 2 and groups[2] and str(groups[2]).isdigit()
                    else 0
                ),
            }
    return None


def run_nmap_scan(target):
    """Ejecuta Nmap con parámetros avanzados y extrae información detallada."""
    print(f"[+] Iniciando escaneo Nmap: {target}")

    cmd = f"nmap -Pn -p- -sS -sV -O --traceroute -T4 --open {target}"

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=300
        )
        output = result.stdout + result.stderr

        if result.returncode != 0:
            print(f"[-] Nmap reportó error (código {result.returncode})")
            return {'raw_output': output, 'status': 'error', 'exit_code': result.returncode}

        parsed_data = extract_nmap_data(output)
        print(f"[+] Escaneo Nmap completado para: {target}")
        return {
            'raw_output': output,
            'status': 'success',
            'exit_code': result.returncode,
            'parsed': parsed_data
        }

    except subprocess.TimeoutExpired:
        print(f"[-] Timeout en escaneo Nmap para: {target}")
        return {'raw_output': 'Escaneo timeout después de 300 segundos', 'status': 'timeout'}
    except Exception as e:
        print(f"[-] Error ejecutando Nmap: {str(e)}")
        return {'raw_output': f'Error: {str(e)}', 'status': 'error'}


def extract_nmap_data(output):
    """Extrae y estructura datos de salida de Nmap incluyendo SO, MAC y Latencia."""
    data = {
        "host_summary": [],
        "open_ports": [],
        "services": [],
        "os_detection": "No detectado",
        "mac_address": "N/A",
        "latency": "N/A",
        "reverse_dns": "N/A",
        "raw_lines": [],
    }

    lines = output.split("\n")

    # 1. Resumen de hosts y DNS Inverso
    dns_match = re.search(r"Nmap scan report for ([^\s]+)\s+\(([\d\.]+)\)", output)
    if dns_match:
        data["reverse_dns"] = dns_match.group(1)
        data["host_summary"].append(dns_match.group(2))
    else:
        for line in lines:
            if "Nmap scan report for" in line:
                match = re.search(r"Nmap scan report for ([^\s]+)", line)
                if match:
                    data["host_summary"].append(match.group(1))

    # 2. Dirección MAC y fabricante
    mac_match = re.search(r'MAC Address:\s*([0-9A-Fa-f:]+)\s*(?:\((.*?)\))?', output)
    if mac_match:
        mac = mac_match.group(1)
        vendor = mac_match.group(2) if mac_match.group(2) else "Desconocido"
        data["mac_address"] = f"{mac} ({vendor})"

    # 3. Latencia (RTT)
    lat_match = re.search(r'Host is up \(([\d\.]+s) latency\)', output)
    if lat_match:
        data["latency"] = lat_match.group(1)

    # 4. Detección de SO (prioridad: exacta -> estimación/guesses -> Service Info)

    # Caso A: Encabezados estándar de Nmap (OS details, Running, OS detected, Aggressive OS guesses)
    os_match = re.search(
        r'(?:OS details|Service Info:|OS detected:|Aggressive OS guesses:)\s*([^\n]+)',
        output,
        re.IGNORECASE
    )

    if os_match:
        data["os_detection"] = os_match.group(1).strip()
    else:
        # Caso B: Si falla la huella TCP/IP, buscar en la línea 'Service Info' (ej: Service Info: OS: Windows)
        service_os_match = re.search(
            r'Service Info:.*?\bOS:\s*([^;\n]+)',
            output,
            re.IGNORECASE
        )
        if service_os_match:
            data["os_detection"] = service_os_match.group(1).strip()
        else:
            # Caso C: Búsqueda secundaría línea por línea mediante función auxiliar
            detected = False
            for line in lines:
                match = re.search(r"(OS\s+detected:|OS:\s*)([^\n]+)", line, re.IGNORECASE)
                if match:
                    extracted = extract_os_from_nmap(match)
                    if extracted and extracted.get('name'):
                        data["os_detection"] = f"{extracted.get('name', '')} {extracted.get('version', '')}".strip()
                        detected = True
                        break

            if not detected:
                data["os_detection"] = "No detectado"

    # 5. Puertos abiertos y servicios
    for line in lines:
        line_stripped = line.strip()
        if "open" in line_stripped.lower() and re.match(r"^[0-9]+/(tcp|udp)", line_stripped):
            parts = re.split(r'\s+', line_stripped, maxsplit=3)

            if len(parts) >= 2:
                port_raw = parts[0]
                state = parts[1]
                service = parts[2] if len(parts) > 2 else "unknown"
                version = parts[3] if len(parts) > 3 else None

                data["open_ports"].append({
                    "port": port_raw.replace('/tcp', '').replace('/udp', ''),
                    "state": state,
                    "service": service,
                    "version": version
                })

    # 6. Líneas relevantes
    for line in lines:
        if any(keyword in line.lower() for keyword in ["port", "service", "version", "open"]):
            data["raw_lines"].append(line.strip())

    return data


def run_searchsploit(target):
    """Busca exploits locales para el objetivo."""
    print(f"[+] Buscando exploits con Searchsploit: {target}")

    cmd = f"searchsploit {target}"

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=180
        )
        output = result.stdout + result.stderr
        parsed_exploits = parse_searchsploit_output(output)

        print(f"[+] Búsqueda de exploits completada para: {target}")
        return {
            'raw_output': output,
            'status': 'success',
            'exit_code': result.returncode,
            'exploits_found': parsed_exploits
        }

    except subprocess.TimeoutExpired:
        return {'raw_output': 'Búsqueda timeout después de 180 segundos', 'status': 'timeout'}
    except Exception as e:
        return {'raw_output': f'Error: {str(e)}', 'status': 'error'}


def parse_searchsploit_output(output):
    """Parsea salida tabular estándar de Searchsploit y extrae exploits relevantes."""
    exploits = []
    for line in output.split('\n'):
        if '|' in line and 'Exploit Title' not in line and '----' not in line:
            parts = line.split('|')
            if len(parts) >= 2:
                title = parts[0].strip()
                path = parts[1].strip()
                exploits.append({
                    'id': path.split('/')[-1] if '/' in path else 'N/A',
                    'platform': 'N/A',
                    'service': 'N/A',
                    'description': title[:500]
                })
    return exploits


def clean_ansi(text):
    """Elimina códigos de color y formato ANSI devueltos por la consola."""
    ansi_regex = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_regex.sub('', text)


def run_full_audit(target):
    """Ejecuta auditoría completa: Nmap + Searchsploit."""
    print(f"\n{'=' * 60}\nINICIANDO AUDITORÍA COMPLETA PARA: {target}\n{'=' * 60}\n")

    results = {
        'status': 'success',
        'timestamp': datetime.now().isoformat(),
        'target': target,
        'nmap_scan': None,
        'searchsploit_scan': None,
        'combined_analysis': {}
    }

    nmap_result = run_nmap_scan(target)
    results['nmap_scan'] = nmap_result

    service_versions = []
    if nmap_result.get('parsed'):
        for port_info in nmap_result['parsed'].get('open_ports', []):
            version = port_info.get('version')
            service = port_info.get('service')

            # Si hay versión, incluirla en la búsqueda para evitar falsos positivos
            # CÓDIGO CORREGIDO:
            if version:
                # Extrae la primera palabra (ej. Apache, OpenSSH, vsftpd) y el primer número de versión que encuentre
                words = version.split()
                product = words[0]  # Ej: Apache, vsftpd, OpenSSH

                # Buscar el primer bloque que contenga números (la versión)
                ver_num = next((w for w in words[1:] if any(c.isdigit() for c in w)), "")

                if ver_num:
                    search_string = f"{product} {ver_num}"  # Queda: "Apache 2.4.58", "vsftpd 3.0.5"
                else:
                    search_string = " ".join(words[:2])
            elif service and service != 'unknown':
                search_string = service
            else:
                continue

            service_versions.append({
                'port': port_info['port'],
                'search_string': search_string,
                'raw_version': version or service
            })

    results['searchsploit_scan'] = run_searchsploit(target)

    if service_versions:
        print(f"\n[+] Buscando exploits específicos para {len(service_versions)} servicios detectados...")

        for sv in service_versions:
            search_cmd = f'searchsploit "{sv["search_string"]}"'

            try:
                result = subprocess.run(
                    search_cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=60
                )

                # Limpiar la salida de caracteres ANSI
                clean_output = clean_ansi(result.stdout)
                exploit_lines = [l for l in clean_output.split('\n') if '|' in l and 'Exploit Title' not in l and '---' not in l]

                if result.returncode == 0 and len(exploit_lines) > 0:
                    exploit_details = []
                    for line in exploit_lines:
                        parts = line.split('|')
                        if len(parts) >= 2:
                            exploit_details.append({
                                'title': parts[0].strip(),
                                'path': parts[1].strip()
                            })

                    # Se incluye el puerto para diferenciar servicios duplicados
                    results['combined_analysis'][f'service_{sv["port"]}_specific'] = {
                        'service': f"{sv['search_string']} (Puerto {sv['port']})",
                        'exploits_found': len(exploit_lines),
                        'exploits_list': exploit_details
                    }
            except Exception as e:
                print(f"[-] Error buscando exploits para {sv['search_string']}: {e}")

    results['combined_analysis']['summary'] = {
        'total_ports_open': len(nmap_result.get('parsed', {}).get('open_ports', [])),
        'services_detected': [p.get('service') for p in nmap_result.get('parsed', {}).get('open_ports', []) if p.get('service')],
        'os_detected': results['nmap_scan'].get('parsed', {}).get('os_detection'),
        'exploits_found_count': sum(v.get('exploits_found', 0) for v in results['combined_analysis'].values() if isinstance(v, dict)),
        'risk_level': calculate_risk_level(results)
    }

    return results

def calculate_risk_level(results):
    """Calcula nivel de riesgo basado en hallazgos."""
    risk_score = 0
    high_risk_services = ['apache', 'nginx', 'tomcat', 'java', 'php']
    medium_risk_services = ['ssh', 'ftp', 'mysql', 'postgres', 'smb', 'netbios']

    services = [p.get('service') for p in results['nmap_scan'].get('parsed', {}).get('open_ports', []) if
                p.get('service')]

    for service in services:
        service_lower = service.lower()
        if any(s in service_lower for s in high_risk_services):
            risk_score += 30
        elif any(s in service_lower for s in medium_risk_services):
            risk_score += 15

    os_info = results['nmap_scan'].get('parsed', {}).get('os_detection')
    if os_info:
        os_str = os_info if isinstance(os_info, str) else os_info.get('name', '')
        if 'linux' in os_str.lower():
            risk_score += 20
        elif 'windows' in os_str.lower():
            risk_score += 15

    exploit_count = results['combined_analysis'].get('summary', {}).get('exploits_found_count', 0)
    if exploit_count > 0:
        risk_score += min(exploit_count * 5, 30)

    if risk_score >= 60:
        return 'ALTO'
    elif risk_score >= 30:
        return 'MEDIO'
    else:
        return 'BAJO'


# ============================================
# FRONTEND CYBERPUNK (HTML/CSS/JS)
# ============================================

CYBERPUNK_TEMPLATE = r'''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cyber Network Auditor - Ethical Hacking Lab</title>
    <style>
        :root {
            --bg-primary: #0d0d12;
            --bg-secondary: #1a1a2e;
            --neon-green: #00ff88;
            --neon-blue: #00d4ff;
            --neon-red: #ff3366;
            --neon-yellow: #ffcc00;
            --text-primary: #ffffff;
            --text-secondary: #a0a0b0;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: 'Courier New', monospace;
            background-color: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            overflow-x: hidden;
        }

        body::before {
            content: '';
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background-image: 
                linear-gradient(rgba(0, 255, 136, 0.03) 1px, transparent 1px),
                linear-gradient(90deg, rgba(0, 255, 136, 0.03) 1px, transparent 1px);
            background-size: 40px 40px;
            pointer-events: none;
        }

        .container { max-width: 1400px; margin: 0 auto; padding: 2rem; position: relative; z-index: 1; }

        header { text-align: center; padding: 2rem 0; border-bottom: 2px solid var(--neon-green); margin-bottom: 2rem; }

        h1 { font-size: 2.5rem; color: var(--neon-green); text-shadow: 0 0 10px var(--neon-green), 0 0 20px var(--neon-blue); margin-bottom: 0.5rem; }

        .subtitle { color: var(--text-secondary); font-size: 1rem; }

        .scan-form {
            background-color: var(--bg-secondary);
            border: 2px solid var(--neon-blue);
            border-radius: 8px;
            padding: 2rem;
            margin-bottom: 2rem;
            box-shadow: 0 0 20px rgba(0, 212, 255, 0.3);
        }

        .form-group { display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; }

        label { color: var(--neon-blue); font-weight: bold; min-width: 150px; }

        input[type="text"] {
            flex: 1; min-width: 200px; padding: 1rem;
            background-color: var(--bg-primary); border: 2px solid var(--neon-green);
            color: var(--text-primary); font-family: inherit; font-size: 1.1rem; border-radius: 4px; outline: none;
        }

        button {
            padding: 1rem 2rem;
            background: linear-gradient(45deg, var(--neon-green), var(--neon-blue));
            border: none; color: var(--bg-primary); font-family: inherit; font-size: 1.1rem;
            font-weight: bold; cursor: pointer; border-radius: 4px; text-transform: uppercase;
        }

        button:hover { transform: translateY(-2px); box-shadow: 0 5px 20px rgba(0, 255, 136, 0.4); }
        button:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }

        #loadingOverlay {
            display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background-color: rgba(13, 13, 18, 0.9); z-index: 1000; justify-content: center; align-items: center; flex-direction: column;
        }

        .spinner {
            width: 80px; height: 80px; border: 4px solid var(--bg-secondary);
            border-top: 4px solid var(--neon-green); border-radius: 50%; animation: spin 1s linear infinite;
        }

        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }

        .loading-text { color: var(--neon-green); font-size: 1.2rem; margin-top: 1rem; text-shadow: 0 0 10px var(--neon-green); }

        .terminal-container {
            background-color: #000; border: 3px solid var(--neon-red);
            border-radius: 8px; padding: 1.5rem; margin-top: 2rem; box-shadow: 0 0 30px rgba(255, 51, 102, 0.4);
        }

        .terminal-header { display: flex; gap: 0.5rem; margin-bottom: 1rem; padding-bottom: 1rem; border-bottom: 1px solid var(--neon-red); }

        .terminal-dot { width: 12px; height: 12px; border-radius: 50%; }
        .dot-red { background-color: var(--neon-red); }
        .dot-yellow { background-color: var(--neon-yellow); }
        .dot-green { background-color: var(--neon-green); }

        .terminal-title { color: var(--text-secondary); font-size: 0.9rem; margin-left: auto; }

        #consoleOutput { white-space: pre-wrap; overflow-y: auto; max-height: 600px; color: var(--text-primary); font-size: 0.95rem; line-height: 1.6; }

        .log-info { color: var(--neon-blue); }
        .log-success { color: var(--neon-green); }
        .log-warning { color: var(--neon-yellow); }
        .log-error { color: var(--neon-red); }

        #consoleOutput::-webkit-scrollbar { width: 8px; }
        #consoleOutput::-webkit-scrollbar-track { background: var(--bg-primary); }
        #consoleOutput::-webkit-scrollbar-thumb { background: var(--neon-blue); border-radius: 4px; }

        .download-section { margin-top: 2rem; text-align: center; }

        #downloadBtn { display: none; background: linear-gradient(45deg, var(--neon-red), var(--neon-blue)); margin: 0 auto; }

        footer { margin-top: 3rem; text-align: center; color: var(--text-secondary); font-size: 0.9rem; padding: 1rem; border-top: 1px solid var(--neon-blue); }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>CYBER NETWORK AUDITOR</h1>
            <p class="subtitle">Ethical Hacking Lab - Advanced Network Penetration Testing Suite</p>
        </header>

        <form id="scanForm" class="scan-form">
            <div class="form-group">
                <label for="targetInput">TARGET IP / HOST:</label>
                <input type="text" id="targetInput" name="target" placeholder="Ej: 192.168.1.100, example.com" autocomplete="off">
            </div>
            <button type="submit" id="scanBtn">INICIAR AUDITORÍA COMPLETA</button>
        </form>

        <div class="terminal-container">
            <div class="terminal-header">
                <span class="terminal-dot dot-red"></span>
                <span class="terminal-dot dot-yellow"></span>
                <span class="terminal-dot dot-green"></span>
                <span class="terminal-title" id="consoleTitle">TERMINAL READY - WAITING FOR INPUT</span>
            </div>
            <div id="consoleOutput"></div>
            <div class="download-section">
                <button id="downloadBtn" onclick="downloadReport()">⬇ DESCARGAR REPORTE VÍA WEB (.txt)</button>
            </div>
        </div>

        <footer>
            <p>Cyber Network Auditor v1.0 | Ethical Hacking Lab Suite</p>
            <p style="color: var(--neon-green); font-size: 0.8rem;">Running on Flask Backend - Port 9039</p>
        </footer>
    </div>

    <div id="loadingOverlay">
        <div class="spinner"></div>
        <div class="loading-text" id="loadingText">INICIANDO ESCANEO...</div>
    </div>

    <script>
        let currentTarget = '';
        let reportData = null;
        const API_URL = '';

        const scanForm = document.getElementById('scanForm');
        const targetInput = document.getElementById('targetInput');
        const scanBtn = document.getElementById('scanBtn');
        const consoleOutput = document.getElementById('consoleOutput');
        const consoleTitle = document.getElementById('consoleTitle');
        const loadingOverlay = document.getElementById('loadingOverlay');
        const downloadBtn = document.getElementById('downloadBtn');

        function addLog(message, type = 'info') {
            const timestamp = new Date().toLocaleTimeString();
            let colorClass = 'log-info';
            if (type === 'success') colorClass = 'log-success';
            if (type === 'error') colorClass = 'log-error';
            if (type === 'warning') colorClass = 'log-warning';

            const logEntry = document.createElement('div');
            logEntry.className = colorClass;
            logEntry.innerHTML = `<span style="color: var(--text-secondary);">[${timestamp}]</span> ${message}`;

            consoleOutput.appendChild(logEntry);
            consoleOutput.scrollTop = consoleOutput.scrollHeight;
        }

        function sanitizeHost(host) {
            if (!host || !host.trim()) return null;
            const normalized = host.trim().toLowerCase();
            const ipv4Pattern = /^((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$/;
            const ipv6Pattern = /^([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$\vert{}^::ffff:([0-9]{1,3}\.){3}[0-9]{1,3}$/;
            const domainPattern = /^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z]{2,})+$/;

            if (ipv4Pattern.test(normalized)) return normalized;
            if (ipv6Pattern.test(normalized)) return normalized;
            if (domainPattern.test(normalized)) return normalized;
            return null;
        }

        scanForm.addEventListener('submit', async function(e) {
            e.preventDefault();

            const rawTarget = targetInput.value.trim();
            currentTarget = sanitizeHost(rawTarget);

            if (!currentTarget) {
                addLog(`Error: Formato de host inválido. Intenta con: 192.168.1.100, example.com`, 'error');
                return;
            }

            // LIMPIAR HISTORIAL ANTERIOR AL INICIAR NUEVO ESCANEO
            consoleOutput.innerHTML = '';
            downloadBtn.style.display = 'none';
            reportData = null;

            scanBtn.disabled = true;
            targetInput.disabled = true;

            loadingOverlay.style.display = 'flex';
            consoleTitle.textContent = `SCANNING TARGET: ${currentTarget.toUpperCase()}`;
            addLog(`\n${'='.repeat(60)}`, 'info');
            addLog(`INICIANDO AUDITORÍA COMPLETA PARA: ${currentTarget}`, 'success');
            addLog(`${'='.repeat(60)}`, 'info');

            try {
                const response = await fetch(`${API_URL}/scan`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ target: currentTarget })
                });

                if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);

                const data = await response.json();
                reportData = data;
                renderResults(data);

            } catch (error) {
                addLog(`Error de conexión con el backend: ${error.message}`, 'error');
            } finally {
                scanBtn.disabled = false;
                targetInput.disabled = false;

                setTimeout(() => { loadingOverlay.style.display = 'none'; }, 1000);

                if (reportData && reportData.status === 'success') {
                    downloadBtn.style.display = 'inline-block';
                }
            }
        });

        function renderResults(data) {
            addLog(`\n[+] TIMESTAMP: ${data.timestamp}`, 'success');
            addLog(`[+] TARGET: ${data.target}`, 'success');
            if (data.nmap_scan) renderNmapOutput(data.nmap_scan);
            if (data.searchsploit_scan) renderSearchsploitOutput(data.searchsploit_scan);
            if (data.combined_analysis && data.combined_analysis.summary) renderSummary(data.combined_analysis.summary);
        }

        function renderNmapOutput(nmapData) {
            if (nmapData.status === 'success') {
                addLog(`\n[+] NMAP SCAN COMPLETADO - ${nmapData.exit_code} exit code`, 'success');
                if (nmapData.parsed) {
                    const p = nmapData.parsed;
                    addLog(`\n================================================================================`, 'info');
                    addLog(`INFORMACIÓN DEL SISTEMA Y HARDWARE`, 'success');
                    addLog(`================================================================================`, 'info');
                    addLog(`Sistema Operativo : ${p.os_detection || 'N/A'}`, 'warning');
                    addLog(`Dirección MAC     : ${p.mac_address || 'N/A'}`, 'info');
                    addLog(`DNS Inverso (PTR) : ${p.reverse_dns || 'N/A'}`, 'info');
                    addLog(`Latencia (RTT)    : ${p.latency || 'N/A'}`, 'info');
                    addLog(`================================================================================`, 'info');

                    if (p.open_ports && p.open_ports.length > 0) {
                        addLog(`\n[+] PUERTOS ABIERTOS (${p.open_ports.length} total):`, 'info');
                        p.open_ports.forEach((port, index) => {
                            addLog(`    ${index + 1}. Port: ${port.port}/TCP`, 'info');
                            addLog(`       Service: ${port.service || 'unknown'}`, 'success');
                            addLog(`       Version: ${port.version || 'not set'}`, 'warning');
                        });
                    }
                }
            }
        }

        function renderSearchsploitOutput(searchsploitData) {
            if (searchsploitData.status === 'success') {
                addLog(`\n[+] SEARCHSPLOIT SCAN COMPLETADO - ${searchsploitData.exit_code} exit code`, 'success');
            }
        }

        function renderSummary(summary) {
            addLog(`\n${'='.repeat(60)}`, 'info');
            addLog(`AUDITORÍA COMPLETA - RESUMEN FINAL`, 'success');
            addLog(`${'='.repeat(60)}`, 'info');

            addLog(`\n[+] PUERTOS ABIERTOS: ${summary.total_ports_open || 0}`, 'info');
            addLog(`[+] EXPLOITS ENCONTRADOS: ${summary.exploits_found_count || 0}`, summary.exploits_found_count > 0 ? 'warning' : 'info');

            let riskColor = '#00ff88'; 
            let riskText = 'BAJO';
            if (summary.risk_level === 'MEDIO') { riskColor = '#ffcc00'; riskText = 'MEDIO'; } 
            else if (summary.risk_level === 'ALTO') { riskColor = '#ff3366'; riskText = 'ALTO'; }

            addLog(`\n[+] NIVEL DE RIESGO: <span style="color: ${riskColor}; font-weight: bold;">${riskText}</span>`, 
                    summary.risk_level === 'ALTO' ? 'warning' : 'success');

            // IMPRIMIR LOS EXPLOITS DETALLADOS EN LA CONSOLA WEB
            if (reportData && reportData.combined_analysis) {
                addLog(`\n[+] DETALLE DE EXPLOITS POR SERVICIO:`, 'warning');
                Object.keys(reportData.combined_analysis).forEach(key => {
                    if (key !== 'summary') {
                        const val = reportData.combined_analysis[key];
                        if (val && val.exploits_list && val.exploits_list.length > 0) {
                            addLog(`    • Servicio: ${val.service} (${val.exploits_found} exploits)`, 'success');
                            val.exploits_list.forEach((exp, idx) => {
                                consoleOutput.appendChild(createCodeBlock(`      [${idx + 1}] ${exp.title} (${exp.path})`));
                            });
                        }
                    }
                });
            }

            addLog(`\n${'='.repeat(60)}`, 'info');
            addLog(`AUDITORÍA COMPLETADA - REPORTE LISTO PARA DESCARGA`, 'success');
        }

        function createCodeBlock(text) {
            const div = document.createElement('div');
            div.style.marginLeft = '2rem';
            div.style.color = '#a0a0b0';
            div.style.fontSize = '0.85rem';
            div.textContent = text;
            return div;
        }

        window.downloadReport = function() {
            if (!reportData || reportData.status !== 'success') {
                addLog(`\n[!] No hay datos de reporte activos.`, 'warning');
                return;
            }

            const timestamp = new Date().toISOString();
            let reportContent = `================================================================================\n`;
            reportContent += `CYBER NETWORK AUDITOR - COMPLETE REPORT\n`;
            reportContent += `Generated: ${timestamp}\nTarget: ${reportData.target}\n`;
            reportContent += `================================================================================\n\n`;

            if (reportData.nmap_scan && reportData.nmap_scan.parsed) {
                const parsed = reportData.nmap_scan.parsed;
                reportContent += `================================================================================\n`;
                reportContent += `INFORMACIÓN DEL SISTEMA Y HARDWARE\n`;
                reportContent += `================================================================================\n`;
                reportContent += `Sistema Operativo : ${parsed.os_detection || 'N/A'}\n`;
                reportContent += `Dirección MAC     : ${parsed.mac_address || 'N/A'}\n`;
                reportContent += `DNS Inverso (PTR) : ${parsed.reverse_dns || 'N/A'}\n`;
                reportContent += `Latencia (RTT)    : ${parsed.latency || 'N/A'}\n`;
                reportContent += `================================================================================\n\n`;

                if (parsed.open_ports && parsed.open_ports.length > 0) {
                    reportContent += `--- NMAP SCAN RESULTS ---\n`;
                    reportContent += `OPEN PORTS:\n`;
                    parsed.open_ports.forEach((port, index) => {
                        reportContent += `${index + 1}. Port: ${port.port}/TCP\n`;
                        reportContent += `   Service: ${port.service || 'unknown'}\n`;
                        reportContent += `   Version: ${port.version || 'not set'}\n`;
                    });
                    reportContent += `\n`;
                }
            }

            if (reportData.combined_analysis) {
                reportContent += `--- EXPLOITS DETAILS ---\n`;
                Object.keys(reportData.combined_analysis).forEach(key => {
                    if (key !== 'summary') {
                        const val = reportData.combined_analysis[key];
                        if (val && val.exploits_list) {
                            reportContent += `Service: ${val.service} (${val.exploits_found} exploits)\n`;
                            val.exploits_list.forEach((exp, idx) => {
                                reportContent += `  [${idx + 1}] ${exp.title} (${exp.path})\n`;
                            });
                            reportContent += `\n`;
                        }
                    }
                });
            }

            reportContent += `================================================================================\n`;
            reportContent += `END OF REPORT\nGenerated by: Cyber Network Auditor v1.0\n`;
            reportContent += `================================================================================\n`;

            const blob = new Blob([reportContent], { type: 'text/plain' });
            const url = URL.createObjectURL(blob);

            const link = document.createElement('a');
            link.href = url;
            link.download = `audit_${currentTarget.replace(/[^a-zA-Z0-9]/g, '_')}_${timestamp.replace(/[:.]/g, '-')}.txt`;

            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);

            addLog(`\n[+] REPORTE DESCARGADO VÍA NAVEGADOR EXITOSAMENTE.`, 'success');
        };
    </script>
</body>
</html>
'''


# ============================================
# BACKEND ROUTES (Flask)
# ============================================

@app.route('/')
def index():
    """Renderiza el frontend Cyberpunk."""
    return render_template_string(CYBERPUNK_TEMPLATE)


@app.route('/scan', methods=['POST'])
def scan_endpoint():
    """Endpoint principal para iniciar auditoría completa."""
    global current_target, scan_results
    try:
        data = request.get_json()
        if not data or 'target' not in data:
            return jsonify({'status': 'error', 'message': 'Missing target'}), 400

        raw_target = data['target']
        current_target = sanitize_host(raw_target)

        if not current_target:
            return jsonify({'status': 'error', 'message': 'Invalid host format'}), 400

        print(f"\n[+] Backend procesando request para: {current_target}")
        results = run_full_audit(current_target)

        scan_results = results
        return jsonify(results)

    except Exception as e:
        print(f'Backend Error: {str(e)}')
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/download', methods=['GET'])
def download_endpoint():
    """Endpoint de backend para generar y servir la descarga directa del reporte .txt."""
    global scan_results, current_target
    if not scan_results or 'target' not in scan_results:
        return jsonify({'status': 'error', 'message': 'No audit report available'}), 404

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_content = f"{'='*80}\n"
    report_content += f"CYBER NETWORK AUDITOR - REPORT\n"
    report_content += f"Target: {scan_results.get('target', 'N/A')}\n"
    report_content += f"Timestamp: {scan_results.get('timestamp', 'N/A')}\n"
    report_content += f"{'='*80}\n\n"

    # Sección SO y Hardware
    nmap_data = scan_results.get('nmap_scan', {}).get('parsed', {})
    if nmap_data:
        report_content += f"{'='*80}\n"
        report_content += f"INFORMACIÓN DEL SISTEMA Y HARDWARE\n"
        report_content += f"{'='*80}\n"
        report_content += f"Sistema Operativo : {nmap_data.get('os_detection', 'N/A')}\n"
        report_content += f"Dirección MAC     : {nmap_data.get('mac_address', 'N/A')}\n"
        report_content += f"DNS Inverso (PTR) : {nmap_data.get('reverse_dns', 'N/A')}\n"
        report_content += f"Latencia (RTT)    : {nmap_data.get('latency', 'N/A')}\n"
        report_content += f"{'='*80}\n\n"

        if nmap_data.get('open_ports'):
            report_content += "--- NMAP OPEN PORTS ---\n"
            for p in nmap_data['open_ports']:
                report_content += f"Port: {p.get('port')}/TCP | Service: {p.get('service')} | Version: {p.get('version')}\n"
            report_content += "\n"

    # Sección Exploits
    combined = scan_results.get('combined_analysis', {})
    if combined:
        report_content += "--- EXPLOITS FOUND ---\n"
        for key, val in combined.items():
            if key != 'summary' and isinstance(val, dict) and val.get('exploits_list'):
                report_content += f"Service: {val.get('service')} ({val.get('exploits_found')} exploits)\n"
                for idx, exp in enumerate(val['exploits_list']):
                    report_content += f"  [{idx+1}] {exp.get('title')} ({exp.get('path')})\n"
                report_content += "\n"

    report_content += f"{'='*80}\nEND OF REPORT\n{'='*80}\n"

    buffer = io.BytesIO()
    buffer.write(report_content.encode('utf-8'))
    buffer.seek(0)

    filename = f"audit_{current_target or 'report'}_{timestamp}.txt"
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype='text/plain'
    )


if __name__ == '__main__':
    print('\n' + '=' * 60)
    print('CYBER NETWORK AUDITOR - READY')
    print('=' * 60)
    print('  • Puerto web: 9039')
    print('  • Accede desde tu navegador para iniciar.')
    app.run(
        host='0.0.0.0',
        port=9039,
        debug=True,
        threaded=True
    )