import os
import time
import threading
import re
import json
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify, Response
import paramiko

app = Flask(__name__)

DB_FILE = "devices_db.json"
DEVICES = {}

DEFAULT_KEYWORDS = [
    "fail", "error", "wrong", "denied", "exception", "critical", "panic", "unreachable",
    "ccsp", "Crash Upper", "HAL_ERR", "bus reset", "fatal error", "radar detected", "DFS state",
    "netifd", "OOM", "firmware crashed", "DHCPDISCOVER", "No lease", "SOLICIT", "RA timeout"
]

DEFAULT_COMMAND = """while true; do
echo "-----";
echo "date-----";
date;
echo "-----";
echo "banner-----";
cat /etc/issue 2>/dev/null || cat /etc/banner 2>/dev/null || cat /etc/version 2>/dev/null || cat /etc/os-release 2>/dev/null;
echo "-----";
echo "etc_info-----";
for f in /etc/version /etc/sw_version /etc/firmware_version /etc/config_stats.json; do
    if [ -f "$f" ]; then
        echo "=== ETC: $f ===";
        cat "$f" 2>/dev/null;
    fi;
done;
echo "-----";
echo "uname-----";
uname -a;
echo "-----";
echo "uptime-----";
uptime;
echo "-----";
top -bn1 | head -n25;
echo "-----";
echo "var_log_all-----";
for f in /var/log/* /var/log/*/*; do
    if [ -f "$f" ]; then
        echo "=== LOG: $f ===";
        tail -n 100 "$f" 2>/dev/null;
    fi;
done;
echo "-----";
ifconfig;
echo "-----";
echo "wl0_bs-----";
wl -i wl0 bs_data 2>/dev/null;
echo "-----";
echo "wl1_bs-----";
wl -i wl1 bs_data 2>/dev/null;
echo "-----";
echo "wl2_bs-----";
wl -i wl2 bs_data 2>/dev/null;
echo "-----";
echo "wl0_status-----";
wl -i wl0 status 2>/dev/null;
echo "-----";
echo "wl1_status-----";
wl -i wl1 status 2>/dev/null;
echo "-----";
echo "wl2_status-----";
wl -i wl2 status 2>/dev/null;
echo "-----";
cat /proc/meminfo;
echo "-----";
echo "wl0_chanim-----";
wl -i wl0 chanim_stats 2>/dev/null;
echo "-----";
echo "wl1_chanim-----";
wl -i wl1 chanim_stats 2>/dev/null;
echo "-----";
echo "wl2_chanim-----";
wl -i wl2 chanim_stats 2>/dev/null;
echo "-----";
tail -n20 /rdklogs/logs/wifi_vendor_hal.log 2>/dev/null;
echo "-----";
tail -n20 /rdklogs/logs/wifi_vendor_apps.log 2>/dev/null;
echo "-----";
tail -n20 /rdklogs/logs/wifi_vendor.log 2>/dev/null;
echo "-----";
ping -c10 8.8.8.8;
echo "-----";
{CUSTOM_COMMANDS}
sleep 10;
done"""

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Monitor SSH Profesional (OpenWrt / prpl / SWAN / RDK)</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #1e1e2e; color: #cdd6f4; margin: 20px; }
        h1, h2 { color: #89b4fa; }
        .card { background: #313244; padding: 15px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
        .form-group { margin-bottom: 10px; }
        label { display: inline-block; width: 140px; font-weight: bold; }
        input[type="text"], input[type="number"], input[type="password"] {
            background: #181825; border: 1px solid #45475a; color: #cdd6f4; padding: 8px; border-radius: 4px; box-sizing: border-box;
        }
        input[type="text"], input[type="password"] { width: 200px; }
        .cmd-row { display: flex; gap: 5px; margin-bottom: 5px; align-items: center; }
        .cmd-row input[type="text"] { width: 100%; max-width: 600px; }
        button { background: #89b4fa; color: #11111b; border: none; padding: 6px 12px; border-radius: 4px; font-weight: bold; cursor: pointer; margin-right: 5px; margin-top: 5px; }
        button:hover { background: #b4befe; }
        button.stop { background: #f38ba8; color: #11111b; }
        button.start { background: #a6e3a1; color: #11111b; }
        button.download { background: #a6e3a1; color: #11111b; }
        button.action { background: #fab387; color: #11111b; }
        button.delete { background: #f38ba8; color: #11111b; float: right; }
        button.add-btn { background: #a6e3a1; color: #11111b; padding: 4px 8px; }
        button.remove-btn { background: #f38ba8; color: #11111b; padding: 4px 8px; }
        .device-card { background: #181825; border: 1px solid #45475a; padding: 15px; border-radius: 8px; margin-bottom: 15px; }
        .status { font-weight: bold; padding: 4px 8px; border-radius: 4px; display: inline-block; }
        .status-running { background: #a6e3a1; color: #11111b; }
        .status-reconnecting { background: #f9e2af; color: #11111b; }
        .status-stopped { background: #f38ba8; color: #11111b; }
        .status-error { background: #fab387; color: #11111b; }
        .status-dot { height: 14px; width: 14px; border-radius: 50%; display: inline-block; margin-right: 8px; vertical-align: middle; }
        .dot-green { background-color: #a6e3a1; box-shadow: 0 0 8px #a6e3a1; }
        .dot-red { background-color: #f38ba8; box-shadow: 0 0 8px #f38ba8; }
        .sys-info-box { background: #313244; border: 1px solid #45475a; border-radius: 6px; padding: 10px; margin: 10px 0; font-size: 0.9em; }
        .sys-info-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; margin-top: 5px; }
        .sys-item { background: #181825; padding: 6px 10px; border-radius: 4px; border-left: 3px solid #89b4fa; }
        .sys-label { font-size: 0.75em; color: #a6adc8; text-transform: uppercase; font-weight: bold; }
        .sys-val { font-size: 0.95em; font-weight: bold; color: #cdd6f4; word-break: break-all; }
        .kpi-container { display: flex; gap: 10px; flex-wrap: wrap; margin: 10px 0; }
        .kpi-card { background: #313244; border: 1px solid #45475a; border-radius: 6px; padding: 8px 12px; min-width: 130px; text-align: center; }
        .kpi-title { font-size: 0.75em; color: #a6adc8; text-transform: uppercase; font-weight: bold; }
        .kpi-value { font-size: 1.05em; font-weight: bold; color: #89b4fa; margin-top: 3px; }
        .log-box { background: #11111b; color: #a6e3a1; font-family: 'Courier New', monospace; padding: 10px; height: 250px; overflow-y: scroll; border-radius: 4px; white-space: pre-wrap; margin-top: 10px; }
        .alert-box { background: #181825; border: 1px solid #f38ba8; color: #f38ba8; font-family: monospace; padding: 8px; max-height: 100px; overflow-y: auto; border-radius: 4px; margin-top: 5px; font-size: 0.85em; }
        .alert-count { background: #f38ba8; color: #11111b; border-radius: 4px; padding: 1px 5px; font-weight: bold; margin-left: 5px; }
        .charts-wrapper { display: flex; gap: 15px; margin-top: 10px; flex-wrap: wrap; }
        .chart-box { background: #181825; padding: 10px; border-radius: 6px; flex: 1; min-width: 280px; height: 160px; }
    </style>
</head>
<body>

    <h1>🖥️ Monitor SSH Profesional (OpenWrt / prpl / SWAN / RDK)</h1>

    <div class="card">
        <h2>Adicionar Equipo a Monitorear</h2>
        <form id="add-device-form">
            <div style="display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 10px;">
                <div><label>IP:</label><input type="text" id="ip" required placeholder="192.168.1.1"></div>
                <div><label>Puerto SSH:</label><input type="number" id="port" value="22" required></div>
                <div><label>Usuario:</label><input type="text" id="user" required value="root"></div>
                <div><label>Password:</label><input type="password" id="password" required></div>
            </div>

            <div class="form-group">
                <label style="width: 100%;">Comandos Adicionales Personalizados:</label>
                <div id="add-custom-cmds-container">
                    <div class="cmd-row">
                        <input type="text" class="add-custom-cmd-input" placeholder="Ej: cat /proc/uptime o wl -i wl0 status">
                        <button type="button" class="add-btn" onclick="addCustomCmdRow('add-custom-cmds-container')">+</button>
                    </div>
                </div>
            </div>

            <div class="form-group">
                <label style="width: 100%;">Palabras Clave de Alerta (separadas por coma):</label>
                <input type="text" id="keywords" style="width: 100%;" value="{{ default_keywords }}">
            </div>
            <button type="button" onclick="addDevice()">Conectar y Monitorear</button>
            <button type="button" onclick="requestNotificationPermission()">Activar Notificaciones Web</button>
        </form>
    </div>

    <h2>Equipos en Monitoreo</h2>
    <div id="devices-container"></div>

    <script>
        const charts = {};
        const deviceStatusState = {};

        function addCustomCmdRow(containerId, value = '') {
            const container = document.getElementById(containerId);
            const row = document.createElement('div');
            row.className = 'cmd-row';
            row.innerHTML = `
                <input type="text" class="${containerId === 'add-custom-cmds-container' ? 'add-custom-cmd-input' : 'dev-custom-cmd-input'}" value="${value}" placeholder="Ej: cat /proc/uptime">
                <button type="button" class="add-btn" onclick="addCustomCmdRow('${containerId}')">+</button>
                <button type="button" class="remove-btn" onclick="removeCustomCmdRow(this)">🗑️</button>
            `;
            container.appendChild(row);
        }

        function removeCustomCmdRow(btn) {
            const row = btn.parentElement;
            const container = row.parentElement;
            if (container.children.length > 1) {
                row.remove();
            } else {
                row.querySelector('input').value = '';
            }
        }

        function getCustomCmds(containerId) {
            const inputs = document.querySelectorAll(`#${containerId} input`);
            const cmds = [];
            inputs.forEach(input => {
                if (input.value.trim() !== '') {
                    cmds.push(input.value.trim());
                }
            });
            return cmds;
        }

        function playBeep() {
            try {
                const ctx = new (window.AudioContext || window.webkitAudioContext)();
                const osc = ctx.createOscillator();
                osc.type = "sine";
                osc.frequency.setValueAtTime(880, ctx.currentTime);
                osc.connect(ctx.destination);
                osc.start();
                osc.stop(ctx.currentTime + 0.3);
            } catch(e) {}
        }

        function requestNotificationPermission() {
            if ("Notification" in window) {
                Notification.requestPermission().then(p => alert("Permiso de notificaciones: " + p));
            }
        }

        function sendWebNotification(title, body) {
            if ("Notification" in window && Notification.permission === "granted") {
                new Notification(title, { body: body });
            }
        }

        function addDevice() {
            const customCmds = getCustomCmds('add-custom-cmds-container');
            const data = {
                ip: document.getElementById('ip').value,
                port: document.getElementById('port').value,
                user: document.getElementById('user').value,
                password: document.getElementById('password').value,
                custom_commands: customCmds,
                keywords: document.getElementById('keywords').value
            };

            fetch('/api/devices/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            })
            .then(res => res.json())
            .then(res => {
                if(res.error) alert(res.error);
                updateDevices();
            });
        }

        function stopDevice(id) { fetch(`/api/devices/stop/${id}`, { method: 'POST' }).then(() => updateDevices()); }
        function startDevice(id) { fetch(`/api/devices/start/${id}`, { method: 'POST' }).then(() => updateDevices()); }
        function deleteDevice(id) { fetch(`/api/devices/delete/${id}`, { method: 'DELETE' }).then(() => updateDevices()); }

        function sendQuickCommand(id, cmd) {
            fetch(`/api/devices/inject_command/${id}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: cmd })
            })
            .then(res => res.json())
            .then(res => { if(res.error) alert(res.error); });
        }

        function updateCommand(id) {
            const safeId = id.replace(/[:\.]/g, '_');
            const customCmds = getCustomCmds(`custom-cmds-container-${safeId}`);
            fetch(`/api/devices/update_command/${id}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ custom_commands: customCmds })
            })
            .then(res => res.json())
            .then(res => {
                if(res.error) alert(res.error);
                else alert("Comandos personalizados actualizados y monitoreo reiniciado.");
                updateDevices();
            });
        }

        function downloadLog(id) { window.location.href = `/api/devices/download_log/${id}`; }
        function downloadAlerts(id) { window.location.href = `/api/devices/download_alerts/${id}`; }

        function initCharts(id) {
            const ctxNet = document.getElementById(`chart-net-${id}`);
            const ctxWifi = document.getElementById(`chart-wifi-${id}`);
            if (!ctxNet || !ctxWifi) return;

            charts[id] = {
                net: new Chart(ctxNet, {
                    type: 'line',
                    data: { labels: [], datasets: [{ label: 'RTT Ping (ms)', data: [], borderColor: '#89b4fa', tension: 0.2 }] },
                    options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: true } } }
                }),
                wifi: new Chart(ctxWifi, {
                    type: 'line',
                    data: { labels: [], datasets: [{ label: 'Wi-Fi Chan %', data: [], borderColor: '#a6e3a1', tension: 0.2 }] },
                    options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: true, max: 100 } } }
                })
            };
        }

        function safeUpdateText(elementId, val) {
            const el = document.getElementById(elementId);
            if (el && val && val !== 'N/A' && val !== '' && val !== 'Detectando...') {
                el.textContent = val;
            }
        }

        function fetchLogs(id) {
            fetch(`/api/devices/logs/${id}`)
            .then(res => res.json())
            .then(data => {
                const logBox = document.getElementById(`log-${id}`);
                const alertsDiv = document.getElementById(`alerts-${id}`);
                const statusSpan = document.getElementById(`status-${id}`);
                const dotSpan = document.getElementById(`dot-${id}`);

                if (logBox) {
                    const isScrolledToBottom = logBox.scrollHeight - logBox.clientHeight <= logBox.scrollTop + 50;
                    logBox.textContent = data.logs;
                    if (isScrolledToBottom) logBox.scrollTop = logBox.scrollHeight;
                }

                if (alertsDiv) {
                    if (data.alert_lines && data.alert_lines.length > 0) {
                        const grouped = [];
                        data.alert_lines.forEach(item => {
                            const existing = grouped.find(g => g.keyword === item.keyword && g.line === item.line && g.command === item.command);
                            if (existing) {
                                existing.count++;
                                existing.lastTime = item.time;
                            } else {
                                grouped.push({ time: item.time, lastTime: item.time, keyword: item.keyword, line: item.line, command: item.command || 'General / Desconocido', count: 1 });
                            }
                        });
                        alertsDiv.innerHTML = grouped.map(a => {
                            const badge = a.count > 1 ? `<span class="alert-count">(x${a.count})</span>` : '';
                            return `<div>[${a.time}] Encontrado: "${a.keyword}" -> ${a.line} <br><small style="color:#89b4fa;">Cmd: ${a.command}</small>${badge}</div>`;
                        }).join('');
                    } else {
                        alertsDiv.innerHTML = '<em>Sin incidencias detectadas</em>';
                    }
                }

                if (deviceStatusState[id] !== undefined && !deviceStatusState[id] && data.has_errors) {
                    playBeep();
                    sendWebNotification(`Alerta en equipo ${id}`, `Se detectaron fallas o desconexión en ${id}`);
                }
                deviceStatusState[id] = data.has_errors;

                if (dotSpan) dotSpan.className = `status-dot ${data.has_errors ? 'dot-red' : 'dot-green'}`;
                if (statusSpan) {
                    statusSpan.className = `status status-${data.status}`;
                    statusSpan.textContent = data.status.toUpperCase();
                }

                if (data.metrics) {
                    safeUpdateText(`sys-version-${id}`, data.metrics.sys_version);
                    safeUpdateText(`sys-kernel-${id}`, data.metrics.sys_kernel);
                    safeUpdateText(`sys-uptime-${id}`, data.metrics.sys_uptime);
                    safeUpdateText(`sys-cpuload-${id}`, data.metrics.sys_cpuload);

                    document.getElementById(`kpi-ram-${id}`).textContent = data.metrics.ram_pct + '%';
                    document.getElementById(`kpi-drops-${id}`).textContent = data.metrics.drops;
                    document.getElementById(`kpi-ping-${id}`).textContent = data.metrics.ping_rtt + ' ms (' + data.metrics.ping_loss + '%)';
                    document.getElementById(`kpi-wifi-${id}`).textContent = data.metrics.wifi_chan + '%';
                    document.getElementById(`kpi-noise-${id}`).textContent = data.metrics.wifi_noise + ' dBm';

                    safeUpdateText(`kpi-wl0-${id}`, data.metrics.wl0_info);
                    safeUpdateText(`kpi-wl1-${id}`, data.metrics.wl1_info);
                    safeUpdateText(`kpi-wl2-${id}`, data.metrics.wl2_info);

                    if (charts[id]) {
                        const now = new Date().toLocaleTimeString();
                        const cNet = charts[id].net;
                        const cWifi = charts[id].wifi;

                        if (cNet.data.labels.length > 15) { cNet.data.labels.shift(); cNet.data.datasets[0].data.shift(); }
                        cNet.data.labels.push(now);
                        cNet.data.datasets[0].data.push(data.metrics.ping_rtt);
                        cNet.update();

                        if (cWifi.data.labels.length > 15) { cWifi.data.labels.shift(); cWifi.data.datasets[0].data.shift(); }
                        cWifi.data.labels.push(now);
                        cWifi.data.datasets[0].data.push(data.metrics.wifi_chan);
                        cWifi.update();
                    }
                }
            });
        }

        function updateDevices() {
            fetch('/api/devices')
            .then(res => res.json())
            .then(devices => {
                const container = document.getElementById('devices-container');
                container.innerHTML = '';

                Object.keys(devices).forEach(id => {
                    const dev = devices[id];
                    const safeId = id.replace(/[:\.]/g, '_');
                    const devEl = document.createElement('div');
                    devEl.className = 'device-card';

                    const customCmds = dev.custom_commands || [];
                    let cmdsHtml = '';
                    if (customCmds.length > 0) {
                        customCmds.forEach(cmd => {
                            cmdsHtml += `
                                <div class="cmd-row">
                                    <input type="text" class="dev-custom-cmd-input" value="${cmd}">
                                    <button type="button" class="add-btn" onclick="addCustomCmdRow('custom-cmds-container-${safeId}')">+</button>
                                    <button type="button" class="remove-btn" onclick="removeCustomCmdRow(this)">🗑️</button>
                                </div>`;
                        });
                    } else {
                        cmdsHtml = `
                            <div class="cmd-row">
                                <input type="text" class="dev-custom-cmd-input" placeholder="Ej: cat /proc/uptime">
                                <button type="button" class="add-btn" onclick="addCustomCmdRow('custom-cmds-container-${safeId}')">+</button>
                                <button type="button" class="remove-btn" onclick="removeCustomCmdRow(this)">🗑️</button>
                            </div>`;
                    }

                    devEl.innerHTML = `
                        <button class="delete" onclick="deleteDevice('${id}')">Borrar Equipo</button>
                        <h3>
                            <span id="dot-${id}" class="status-dot ${dev.has_errors ? 'dot-red' : 'dot-green'}"></span>
                            ${dev.ip}:${dev.port} (${dev.user}) 
                            <span id="status-${id}" class="status status-${dev.status}">${dev.status.toUpperCase()}</span>
                        </h3>

                        <!-- Sección Independiente de Datos del Sistema y Software -->
                        <div class="sys-info-box">
                            <strong style="color: #89b4fa;">📋 Información Principal del Sistema:</strong>
                            <div class="sys-info-grid">
                                <div class="sys-item"><div class="sys-label">Versión / OS</div><div class="sys-val" id="sys-version-${id}">Detectando...</div></div>
                                <div class="sys-item"><div class="sys-label">Kernel</div><div class="sys-val" id="sys-kernel-${id}">Detectando...</div></div>
                                <div class="sys-item"><div class="sys-label">Uptime</div><div class="sys-val" id="sys-uptime-${id}">Detectando...</div></div>
                                <div class="sys-item"><div class="sys-label">CPU Load</div><div class="sys-val" id="sys-cpuload-${id}">-</div></div>
                            </div>
                        </div>

                        <!-- Tarjetas KPIs Visuales Generales y Wi-Fi Tribanda -->
                        <div class="kpi-container">
                            <div class="kpi-card"><div class="kpi-title">RAM Usada</div><div class="kpi-value" id="kpi-ram-${id}">-</div></div>
                            <div class="kpi-card"><div class="kpi-title">Drops Net</div><div class="kpi-value" id="kpi-drops-${id}">-</div></div>
                            <div class="kpi-card"><div class="kpi-title">Ping RTT / Loss</div><div class="kpi-value" id="kpi-ping-${id}">-</div></div>
                            <div class="kpi-card"><div class="kpi-title">Wi-Fi Chan Util</div><div class="kpi-value" id="kpi-wifi-${id}">-</div></div>
                            <div class="kpi-card"><div class="kpi-title">Noise Floor</div><div class="kpi-value" id="kpi-noise-${id}">-</div></div>
                            <div class="kpi-card"><div class="kpi-title">wl0 Status</div><div class="kpi-value" id="kpi-wl0-${id}">-</div></div>
                            <div class="kpi-card"><div class="kpi-title">wl1 Status</div><div class="kpi-value" id="kpi-wl1-${id}">-</div></div>
                            <div class="kpi-card"><div class="kpi-title">wl2 Status</div><div class="kpi-value" id="kpi-wl2-${id}">-</div></div>
                        </div>

                        <!-- Acciones Rápida Directas -->
                        <div style="margin-bottom: 8px;">
                            <strong>Comandos Rápidos:</strong>
                            <button class="action" onclick="sendQuickCommand('${id}', 'wl -i wl0 assoclist || iw dev wlan0 station dump')">STAs Conectadas</button>
                            <button class="action" onclick="sendQuickCommand('${id}', 'logread || dmesg | tail -n30')">Log Kernel/OS</button>
                            <button class="action" onclick="sendQuickCommand('${id}', 'top -bn1 | head -n15')">Top Procesos</button>
                        </div>

                        <div style="margin-bottom: 10px;">
                            <label style="width: 100%;"><strong>Comandos Adicionales Personalizados:</strong></label>
                            <div id="custom-cmds-container-${safeId}">
                                ${cmdsHtml}
                            </div>
                            <button onclick="updateCommand('${id}')">Actualizar Comandos</button>
                        </div>

                        <p><strong>Alertas y Puntos de Error Detectados:</strong></p>
                        <div id="alerts-${id}" class="alert-box">Cargando alertas...</div>

                        <!-- Gráficas de Tendencia -->
                        <div class="charts-wrapper">
                            <div class="chart-box"><canvas id="chart-net-${id}"></canvas></div>
                            <div class="chart-box"><canvas id="chart-wifi-${id}"></canvas></div>
                        </div>

                        <div style="margin-top: 10px;">
                            ${dev.status === 'stopped' ? `<button class="start" onclick="startDevice('${id}')">Continuar Monitoreo</button>` : `<button class="stop" onclick="stopDevice('${id}')">Detener Monitoreo</button>`}
                            <button class="download" onclick="downloadLog('${id}')">Descargar Log Completo</button>
                            <button class="download" onclick="downloadAlerts('${id}')">Descargar Puntos de Error</button>
                        </div>

                        <div class="log-box" id="log-${id}">Cargando logs...</div>
                    `;
                    container.appendChild(devEl);
                    initCharts(id);
                    fetchLogs(id);
                });
            });
        }

        setInterval(() => {
            fetch('/api/devices')
            .then(res => res.json())
            .then(devices => {
                Object.keys(devices).forEach(id => fetchLogs(id));
            });
        }, 2000);

        updateDevices();
    </script>
</body>
</html>
"""


def build_full_command(custom_commands):
    custom_str = ""
    if custom_commands:
        for cmd in custom_commands:
            if cmd.strip():
                custom_str += f'echo "=== CUSTOM: {cmd.strip()} ===";\n'
                custom_str += f'{cmd.strip()};\n'
                custom_str += 'echo "-----";\n'
    return DEFAULT_COMMAND.replace("{CUSTOM_COMMANDS}", custom_str)


def parse_wifi_band_and_status(status_text):
    """ Extrae directamente la línea Chanspec (ej. 5GHz channel 114 160MHz) o evalúa si la interfaz no existe """
    if not status_text or "not found" in status_text.lower() or "no such device" in status_text.lower():
        return "No presente"

    chanspec_match = re.search(r'Chanspec:\s*([^\n\r]+)', status_text, re.IGNORECASE)
    if chanspec_match:
        raw_chanspec = chanspec_match.group(1).strip()
        clean_chanspec = re.sub(r'\s*\([0-9a-fxA-FX]+\)', '', raw_chanspec)
        return clean_chanspec

    chan_match = re.search(r'Channel:\s*([^\n\r]+)', status_text, re.IGNORECASE)
    if chan_match:
        return f"Ch: {chan_match.group(1).strip()}"

    return "Activa (Sin Chanspec)"


def parse_metrics_from_output(output, prev_metrics=None):
    """ Expresiones regulares para extraer sistema, versión, RAM, CPU y métricas Wi-Fi Tribanda """
    metrics = {
        'sys_version': 'N/A',
        'sys_kernel': 'N/A',
        'sys_uptime': 'N/A',
        'sys_cpuload': 'N/A',
        'ram_pct': 0,
        'drops': 0,
        'ping_rtt': 0,
        'ping_loss': 0,
        'wifi_chan': 0,
        'wifi_noise': 0,
        'wl0_info': 'N/A',
        'wl1_info': 'N/A',
        'wl2_info': 'N/A'
    }

    if prev_metrics:
        metrics.update(prev_metrics)

    ver_match = re.search(r'Version:\s*([^\n\r]+)', output, re.IGNORECASE)
    if ver_match:
        metrics['sys_version'] = ver_match.group(1).strip()[:45]
    else:
        banner_match = re.search(r'banner-----\s*\n(.*?)\n-----', output, re.DOTALL)
        if banner_match:
            raw_banner = banner_match.group(1).strip()
            first_lines = [line.strip() for line in raw_banner.split('\n') if
                           line.strip() and not line.startswith('-') and not line.startswith(
                               '_') and not line.startswith('\\') and not line.startswith('|')]
            if first_lines:
                metrics['sys_version'] = first_lines[0][:40]

    uname_match = re.search(r'Linux\s+[^\s]+\s+([^\s]+)', output)
    if uname_match:
        metrics['sys_kernel'] = uname_match.group(1)

    uptime_match = re.search(r'uptime-----\s*\n([^\n]+)', output)
    if uptime_match:
        raw_upt = uptime_match.group(1).strip()
        load_split = re.search(r'load average:\s*(.+)', raw_upt, re.IGNORECASE)
        if load_split:
            metrics['sys_cpuload'] = load_split.group(1).strip()
            up_part = re.search(r'up\s+(.+?),\s*load', raw_upt, re.IGNORECASE)
            if up_part:
                metrics['sys_uptime'] = up_part.group(1).strip()
            else:
                metrics['sys_uptime'] = raw_upt.split(',')[0].replace('uptime', '').strip()
        else:
            metrics['sys_uptime'] = raw_upt

    if metrics['sys_cpuload'] == 'N/A':
        load_top = re.search(r'Load average:\s*([\d\.,\s]+)', output, re.IGNORECASE)
        if load_top:
            metrics['sys_cpuload'] = load_top.group(1).strip()

    mem_total = re.search(r'MemTotal:\s*(\d+)', output)
    mem_avail = re.search(r'MemAvailable:\s*(\d+)', output)
    if mem_total and mem_avail:
        total = int(mem_total.group(1))
        avail = int(mem_avail.group(1))
        if total > 0:
            metrics['ram_pct'] = round(((total - avail) / total) * 100, 1)

    drops_matches = re.findall(r'(?:dropped|errors):\s*(\d+)', output, re.IGNORECASE)
    if drops_matches:
        metrics['drops'] = sum(int(d) for d in drops_matches)

    rtt_match = re.search(r'min/avg/max = [\d\.]+/([\d\.]+)/[\d\.]+', output)
    loss_match = re.search(r'(\d+)% packet loss', output)
    if rtt_match:
        metrics['ping_rtt'] = float(rtt_match.group(1))
    if loss_match:
        metrics['ping_loss'] = int(loss_match.group(1))

    chan_match = re.search(r'chanim_stats.*?(\d+)%\s+glitch', output, re.DOTALL)
    noise_match = re.search(r'noise:\s*(-?\d+)', output, re.IGNORECASE)
    if chan_match:
        metrics['wifi_chan'] = int(chan_match.group(1))
    if noise_match:
        metrics['wifi_noise'] = int(noise_match.group(1))

    wl0_match = re.search(r'wl0_status-----\s*\n(.*?)(?=\n-----|\Z)', output, re.DOTALL)
    wl1_match = re.search(r'wl1_status-----\s*\n(.*?)(?=\n-----|\Z)', output, re.DOTALL)
    wl2_match = re.search(r'wl2_status-----\s*\n(.*?)(?=\n-----|\Z)', output, re.DOTALL)

    if wl0_match:
        metrics['wl0_info'] = parse_wifi_band_and_status(wl0_match.group(1))
    if wl1_match:
        metrics['wl1_info'] = parse_wifi_band_and_status(wl1_match.group(1))
    if wl2_match:
        metrics['wl2_info'] = parse_wifi_band_and_status(wl2_match.group(1))

    return metrics


def save_devices_to_disk():
    data_to_save = {}
    for dev_id, dev in DEVICES.items():
        data_to_save[dev_id] = {
            'ip': dev['ip'],
            'port': dev['port'],
            'user': dev['user'],
            'password': dev['password'],
            'custom_commands': dev.get('custom_commands', []),
            'keywords': dev['keywords']
        }
    try:
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(data_to_save, f, indent=4)
    except Exception as e:
        print(f"Error guardando base de datos: {e}")


def load_devices_from_disk():
    if not os.path.exists(DB_FILE):
        return
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            saved_data = json.load(f)
            for dev_id, data in saved_data.items():
                custom_cmds = data.get('custom_commands', [])
                full_cmd = build_full_command(custom_cmds)
                DEVICES[dev_id] = {
                    'ip': data['ip'],
                    'port': data['port'],
                    'user': data['user'],
                    'password': data['password'],
                    'custom_commands': custom_cmds,
                    'command': full_cmd,
                    'keywords': data['keywords'],
                    'logs': f"[SISTEMA]: Servidor/Script reiniciado. Reorientando monitoreo...\n",
                    'alert_lines': [],
                    'metrics': {'sys_version': 'N/A', 'sys_kernel': 'N/A', 'sys_uptime': 'N/A', 'sys_cpuload': 'N/A',
                                'ram_pct': 0, 'drops': 0, 'ping_rtt': 0, 'ping_loss': 0, 'wifi_chan': 0,
                                'wifi_noise': 0,
                                'wl0_info': 'N/A', 'wl1_info': 'N/A', 'wl2_info': 'N/A'},
                    'has_errors': False,
                    'status': 'connecting',
                    'running': True,
                    'ssh_channel': None
                }
                t = threading.Thread(
                    target=ssh_worker,
                    args=(dev_id, data['ip'], data['port'], data['user'], data['password'], full_cmd,
                          data['keywords']),
                    daemon=True
                )
                t.start()
    except Exception as e:
        print(f"Error cargando base de datos: {e}")


def ssh_worker(device_id, ip, port, user, password, command, keywords):
    ifconfig_err_pattern = re.compile(
        r'(errors:|\bdropped:\b|\boverruns:\b|\bcarrier:\b|RX errors|TX errors|RX dropped|TX dropped)',
        re.IGNORECASE)
    ping_err_pattern = re.compile(
        r'(100% packet loss|Destination Host Unreachable|Network is unreachable|Request timeout|ping: bad address)',
        re.IGNORECASE)

    was_connected_before = False
    current_command = "Inicio de monitoreo"

    while DEVICES.get(device_id, {}).get('running', False):
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            DEVICES[device_id]['status'] = 'connecting' if not was_connected_before else 'reconnecting'

            ssh.connect(
                ip,
                port=int(port),
                username=user,
                password=password,
                timeout=10,
                banner_timeout=10,
                auth_timeout=10
            )

            transport = ssh.get_transport()
            if transport:
                transport.set_keepalive(5)

            DEVICES[device_id]['status'] = 'running'

            if was_connected_before:
                ts_recon = datetime.now().strftime("%H:%M:%S")
                recon_msg = f"Conexión SSH RESTABLECIDA exitosamente a las {ts_recon}"
                DEVICES[device_id]['alert_lines'].append({
                    'time': ts_recon,
                    'keyword': 'SSH_RECONNECTED',
                    'line': recon_msg,
                    'command': 'Conexión SSH'
                })
                DEVICES[device_id]['logs'] += f"\n[SISTEMA {ts_recon}]: {recon_msg}\n"

            was_connected_before = True

            channel = ssh.invoke_shell()
            channel.settimeout(2.0)
            channel.send(command + "\n")
            DEVICES[device_id]['ssh_channel'] = channel

            line_buffer = ""

            while DEVICES.get(device_id, {}).get('running', False):
                if not transport or not transport.is_active():
                    raise Exception("Conexión perdida con el host remoto")

                try:
                    if channel.recv_ready():
                        output = channel.recv(4096).decode('utf-8', errors='ignore')
                        DEVICES[device_id]['logs'] += output
                        line_buffer += output

                        if len(DEVICES[device_id]['logs']) > 2000000:
                            DEVICES[device_id]['logs'] = DEVICES[device_id]['logs'][-2000000:]

                        prev = DEVICES[device_id].get('metrics', {})
                        DEVICES[device_id]['metrics'] = parse_metrics_from_output(DEVICES[device_id]['logs'][-200000:],
                                                                                  prev_metrics=prev)

                        if "\n" in line_buffer:
                            lines = line_buffer.split("\n")
                            line_buffer = lines[-1]

                            for line in lines[:-1]:
                                clean_line = line.strip()
                                if not clean_line:
                                    continue

                                if clean_line.startswith("=== LOG:") or clean_line.startswith(
                                        "=== ETC:") or clean_line.startswith("=== CUSTOM:"):
                                    current_command = clean_line
                                elif clean_line.endswith("-----") and not clean_line.startswith("-----"):
                                    current_command = clean_line.replace("-----", "")

                                for kw in keywords:
                                    if re.search(r'\b' + re.escape(kw) + r'\b', clean_line, re.IGNORECASE):
                                        ts = datetime.now().strftime("%H:%M:%S")
                                        DEVICES[device_id]['alert_lines'].append({
                                            'time': ts,
                                            'keyword': kw,
                                            'line': clean_line,
                                            'command': current_command
                                        })
                                        DEVICES[device_id]['has_errors'] = True

                                if ifconfig_err_pattern.search(clean_line):
                                    counts = re.findall(r'(errors|dropped|overruns):\s*([1-9]\d*)', clean_line,
                                                        re.IGNORECASE)
                                    if counts or "dropped" in clean_line.lower() or "errors" in clean_line.lower():
                                        if not any(c in clean_line for c in
                                                   ["errors:0", "dropped:0", "overruns:0", "carrier:0", "RX errors 0",
                                                    "TX errors 0", "RX dropped 0", "TX dropped 0"]):
                                            ts = datetime.now().strftime("%H:%M:%S")
                                            DEVICES[device_id]['alert_lines'].append({
                                                'time': ts,
                                                'keyword': 'NET_IFCONFIG_DROP_ERR',
                                                'line': clean_line,
                                                'command': current_command
                                            })
                                            DEVICES[device_id]['has_errors'] = True

                                if ping_err_pattern.search(clean_line):
                                    ts = datetime.now().strftime("%H:%M:%S")
                                    DEVICES[device_id]['alert_lines'].append({
                                        'time': ts,
                                        'keyword': 'PING_NET_DOWN',
                                        'line': clean_line,
                                        'command': current_command
                                    })
                                    DEVICES[device_id]['has_errors'] = True

                except Exception:
                    pass

                time.sleep(0.5)

        except Exception as e:
            if DEVICES.get(device_id, {}).get('running', False):
                ts_disc = datetime.now().strftime("%H:%M:%S")
                err_msg = f"EQUIPO DESCONECTADO A LAS {ts_disc}. Motivo: {str(e)}"

                if not DEVICES[device_id]['alert_lines'] or DEVICES[device_id]['alert_lines'][-1].get(
                        'keyword') != 'SSH_DISCONNECTED':
                    DEVICES[device_id]['alert_lines'].append({
                        'time': ts_disc,
                        'keyword': 'SSH_DISCONNECTED',
                        'line': err_msg,
                        'command': 'Conexión SSH'
                    })

                DEVICES[device_id]['has_errors'] = True
                DEVICES[device_id]['status'] = 'reconnecting'
                DEVICES[device_id][
                    'logs'] += f"\n[DESCONEXION {ts_disc}]: Reintentando conexión SSH en 5 segundos... ({str(e)})\n"
                time.sleep(5)

        finally:
            try:
                if 'channel' in locals() and channel:
                    channel.close()
                ssh.close()
            except Exception:
                pass

    if DEVICES.get(device_id):
        DEVICES[device_id]['status'] = 'stopped'


@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE,
                                  default_keywords=", ".join(DEFAULT_KEYWORDS))


@app.route('/api/devices', methods=['GET'])
def get_devices():
    res = {}
    for dev_id, dev in DEVICES.items():
        res[dev_id] = {
            'ip': dev['ip'],
            'port': dev['port'],
            'user': dev['user'],
            'status': dev['status'],
            'custom_commands': dev.get('custom_commands', []),
            'has_errors': dev.get('has_errors', False)
        }
    return jsonify(res)


@app.route('/api/devices/logs/<device_id>', methods=['GET'])
def get_device_logs(device_id):
    if device_id in DEVICES:
        return jsonify({
            'logs': DEVICES[device_id]['logs'],
            'alert_lines': DEVICES[device_id]['alert_lines'],
            'status': DEVICES[device_id]['status'],
            'has_errors': DEVICES[device_id]['has_errors'],
            'metrics': DEVICES[device_id].get('metrics', {})
        })
    return jsonify({'error': 'Not found'}), 404


@app.route('/api/devices/inject_command/<device_id>', methods=['POST'])
def inject_command(device_id):
    if device_id in DEVICES and DEVICES[device_id].get('ssh_channel'):
        cmd = request.json.get('command')
        try:
            DEVICES[device_id]['ssh_channel'].send(cmd + "\n")
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    return jsonify({'error': 'Equipo no conectado o inactivo'}), 404


@app.route('/api/devices/add', methods=['POST'])
def add_device():
    data = request.json
    device_id = f"{data['ip']}:{data['port']}"

    if device_id in DEVICES and DEVICES[device_id]['running']:
        return jsonify({'error': 'El equipo ya está siendo monitoreado'}), 400

    keywords = [k.strip() for k in data.get('keywords', '').split(',') if k.strip()]
    custom_cmds = data.get('custom_commands', [])
    full_cmd = build_full_command(custom_cmds)

    DEVICES[device_id] = {
        'ip': data['ip'],
        'port': data['port'],
        'user': data['user'],
        'password': data['password'],
        'custom_commands': custom_cmds,
        'command': full_cmd,
        'keywords': keywords,
        'logs': "",
        'alert_lines': [],
        'metrics': {'sys_version': 'N/A', 'sys_kernel': 'N/A', 'sys_uptime': 'N/A', 'sys_cpuload': 'N/A',
                    'ram_pct': 0, 'drops': 0, 'ping_rtt': 0, 'ping_loss': 0, 'wifi_chan': 0, 'wifi_noise': 0,
                    'wl0_info': 'N/A', 'wl1_info': 'N/A', 'wl2_info': 'N/A'},
        'has_errors': False,
        'status': 'connecting',
        'running': True,
        'ssh_channel': None
    }

    save_devices_to_disk()

    t = threading.Thread(
        target=ssh_worker,
        args=(device_id, data['ip'], data['port'], data['user'], data['password'], full_cmd, keywords),
        daemon=True
    )
    t.start()

    return jsonify({'success': True, 'device_id': device_id})


@app.route('/api/devices/update_command/<device_id>', methods=['POST'])
def update_command(device_id):
    if device_id not in DEVICES:
        return jsonify({'error': 'Equipo no encontrado'}), 404

    data = request.json
    custom_cmds = data.get('custom_commands', [])
    full_cmd = build_full_command(custom_cmds)

    DEVICES[device_id]['running'] = False
    time.sleep(1)

    DEVICES[device_id]['custom_commands'] = custom_cmds
    DEVICES[device_id]['command'] = full_cmd
    DEVICES[device_id]['running'] = True
    DEVICES[device_id]['status'] = 'connecting'

    save_devices_to_disk()

    t = threading.Thread(
        target=ssh_worker,
        args=(
            device_id,
            DEVICES[device_id]['ip'],
            DEVICES[device_id]['port'],
            DEVICES[device_id]['user'],
            DEVICES[device_id]['password'],
            full_cmd,
            DEVICES[device_id]['keywords']
        ),
        daemon=True
    )
    t.start()

    return jsonify({'success': True})


@app.route('/api/devices/download_log/<device_id>', methods=['GET'])
def download_log(device_id):
    if device_id in DEVICES:
        content = DEVICES[device_id]['logs']
        filename = f"log_{device_id.replace(':', '_')}.txt"
        return Response(
            content,
            mimetype="text/plain",
            headers={"Content-disposition": f"attachment; filename={filename}"}
        )
    return jsonify({'error': 'Not found'}), 404


@app.route('/api/devices/download_alerts/<device_id>', methods=['GET'])
def download_alerts(device_id):
    if device_id in DEVICES:
        lines = DEVICES[device_id]['alert_lines']
        content = f"REGISTRO DE ALERTAS Y PUNTOS DE ERROR PARA EQUIPO {device_id}\n"
        content += "=" * 60 + "\n\n"
        for item in lines:
            cmd_info = item.get('command', 'General / Desconocido')
            content += f"[{item['time']}] Criterio: {item['keyword']}\nComando ejecutado: {cmd_info}\nLínea: {item['line']}\n"
            content += "-" * 40 + "\n"

        filename = f"errores_{device_id.replace(':', '_')}.txt"
        return Response(
            content,
            mimetype="text/plain",
            headers={"Content-disposition": f"attachment; filename={filename}"}
        )
    return jsonify({'error': 'Not found'}), 404


@app.route('/api/devices/stop/<device_id>', methods=['POST'])
def stop_device(device_id):
    if device_id in DEVICES:
        DEVICES[device_id]['running'] = False
        DEVICES[device_id]['status'] = 'stopped'
    return jsonify({'success': True})


@app.route('/api/devices/start/<device_id>', methods=['POST'])
def start_device(device_id):
    if device_id in DEVICES:
        if DEVICES[device_id]['running']:
            return jsonify({'success': True, 'message': 'El monitoreo ya está activo'})

        DEVICES[device_id]['running'] = True
        DEVICES[device_id]['status'] = 'connecting'

        full_cmd = build_full_command(DEVICES[device_id].get('custom_commands', []))
        DEVICES[device_id]['command'] = full_cmd

        t = threading.Thread(
            target=ssh_worker,
            args=(
                device_id,
                DEVICES[device_id]['ip'],
                DEVICES[device_id]['port'],
                DEVICES[device_id]['user'],
                DEVICES[device_id]['password'],
                full_cmd,
                DEVICES[device_id]['keywords']
            ),
            daemon=True
        )
        t.start()

    return jsonify({'success': True})


@app.route('/api/devices/delete/<device_id>', methods=['DELETE'])
def delete_device(device_id):
    if device_id in DEVICES:
        DEVICES[device_id]['running'] = False
        time.sleep(0.5)
        del DEVICES[device_id]
        save_devices_to_disk()
    return jsonify({'success': True})


if __name__ == '__main__':
    load_devices_from_disk()
    app.run(host='0.0.0.0', port=9001, debug=False)