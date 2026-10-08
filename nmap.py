#!/usr/bin/env python3
"""
Cyberpunk Network Scanner - Ethical Hacking Edition
Servidor Flask en puerto 9039 (Sincrónico y Robusto)
"""

from flask import Flask, render_template_string, request, redirect, url_for, Response
import subprocess
import json
import re
from datetime import datetime

app = Flask(__name__)

# Almacenamiento temporal del último resultado
LAST_RESULT = None

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cyberpunk Network Scanner</title>
    <style>
        :root {
            --bg-primary: #0d0d12;
            --bg-secondary: #1a1a24;
            --bg-tertiary: #15151f;
            --primary: #00ff88;
            --secondary: #00d4ff;
            --danger: #ff3366;
            --text-primary: #ffffff;
            --text-secondary: #b0b0c0;
            --border: #2a2a3e;
            --glow-primary: 0 0 10px rgba(0, 255, 136, 0.5);
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', monospace; background: var(--bg-primary); color: var(--text-primary); padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        header { background: var(--bg-secondary); border-bottom: 2px solid var(--primary); padding: 20px; margin-bottom: 20px; text-align: center; }
        h1 { color: var(--primary); text-shadow: var(--glow-primary); }
        .input-section { background: var(--bg-secondary); border: 1px solid var(--border); padding: 20px; border-radius: 8px; margin-bottom: 20px; }
        .input-group { display: flex; gap: 10px; }
        input[type="text"] { flex: 1; background: var(--bg-tertiary); border: 1px solid var(--border); color: var(--text-primary); padding: 12px; border-radius: 4px; font-size: 1rem; }
        .btn { padding: 12px 24px; font-weight: bold; border: none; border-radius: 4px; cursor: pointer; text-transform: uppercase; text-decoration: none; display: inline-block; }
        .btn-primary { background: var(--primary); color: #000; }
        .btn-secondary { background: var(--secondary); color: #000; }
        .terminal-container { background: #050508; border: 1px solid var(--border); border-radius: 8px; padding: 15px; margin-bottom: 20px; }
        .terminal-output { height: 350px; overflow-y: auto; font-family: monospace; font-size: 0.9rem; line-height: 1.4; color: var(--text-secondary); white-space: pre-wrap; }
        
        /* Overlay de Carga estilo Cyberpunk */
        #loadingOverlay {
            display: none;
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(13, 13, 18, 0.95);
            z-index: 9999;
            align-items: center; justify-content: center;
            flex-direction: column;
        }
        .spinner {
            width: 50px; height: 50px;
            border: 5px solid var(--bg-tertiary);
            border-top: 5px solid var(--primary);
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin-bottom: 20px;
        }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    </style>
</head>
<body>

    <div id="loadingOverlay">
        <div class="spinner"></div>
        <h2 style="color: var(--primary);">[PROCESANDO ESCANEO DE RED]</h2>
        <p style="color: var(--text-secondary); margin-top: 10px;">Ejecutando Nmap & Searchsploit en el servidor...</p>
    </div>

    <div class="container">
        <header>
            <h1>Cyberpunk Network Scanner</h1>
        </header>

        <section class="input-section">
            <form id="scanForm" action="/scan" method="POST">
                <div class="input-group">
                    <input type="text" name="target" placeholder="Ej: 127.0.0.1 o 192.168.35.1" value="{{ target or '127.0.0.1' }}" required>
                    <button type="submit" class="btn btn-primary">Ejecutar Escaneo</button>
                    {% if result %}
                    <a href="/download" class="btn btn-secondary">Descargar Reporte</a>
                    {% endif %}
                </div>
            </form>
        </section>

        <section class="terminal-container">
            <div class="terminal-output">
{% if result %}{{ result }}{% else %}[SYSTEM] Esperando objetivo... Haz clic en "Ejecutar Escaneo" para iniciar.{% endif %}
            </div>
        </section>
    </div>

    <script>
        document.getElementById('scanForm').addEventListener('submit', function() {
            document.getElementById('loadingOverlay').style.display = 'flex';
        });
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, result=LAST_RESULT, target=request.args.get('target', ''))

@app.route('/scan', methods=['POST'])
def scan():
    global LAST_RESULT
    target = request.form.get('target', '127.0.0.1').strip()
    target_clean = re.sub(r'[^a-zA-Z0-9\.\-]', '', target)

    output = f"=== INICIO DE ESCANEO EN {target_clean} ===\n"
    output += f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    # 1. Ejecutar Nmap
    nmap_raw_stdout = ""
    try:
        cmd_nmap = ["nmap", "-sS", "-sV", "-O", "-T4", target_clean]
        res_nmap = subprocess.run(cmd_nmap, capture_output=True, text=True, timeout=300)
        output += "--- RESULTADOS NMAP ---\n"
        nmap_raw_stdout = res_nmap.stdout if res_nmap.stdout else res_nmap.stderr
        output += nmap_raw_stdout
    except Exception as e:
        output += f"Error ejecutando Nmap: {str(e)}\n"

    output += "\n\n--- BÚSQUEDA EN SEARCHSPLOIT ---\n"
    # 2. Extraer servicios y versiones de Nmap para consultar Searchsploit servicio por servicio
    try:
        # Regex para capturar puerto, estado, servicio y versión completa de la tabla de Nmap
        port_lines = re.findall(r'^\d+/\w+\s+open\s+([^\s]+)\s+(.*)$', nmap_raw_stdout, re.MULTILINE)
        
        searched_services = set()
        if port_lines:
            for service, version_info in port_lines:
                version_clean = version_info.strip()
                # Construir término de búsqueda preferentemente con la versión específica o nombre del servicio
                if version_clean and not version_clean.startswith('?'):
                    query = f"{service} {version_clean}".split('(')[0].strip()
                else:
                    query = service.strip()
                
                # Evitar búsquedas duplicadas
                if query and query not in searched_services:
                    searched_services.add(query)
                    output += f"\n[+] Búsqueda para servicio: {query}\n"
                    cmd_sp = ["searchsploit", query]
                    res_sp = subprocess.run(cmd_sp, capture_output=True, text=True, timeout=30)
                    sp_stdout = res_sp.stdout.strip() if res_sp.stdout else ""
                    
                    if sp_stdout and "Exploits: No Results" not in sp_stdout:
                        output += sp_stdout + "\n"
                    else:
                        output += "No se encontraron exploits conocidos para este servicio.\n"
        
        if not searched_services:
            output += "No se detectaron servicios abiertos explícitos para consultar en Searchsploit.\n"

    except Exception as e:
        output += f"Error ejecutando Búsqueda en Searchsploit: {str(e)}\n"

    LAST_RESULT = output
    return redirect(url_for('index', target=target_clean))

@app.route('/download')
def download():
    if not LAST_RESULT:
        return "No hay reporte disponible", 400
    return Response(
        LAST_RESULT,
        mimetype="text/plain",
        headers={"Content-disposition": "attachment; filename=reporte_auditoria.txt"}
    )

if __name__ == '__main__':
    print("Servidor listo en puerto 9039...")
    app.run(host='0.0.0.0', port=9039, debug=True)
