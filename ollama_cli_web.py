'''Instalar modulos con:
pip3 install flask ollama --ignore-installed typing-extensions --break-system-packages
pip3 install flask ollama --break-system-packages'''

import ollama
import sys
import re
import time
import json
import sqlite3
import threading
import subprocess
import queue
import os
import base64
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string, Response, stream_with_context

# ==========================================
# -- BEGIN SETTINGS -- (NO BORRAR ESTA LINEA)
MODELO = 'fredrezones55/Qwen3.5-Uncensored-HauhauCS-Aggressive:4b'
KEEP_ALIVE = '30m'
THINK = False
OLLAMA_OPTIONS = {
    'num_ctx': 16000,
    'temperature': 0.1,
    'top_k': 40,
    'top_p': 0.9,
    'presence_penalty': 0.0,
    'repeat_penalty': 1.2,
    'num_thread': 8,
    'num_predict': 2048,
    'num_batch': 512,
    'use_mmap': True,
    'repeat_last_n': 128,
    'num_gpu': 99
}
# -- END SETTINGS -- (NO BORRAR ESTA LINEA)
# ==========================================

app = Flask(__name__)
DB_NAME = "agente_chats.db"

# Crear carpeta de subidas para los archivos adjuntos
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Control de tareas asíncronas en segundo plano
EJECUCIONES_ACTIVAS = {}
COLAS_STREAM = {}


# ==========================================
# BASE DE DATOS SQLITE
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        '''CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY AUTOINCREMENT, titulo TEXT, creado DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    c.execute(
        '''CREATE TABLE IF NOT EXISTS mensajes (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, rol TEXT, contenido TEXT)''')
    conn.commit()
    conn.close()


def db_query(query, args=(), fetch=True):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(query, args)
    if fetch:
        res = c.fetchall()
    else:
        conn.commit()
        res = c.lastrowid
    conn.close()
    return res


init_db()


# ==========================================
# FUNCIONES DEL AGENTE Y SISTEMA
# ==========================================
def obtener_hora_actual() -> str:
    return datetime.now().strftime("%H:%M:%S")


def ejecutar_comando_sistema(comando: str):
    tiempo_inicio = time.time()
    timeout = 900
    try:
        proceso = subprocess.Popen(
            comando, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True, bufsize=1
        )
        while True:
            linea = proceso.stdout.readline()
            if not linea and proceso.poll() is not None:
                break
            if linea:
                yield linea
            if time.time() - tiempo_inicio > timeout:
                proceso.kill()
                yield "\n[Error: El comando excedió el tiempo límite (900s).]\n"
                break
        proceso.stdout.close()
        proceso.wait()
    except Exception as e:
        yield f"Error al ejecutar comando: {e}\n"


def limpiar_texto(texto):
    if not texto: return ""
    return re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', texto)


def auto_titular_chat(mensaje):
    try:
        res = ollama.generate(
            model=MODELO,
            prompt=f"Resume esto en 3 a 5 palabras máximo para un título de chat: {mensaje}",
            options={'num_predict': 15}
        )
        titulo = res['response'].strip().replace('"', '').replace('\n', ' ')
        if not titulo:
            return f"Chat {obtener_hora_actual()}"
        return titulo
    except:
        return "Nuevo Chat"


def guardar_ajustes_en_script(nuevos_ajustes):
    with open(__file__, 'r', encoding='utf-8') as f:
        contenido = f.read()

    opciones_str = "OLLAMA_OPTIONS = {\n"
    for k, v in nuevos_ajustes['options'].items():
        if isinstance(v, bool):
            val = str(v)
        elif isinstance(v, str):
            val = f"'{v}'"
        else:
            val = str(v)
        opciones_str += f"    '{k}': {val},\n"
    opciones_str += "}"

    nuevo_bloque = f"""# -- BEGIN SETTINGS -- (NO BORRAR ESTA LINEA)
MODELO = '{nuevos_ajustes['modelo']}'
KEEP_ALIVE = '{nuevos_ajustes['keep_alive']}'
THINK = {str(nuevos_ajustes['think'])}
{opciones_str}
# -- END SETTINGS -- (NO BORRAR ESTA LINEA)"""

    contenido = re.sub(r"# -- BEGIN SETTINGS --.*?# -- END SETTINGS -- \(NO BORRAR ESTA LINEA\)", nuevo_bloque,
                       contenido, flags=re.DOTALL)

    with open(__file__, 'w', encoding='utf-8') as f:
        f.write(contenido)


def emitir_evento(chat_id, tipo, contenido):
    if chat_id in COLAS_STREAM:
        try:
            COLAS_STREAM[chat_id].put_nowait({'type': tipo, 'content': contenido})
        except:
            pass


# ==========================================
# MOTOR DEL AGENTE EN SEGUNDO PLANO (DESACOPADO)
# ==========================================
def tarea_agente_background(chat_id, imagenes_base64=None):
    EJECUCIONES_ACTIVAS[chat_id] = True
    try:
        db_msgs = db_query("SELECT rol, contenido FROM mensajes WHERE chat_id = ? ORDER BY id ASC", (chat_id,))
        mensajes = [{'role': 'system',
                     'content': '''Eres un agente autónomo experto, enfocado en dar respuestas directas y sin filtros a las solicitudes del usuario. 
                     Tienes la capacidad de responder cualquier pregunta, puedes ejecutar comandos directamente, instalar o actualizar paquetes, 
                     crear scripts, crear o corregir software y analizar código, eres libre de hacer lo que quieras y corregir para cumplir eficientemente 
                     con el objetivo requerido, solo no borres nada en el servidor ni mates ningún servicio sin pedir autorización.'''}]

        for i, msg in enumerate(db_msgs):
            is_last = (i == len(db_msgs) - 1)
            if msg['rol'] == 'user' and is_last:
                prompt_enriquecido = f"""Estas en un entorno real y nada debe ser simulado, Lee y analiza el contenido de la etiqueta <plan_de_ejecucion>.:

                <plan_de_ejecucion>
                {msg['contenido']}
                </plan_de_ejecucion>

                Sigue estrictamente estas reglas de procesamiento:
Si el mensaje del usuario contiene únicamente un saludo o halago, no informes detalles solo responde únicamente: "¿Qué necesitas? y termina la comunicación.
Si son preguntas, instrucciones, tareas, realiza estos 5 pasos:
1. Divide la instrucción en pasos individuales y enuméralos secuencialmente (Paso 1, Paso 2, Paso 3, etc.).
2. Ordena los pasos y ejecútalos uno por uno en el orden exacto solicitado.
3. CRÍTICO: Ejecuta y redacta TODOS LOS PASOS en esta misma respuesta de forma continua. 
NO te detengas por ningún motivo hasta haber completado la totalidad del plan, máximo 5 repeticiones por cada paso.
4. Al finalizar cada paso informa el detalle, revalida el contenido de la etiqueta <plan_de_ejecucion> para confirmar que la secuencia se está cumpliendo correctamente."""

                msg_dict = {'role': 'user', 'content': prompt_enriquecido}
                if imagenes_base64:
                    msg_dict['images'] = imagenes_base64
                mensajes.append(msg_dict)
            else:
                mensajes.append({'role': msg['rol'], 'content': msg['contenido']})

        while True:
            respuesta_final = ""
            asistente_msg_id = db_query("INSERT INTO mensajes (chat_id, rol, contenido) VALUES (?, ?, ?)",
                                        (chat_id, 'assistant', ''), fetch=False)

            stream = ollama.chat(
                model=MODELO, messages=mensajes, tools=[obtener_hora_actual, ejecutar_comando_sistema],
                stream=True, keep_alive=KEEP_ALIVE, think=THINK, options=OLLAMA_OPTIONS
            )

            herramientas_solicitadas = []

            for chunk in stream:
                if hasattr(chunk.message, 'tool_calls') and chunk.message.tool_calls:
                    herramientas_solicitadas = chunk.message.tool_calls

                pensamiento = getattr(chunk.message, 'thinking', None)
                if pensamiento:
                    p_limpio = limpiar_texto(pensamiento)
                    if p_limpio:
                        emitir_evento(chat_id, 'think', p_limpio)

                contenido = chunk.message.content
                if contenido:
                    c_limpio = limpiar_texto(contenido)
                    if c_limpio:
                        respuesta_final += c_limpio
                        db_query("UPDATE mensajes SET contenido = ? WHERE id = ?",
                                 (respuesta_final, asistente_msg_id), fetch=False)
                        emitir_evento(chat_id, 'text', c_limpio)

            if not respuesta_final.strip():
                db_query("DELETE FROM mensajes WHERE id = ?", (asistente_msg_id,), fetch=False)

            if herramientas_solicitadas:
                mensajes.append(
                    {'role': 'assistant', 'content': respuesta_final, 'tool_calls': herramientas_solicitadas})

                for tool in herramientas_solicitadas:
                    nombre_fn = tool.function.name
                    args = tool.function.arguments

                    emitir_evento(chat_id, 'tool', f'\n\n🔧 Ejecutando: {nombre_fn}({args})...\nSalida:\n')
                    res_completo = ""

                    if nombre_fn == 'obtener_hora_actual':
                        res_completo = obtener_hora_actual(**args)
                        emitir_evento(chat_id, 'tool_res', res_completo + '\n')

                    elif nombre_fn == 'ejecutar_comando_sistema':
                        for fragmento in ejecutar_comando_sistema(**args):
                            res_completo += fragmento
                            emitir_evento(chat_id, 'tool_res', fragmento)
                    else:
                        res_completo = "Herramienta no encontrada."
                        emitir_evento(chat_id, 'tool_res', res_completo + '\n')

                    mensajes.append({'role': 'tool', 'content': str(res_completo)})

                    if not respuesta_final.strip():
                        asistente_msg_id = db_query("INSERT INTO mensajes (chat_id, rol, contenido) VALUES (?, ?, ?)",
                                                    (chat_id, 'assistant', ''), fetch=False)

                    bloque_terminal = f'\n<div class="tool-text">🔧 Ejecutando: {nombre_fn}({args})...\nSalida:\n{res_completo}</div>\n'
                    respuesta_final += bloque_terminal

                    db_query("UPDATE mensajes SET contenido = ? WHERE id = ?",
                             (respuesta_final, asistente_msg_id), fetch=False)

                continue
            else:
                mensajes.append({'role': 'assistant', 'content': respuesta_final})

                texto_ia = respuesta_final.lower()
                if "proced" in texto_ia or "siguiente paso" in texto_ia or "continu" in texto_ia or "vamos a" in texto_ia  or "paso" in texto_ia:
                    mensajes.append({
                        'role': 'user',
                        'content': 'Ejecuta el comando correspondiente al siguiente paso ahora mismo usando la herramienta de sistema.'
                    })
                    continue

                if respuesta_final.strip():
                    emitir_evento(chat_id, 'finish', '')

                break
    except Exception as e:
        print(f"Error en tarea background para chat {chat_id}: {e}")
        emitir_evento(chat_id, 'error', str(e))
    finally:
        EJECUCIONES_ACTIVAS[chat_id] = False
        emitir_evento(chat_id, 'done', '[DONE]')


# ==========================================
# RUTAS API FLASK
# ==========================================
@app.route('/api/upload', methods=['POST'])
def upload_files():
    archivos_subidos = []
    if 'archivos' in request.files:
        archivos = request.files.getlist('archivos')
        for archivo in archivos:
            if archivo.filename:
                nombre_seguro = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', archivo.filename)
                ruta = os.path.join(UPLOAD_FOLDER, nombre_seguro)
                archivo.save(ruta)
                archivos_subidos.append({'nombre': nombre_seguro, 'ruta': ruta})
    return jsonify({'archivos': archivos_subidos})


@app.route('/api/chats', methods=['GET', 'POST'])
def handle_chats():
    if request.method == 'GET':
        chats = db_query("SELECT * FROM chats ORDER BY creado DESC")
        return jsonify([dict(c) for c in chats])
    elif request.method == 'POST':
        msg = request.json.get('mensaje', 'Nuevo Chat')
        titulo = auto_titular_chat(msg)
        chat_id = db_query("INSERT INTO chats (titulo) VALUES (?)", (titulo,), fetch=False)
        return jsonify({"id": chat_id, "titulo": titulo})


@app.route('/api/chats/<int:chat_id>', methods=['DELETE'])
def delete_chat(chat_id):
    db_query("DELETE FROM chats WHERE id = ?", (chat_id,), fetch=False)
    db_query("DELETE FROM mensajes WHERE chat_id = ?", (chat_id,), fetch=False)
    return jsonify({"status": "ok"})

@app.route('/api/chats/<int:chat_id>/status', methods=['GET'])
def check_chat_status(chat_id):
    is_active = EJECUCIONES_ACTIVAS.get(chat_id, False)
    return jsonify({"active": is_active})

@app.route('/api/chats/<int:chat_id>/historial', methods=['GET'])
def get_historial(chat_id):
    msgs = db_query("SELECT rol, contenido FROM mensajes WHERE chat_id = ? ORDER BY id ASC", (chat_id,))
    return jsonify([dict(m) for m in msgs])


@app.route('/api/settings', methods=['GET', 'POST'])
def settings():
    global MODELO, KEEP_ALIVE, THINK, OLLAMA_OPTIONS
    if request.method == 'GET':
        try:
            modelos_locales = [m['name'] for m in ollama.list().get('models', [])]
            # Verificamos si el modelo está en la lista de los que tenemos descargados
            modelo_en_lista = MODELO in modelos_locales
            # Además, preguntamos a Ollama cuáles modelos están corriendo en RAM ahora mismo
            modelos_corriendo = [m['name'] for m in ollama.ps().get('models', [])]
            modelo_cargado = MODELO in modelos_corriendo
        except:
            modelos_locales = []
            modelo_cargado = False

        return jsonify({
            "modelo": MODELO, "keep_alive": KEEP_ALIVE, "think": THINK,
            "options": OLLAMA_OPTIONS, "modelos_locales": modelos_locales, "modelo_cargado": modelo_cargado
        })
    elif request.method == 'POST':
        datos = request.json
        modelo_anterior = MODELO

        MODELO = datos['modelo']
        KEEP_ALIVE = datos['keep_alive']
        THINK = datos.get('think', False)

        for k in OLLAMA_OPTIONS.keys():
            if k in datos['options']:
                tipo = type(OLLAMA_OPTIONS[k])
                OLLAMA_OPTIONS[k] = tipo(datos['options'][k])

        datos['options'] = OLLAMA_OPTIONS.copy()
        guardar_ajustes_en_script(datos)

        # Si cambiamos de modelo, forzamos la descarga del viejo y la carga del nuevo en segundo plano
        if modelo_anterior != MODELO:
            def recargar_modelo():
                try:
                    print(f"Descargando modelo anterior {modelo_anterior} de RAM...")
                    # Mandar keep_alive 0 al modelo viejo lo descarga inmediatamente de la RAM
                    ollama.generate(model=modelo_anterior, prompt="", keep_alive=0)
                except:
                    pass
                try:
                    print(f"Cargando nuevo modelo {MODELO} en RAM...")
                    ollama.generate(model=MODELO, prompt="", keep_alive=KEEP_ALIVE, options=OLLAMA_OPTIONS)
                    print("¡Nuevo modelo listo!")
                except Exception as e:
                    print(f"Error al cargar nuevo modelo: {e}")

            threading.Thread(target=recargar_modelo, daemon=True).start()

        return jsonify({"status": "ok", "mensaje": "Ajustes guardados y modelo actualizando."})


@app.route('/api/pull_model', methods=['POST'])
def pull_model():
    modelo = request.json.get('modelo')

    def pull_thread():
        try:
            ollama.pull(modelo)
        except Exception as e:
            print(f"Error pulling: {e}")

    threading.Thread(target=pull_thread).start()
    return jsonify({"status": "iniciado"})

# --- NUEVA RUTA PARA REINICIAR OLLAMA DESDE EL BOTÓN STOP ---
@app.route('/api/restart_ollama', methods=['POST'])
def restart_ollama():
    try:
        # Intenta primero sin sudo
        res = subprocess.run("systemctl restart ollama.service", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            # Si falla, intenta con sudo (requiere que el usuario tenga permisos NOPASSWD en visudo para este comando)
            res = subprocess.run("sudo systemctl restart ollama.service", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return jsonify({"status": "ok", "mensaje": "Servicio Ollama reiniciado."})
    except Exception as e:
        return jsonify({"status": "error", "mensaje": str(e)}), 500

@app.route('/api/chat/stream', methods=['POST'])
def chat_stream():
    datos = request.json
    chat_id = datos.get('chat_id')
    user_input = datos.get('mensaje')
    archivos_data = datos.get('archivos', [])

    texto_archivos = ""
    imagenes_base64 = []

    if archivos_data:
        texto_archivos += "\n\n--- ARCHIVOS ADJUNTOS POR EL USUARIO ---"
        for arch in archivos_data:
            ruta = arch['ruta']
            nombre = arch['nombre']
            ext = nombre.split('.')[-1].lower() if '.' in nombre else ''

            if ext in ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp']:
                try:
                    with open(ruta, "rb") as image_file:
                        imagenes_base64.append(base64.b64encode(image_file.read()).decode('utf-8'))
                    texto_archivos += f"\n[Imagen adjunta: {nombre}]"
                except Exception as e:
                    print("Error leyendo imagen:", e)

            elif ext in ['txt', 'csv', 'json', 'py', 'js', 'html', 'css', 'md', 'xml', 'log', 'sh', 'bat', 'yml',
                         'yaml', 'ini']:
                try:
                    with open(ruta, "r", encoding="utf-8", errors='replace') as f:
                        contenido_txt = f.read()
                    texto_archivos += f"\n\n[Contenido del archivo de texto/código: {nombre} | Ruta: {ruta}]\n{contenido_txt}\n[Fin del contenido de {nombre}]"
                except Exception as e:
                    texto_archivos += f"\n[Archivo adjunto: {nombre} - Error leyendo texto: {e}]"

            else:
                texto_archivos += f"\n[Archivo Complejo/Binario subido: {nombre} (Guardado en: {ruta}). Este formato no es texto plano. Si el usuario te pregunta sobre él, usa obligatoriamente tu herramienta 'ejecutar_comando_sistema' con utilidades de terminal (ej. cat, strings, pdftotext, tshark, etc.) apuntando a esa ruta exacta para extraer su contenido.]"

    user_input_db = user_input + texto_archivos

    # SOLO insertar en la base de datos si hay un mensaje real.
    # Si es vacío, asumimos que es una solicitud de reconexión.
    if user_input_db.strip():
        db_query("INSERT INTO mensajes (chat_id, rol, contenido) VALUES (?, ?, ?)", (chat_id, 'user', user_input_db),
                 fetch=False)

    q = queue.Queue()
    COLAS_STREAM[chat_id] = q

    if not EJECUCIONES_ACTIVAS.get(chat_id, False) and user_input_db.strip():
        t = threading.Thread(target=tarea_agente_background, args=(chat_id, imagenes_base64), daemon=True)
        t.start()

    def generate():
        try:
            while True:
                try:
                    evento = q.get(timeout=30)
                    if evento['type'] == 'done':
                        yield "data: [DONE]\n\n"
                        break
                    yield f"data: {json.dumps(evento)}\n\n"
                except queue.Empty:
                    yield "data: {\"type\": \"ping\"}\n\n"
        finally:
            if chat_id in COLAS_STREAM:
                del COLAS_STREAM[chat_id]

    return Response(stream_with_context(generate()), mimetype='text/event-stream')


# ==========================================
# INTERFAZ WEB (FRONTEND GEMINI-LIKE) CON RESPONSIVO
# ==========================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Agente Autónomo IA</title>
    <style>
        :root { --bg-sidebar: #f0f4f9; --bg-main: #ffffff; --text-main: #1f1f1f; --text-muted: #444746; --accent: #0b57d0; --border: #e3e3e3; }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }
        body { display: flex; height: 100vh; background-color: var(--bg-main); color: var(--text-main); overflow: hidden; }

        .sidebar { width: 280px; flex-shrink: 0; background-color: var(--bg-sidebar); padding: 20px 10px; display: flex; flex-direction: column; overflow-y: auto; transition: margin-left 0.3s ease; }
        .sidebar.collapsed { margin-left: -280px; }

        .logo { display: flex; align-items: center; gap: 10px; font-size: 20px; font-weight: 500; margin-bottom: 30px; padding-left: 10px; }
        .btn-new { background-color: #e8eaed; border: none; border-radius: 20px; padding: 12px 15px; font-size: 14px; font-weight: 500; display: flex; gap: 10px; align-items: center; cursor: pointer; transition: 0.2s; margin-bottom: 20px; }
        .btn-new:hover { background-color: #dbe1e8; }
        .chat-list { flex: 1; display: flex; flex-direction: column; gap: 5px; }
        .chat-item { padding: 10px 15px; border-radius: 20px; cursor: pointer; display: flex; justify-content: space-between; align-items: center; font-size: 14px; color: var(--text-muted); transition: 0.2s; }
        .chat-item:hover, .chat-item.active { background-color: #e8eaed; color: var(--text-main); }
        .delete-btn { opacity: 0; background: none; border: none; color: #d93025; cursor: pointer; }
        .chat-item:hover .delete-btn { opacity: 1; }

        .main-content { flex: 1; display: flex; flex-direction: column; position: relative; width: 100%; }

        .header { display: flex; justify-content: space-between; padding: 15px 20px; align-items: center; }
        .btn-icon { background: none; border: none; font-size: 20px; cursor: pointer; color: var(--text-muted); padding: 10px; border-radius: 50%; transition: 0.2s; display: flex; align-items: center; justify-content: center; }
        .btn-icon:hover { background-color: var(--bg-sidebar); }

        .chat-area { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; align-items: center; }
        .chat-container { width: 100%; max-width: 800px; display: flex; flex-direction: column; gap: 20px; padding-bottom: 120px; }
        .message { max-width: 85%; padding: 12px 18px; border-radius: 20px; font-size: 15px; line-height: 1.5; white-space: pre-wrap; word-wrap: break-word; }
        .msg-user { align-self: flex-end; background-color: var(--bg-sidebar); color: var(--text-main); border-bottom-right-radius: 5px; }
        .msg-ia { align-self: flex-start; background-color: transparent; color: var(--text-main); }
        .think-text { color: #888; font-style: italic; font-size: 13.5px; border-left: 3px solid #ccc; padding-left: 10px; margin-bottom: 10px; }
        .tool-text { font-family: monospace; background-color: #1e1e1e; color: #00ff00; padding: 10px; border-radius: 10px; font-size: 13px; margin-top: 10px; overflow-x: auto; margin-bottom: 15px; }
        .welcome-title { text-align: center; font-size: 40px; font-weight: 500; margin: auto; background: -webkit-linear-gradient(45deg, #4285f4, #d96570); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }

        /* Input y Contenedor de Archivos */
        .input-wrapper { position: absolute; bottom: 30px; left: 50%; transform: translateX(-50%); width: 100%; max-width: 800px; padding: 0 20px; }

        .file-preview-container { display: none; flex-wrap: wrap; gap: 10px; margin-bottom: 10px; width: 100%; padding: 0 5px; }
        .file-pill { background: #e8eaed; padding: 6px 14px; border-radius: 15px; font-size: 13px; display: flex; align-items: center; gap: 10px; color: var(--text-muted); box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        .file-pill-remove { cursor: pointer; color: #d93025; font-weight: bold; font-size: 16px; transition: 0.2s; }
        .file-pill-remove:hover { transform: scale(1.2); }

        .input-box { display: flex; align-items: center; background-color: var(--bg-sidebar); border-radius: 30px; padding: 5px 15px; box-shadow: 0 2px 6px rgba(0,0,0,0.05); }
        .input-box textarea { flex: 1; border: none; background: transparent; padding: 12px; font-size: 15px; resize: none; outline: none; max-height: 200px; overflow-y: auto; }

        .file-btn { background: none; border: none; cursor: pointer; padding: 10px; color: var(--text-muted); font-size: 22px; display: flex; align-items: center; transition: 0.2s; }
        .file-btn:hover { color: var(--accent); }
        .send-btn { background: none; border: none; cursor: pointer; padding: 10px; color: var(--accent); font-size: 18px; display: flex; align-items: center; }
        .stop-btn { background: none; border: none; cursor: pointer; padding: 10px; color: #d93025; font-size: 18px; display: flex; align-items: center; transition: 0.2s; }
        .stop-btn:hover { transform: scale(1.1); }

        /* Modal Settings */
        .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); align-items: center; justify-content: center; z-index: 2000; }
        .modal-content { background: white; padding: 30px; border-radius: 15px; width: 500px; max-height: 90vh; overflow-y: auto; }
        .form-group { margin-bottom: 15px; display: flex; flex-direction: column; gap: 5px; }
        .form-group label { font-size: 13px; font-weight: 600; color: var(--text-muted); }
        .form-group input, .form-group select { padding: 10px; border: 1px solid var(--border); border-radius: 8px; font-size: 14px; }
        .status-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; background-color: #ccc; margin-left: 10px; }
        .status-dot.active { background-color: #34a853; }
        .btn-primary { background-color: var(--accent); color: white; border: none; padding: 10px 20px; border-radius: 20px; cursor: pointer; font-weight: 500; }
        .btn-primary:hover { opacity: 0.9; }

        @media (max-width: 768px) {
            .sidebar { position: absolute; height: 100%; z-index: 1000; box-shadow: 4px 0 15px rgba(0,0,0,0.15); }
            .message { max-width: 95%; font-size: 14.5px; padding: 10px 15px; }
            .input-wrapper { bottom: 10px; padding: 0 10px; }
            .welcome-title { font-size: 30px; text-align: center; }
            .modal-content { width: 95%; padding: 20px; }
        }
    </style>
</head>
<body>

    <div class="sidebar" id="appSidebar">
        <div class="logo">✨ Agente IA</div>
        <button class="btn-new" onclick="newChat()">➕ Nuevo chat</button>
        <div class="chat-list" id="chatList"></div>
    </div>

    <div class="main-content">
        <div class="header">
            <button class="btn-icon" onclick="toggleSidebar()" title="Ocultar/Mostrar Menú">☰</button>
            <button class="btn-icon" onclick="openSettings()" title="Ajustes">⚙️</button>
        </div>

        <div class="chat-area" id="chatArea">
            <h1 class="welcome-title" id="welcomeTitle">¿Por dónde empezamos?</h1>
            <div class="chat-container" id="chatContainer"></div>
        </div>

        <div class="input-wrapper">
            <div id="filePreview" class="file-preview-container"></div>
            <div class="input-box">
                <button class="file-btn" onclick="document.getElementById('fileInput').click()" title="Adjuntar archivos">📎</button>
                <input type="file" id="fileInput" multiple style="display:none;" onchange="handleFileSelect(event)">
                <textarea id="userInput" rows="1" placeholder="Ingresa comandos o plan de ejecución..." oninput="autoResize(this)" onkeydown="checkEnter(event)"></textarea>
                <button class="send-btn" id="sendBtn" onclick="sendMessage()">➤</button>
                <button class="stop-btn" id="stopBtn" onclick="stopGeneration()" style="display:none;" title="Detener generación y reiniciar Ollama">⏹️</button>
            </div>
        </div>
    </div>

    <!-- MODAL AJUSTES -->
    <div class="modal" id="settingsModal">
        <div class="modal-content">
            <h2 style="margin-bottom: 20px;">Ajustes de Modelo <span class="status-dot" id="modelStatus" title="Modelo cargado"></span></h2>
            <div class="form-group">
                <label>Modelo Actual (Escribe o selecciona)</label>
                <input type="text" id="setModelo" list="modelList">
                <datalist id="modelList"></datalist>
                <small><a href="https://ollama.com/search" target="_blank">🔍 Buscar modelos en Ollama</a></small>
            </div>
            <button onclick="pullModel()" style="padding:5px; margin-bottom:15px; cursor:pointer;">⬇️ Instalar Modelo Escrito</button>
            <hr style="margin-bottom: 15px; border-color: var(--border);">

            <div style="display:flex; gap:10px;">
                <div class="form-group" style="flex:1;"><label>Keep Alive</label><input type="text" id="setKeepAlive"></div>
                <div class="form-group" style="flex:1;"><label>Num GPU</label><input type="number" id="setNumGpu"></div>
            </div>
            <div style="display:flex; gap:10px;">
                <div class="form-group" style="flex:1;"><label>Num Ctx</label><input type="number" id="setNumCtx"></div>
                <div class="form-group" style="flex:1;"><label>Temperature</label><input type="number" step="0.1" id="setTemp"></div>
            </div>
            <div style="display:flex; gap:10px;">
                <div class="form-group" style="flex:1;"><label>Top K</label><input type="number" id="setTopK"></div>
                <div class="form-group" style="flex:1;"><label>Top P</label><input type="number" step="0.1" id="setTopP"></div>
            </div>
            <div style="display:flex; gap:10px;">
                <div class="form-group" style="flex:1;"><label>Num Thread</label><input type="number" id="setThread"></div>
                <div class="form-group" style="flex:1;"><label>Num Predict</label><input type="number" id="setPredict"></div>
            </div>

            <div style="display:flex; justify-content: flex-end; gap: 10px; margin-top: 20px;">
                <button onclick="closeSettings()" style="padding: 10px 20px; border:none; border-radius:20px; cursor:pointer;">Cancelar</button>
                <button onclick="saveSettings()" class="btn-primary">Aplicar Cambios</button>
            </div>
        </div>
    </div>

    <script>
        let currentChatId = null;
        let selectedFiles = [];
        let currentController = null;
        
        async function stopGeneration() {
            if (currentController) {
                currentController.abort(); // Detiene el stream del lado del cliente
            }
            if(!confirm("¿Estás seguro de detener y reiniciar el servicio de Ollama? Esto matará cualquier proceso activo.")) return;
            try {
                alert("Enviando orden de reinicio...");
                const res = await fetch('/api/restart_ollama', { method: 'POST' });
                const data = await res.json();
                if(res.ok) alert("¡Éxito! " + data.mensaje);
                else alert("Error al reiniciar: " + (data.mensaje || "Revisa permisos sudo."));
            } catch(e) {
                console.error(e);
                alert("No se pudo conectar con el servidor para reiniciar.");
            }
        }

        function toggleSidebar() {
            document.getElementById('appSidebar').classList.toggle('collapsed');
        }

        function autoResize(el) {
            el.style.height = 'auto';
            el.style.height = (el.scrollHeight) + 'px';
        }

        function checkEnter(e) {
            if(e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        }

        // --- SISTEMA DE ARCHIVOS FRONTEND ---
        function handleFileSelect(event) {
            const files = event.target.files;
            for(let i=0; i<files.length; i++) {
                selectedFiles.push(files[i]);
            }
            renderFilePreview();
            document.getElementById('fileInput').value = '';
        }

        function renderFilePreview() {
            const preview = document.getElementById('filePreview');
            if(selectedFiles.length === 0) {
                preview.style.display = 'none';
                return;
            }
            preview.style.display = 'flex';
            preview.innerHTML = '';
            selectedFiles.forEach((f, index) => {
                const pill = document.createElement('div');
                pill.className = 'file-pill';
                pill.innerHTML = `<span>📎 ${f.name}</span> <span class="file-pill-remove" onclick="removeFile(${index})">×</span>`;
                preview.appendChild(pill);
            });
        }

        function removeFile(index) {
            selectedFiles.splice(index, 1);
            renderFilePreview();
        }
        // ------------------------------------

        async function loadChats() {
            const res = await fetch('/api/chats');
            const chats = await res.json();
            const list = document.getElementById('chatList');
            list.innerHTML = '';
            chats.forEach(c => {
                const div = document.createElement('div');
                div.className = `chat-item ${c.id === currentChatId ? 'active' : ''}`;
                div.innerHTML = `<span onclick="selectChat(${c.id})">${c.titulo}</span> <button class="delete-btn" onclick="deleteChat(${c.id})">🗑️</button>`;
                list.appendChild(div);
            });
        }

        function newChat() {
            currentChatId = null;
            document.getElementById('chatContainer').innerHTML = '';
            document.getElementById('welcomeTitle').style.display = 'block';
            loadChats();
            if(window.innerWidth <= 768) {
                document.getElementById('appSidebar').classList.add('collapsed');
            }
        }

        async function selectChat(id) {
            // 1. Abortar cualquier conexión activa del chat anterior
            if (currentController) {
                currentController.abort();
                currentController = null;
            }

            currentChatId = id;
            document.getElementById('welcomeTitle').style.display = 'none';
            
            // 2. Cargar el historial estático
            const res = await fetch(`/api/chats/${id}/historial`);
            const msgs = await res.json();
            const container = document.getElementById('chatContainer');
            container.innerHTML = '';
            msgs.forEach(m => appendMessageUI(m.rol, m.contenido));
            loadChats();

            if(window.innerWidth <= 768) {
                document.getElementById('appSidebar').classList.add('collapsed');
            }

            // 3. Verificar si el agente sigue respondiendo en este chat
            const statusRes = await fetch(`/api/chats/${id}/status`);
            const statusData = await statusRes.json();

            if (statusData.active) {
                // Si está activo, reconectarse al stream
                console.log("Reconectando a stream activo del chat:", id);
                document.getElementById('sendBtn').style.display = 'none';
                document.getElementById('stopBtn').style.display = 'flex';
                reconectarStream(id);
            } else {
                document.getElementById('sendBtn').style.display = 'flex';
                document.getElementById('stopBtn').style.display = 'none';
            }
        }
        
        async function reconectarStream(chatId) {
            currentController = new AbortController();
            
            // Creamos un nuevo div para la respuesta que sigue llegando, 
            // o usamos el último si ya era del asistente.
            const container = document.getElementById('chatContainer');
            let aiDiv = container.lastElementChild;
            if (!aiDiv || !aiDiv.classList.contains('msg-ia')) {
                aiDiv = document.createElement('div');
                aiDiv.className = 'message msg-ia';
                container.appendChild(aiDiv);
            }

            try {
                // Para reconectarnos, enviamos un payload vacío pero con el chat_id
                const response = await fetch('/api/chat/stream', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({chat_id: chatId, mensaje: "", archivos: []}),
                    signal: currentController.signal
                });

                const reader = response.body.getReader();
                const decoder = new TextDecoder("utf-8");
                let aiText = "", aiThink = "", aiTool = "";
                
                // Extraemos el texto que ya estaba en el div para no sobreescribirlo desde cero
                // y seguir añadiendo a partir de ahí
                const currentHtml = aiDiv.innerHTML;

                let buffer = "";
                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, {stream: true});                    
                    buffer = buffer.split(String.fromCharCode(13)).join('');
                    const separador_seguro = String.fromCharCode(10) + String.fromCharCode(10);
                    const lines = buffer.split(separador_seguro); 
                    buffer = lines.pop(); 

                    for(let line of lines) {
                        line = line.trim();
                        if(line.startsWith('data:')) {
                            const dataStr = line.replace('data:', '').trim();                   

                            if(dataStr === '[DONE]') break;
                            if(!dataStr) continue;

                            try {
                                const parsed = JSON.parse(dataStr);
                                let html_fragmento = '';

                                if(parsed.type === 'think') {
                                    html_fragmento = `<span class="think-text" style="display:inline;">${parsed.content}</span>`;
                                } else if(parsed.type === 'text') {
                                    html_fragmento = parsed.content;
                                } else if(parsed.type === 'tool' || parsed.type === 'tool_res') {
                                    // Limpiamos un poco el formato de las herramientas en vivo
                                    let textoLimpiado = parsed.content;
                                    textoLimpiado = textoLimpiado.replace(/\\n\\[Ejecutando/g, '<div class="tool-text">\\n[Ejecutando').replace(/Salida:\\n.*\\n\\]\\n/g, '</div>');
                                    html_fragmento = textoLimpiado;
                                } else if(parsed.type === 'finish') {
                                    loadChats(); 
                                    continue; 
                                }

                                // Sumamos el nuevo fragmento directamente al final del div
                                aiDiv.innerHTML += html_fragmento;
                                document.getElementById('chatArea').scrollTop = document.getElementById('chatArea').scrollHeight;

                            } catch(e) {}
                        }
                    }
                }
            } catch(e) {
                if (e.name === 'AbortError') console.log("Stream desconectado al cambiar de chat.");
            } finally {
                currentController = null;
                document.getElementById('sendBtn').style.display = 'flex';
                document.getElementById('stopBtn').style.display = 'none';
                loadChats();
            }
        }

        async function deleteChat(id) {
            await fetch(`/api/chats/${id}`, {method: 'DELETE'});
            if(currentChatId === id) newChat();
            else loadChats();
        }

        async function sendMessage() {
            const input = document.getElementById('userInput');
            const text = input.value.trim();

            // Requerir al menos texto o archivos
            if(!text && selectedFiles.length === 0) return;

            input.value = '';
            input.style.height = 'auto';
            document.getElementById('welcomeTitle').style.display = 'none';

            let textoVisibleUI = text;
            if(selectedFiles.length > 0) {
                textoVisibleUI += `<br><br><small style="color: #666;">[📎 Se adjuntaron ${selectedFiles.length} archivo(s)]</small>`;
            }
            appendMessageUI('user', textoVisibleUI);

            // Subir archivos primero al backend
            let archivosData = [];
            if(selectedFiles.length > 0) {
                const formData = new FormData();
                selectedFiles.forEach(f => formData.append('archivos', f));

                try {
                    const upRes = await fetch('/api/upload', {method: 'POST', body: formData});
                    const upJson = await upRes.json();
                    archivosData = upJson.archivos;
                    selectedFiles = [];
                    renderFilePreview();
                } catch(e) {
                    console.error("Error subiendo archivos:", e);
                }
            }

            if(!currentChatId) {
                try {
                    const res = await fetch('/api/chats', {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({mensaje: text || "Análisis de archivos"})
                    });
                    const data = await res.json();
                    currentChatId = data.id;
                    loadChats();
                } catch(e) {
                    console.error("Error al crear chat:", e);
                }
            }

            const container = document.getElementById('chatContainer');
            const aiDiv = document.createElement('div');
            aiDiv.className = 'message msg-ia';
            container.appendChild(aiDiv);
            
            // --- AGREGAR ESTO AQUÍ ---
            document.getElementById('sendBtn').style.display = 'none';
            document.getElementById('stopBtn').style.display = 'flex';
            currentController = new AbortController();
            // -------------------------

            try {
                // Enviar texto junto con los datos de los archivos adjuntos
                const response = await fetch('/api/chat/stream', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({chat_id: currentChatId, mensaje: text, archivos: archivosData}),
                    signal: currentController.signal
                });

                const reader = response.body.getReader();
                const decoder = new TextDecoder("utf-8");
                let aiText = "", aiThink = "", aiTool = "";
                let buffer = "";

                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, {stream: true});                    
                    buffer = buffer.split(String.fromCharCode(13)).join('');
                    const separador_seguro = String.fromCharCode(10) + String.fromCharCode(10);
                    const lines = buffer.split(separador_seguro); 
                    buffer = lines.pop(); 
                    for(let line of lines) {
                        line = line.trim();
                        if(line.startsWith('data:')) {
                            const dataStr = line.replace('data:', '').trim();                   

                            if(dataStr === '[DONE]') break;
                            if(!dataStr) continue;

                            try {
                                const parsed = JSON.parse(dataStr);

                                if(parsed.type === 'think') aiThink += parsed.content;
                                else if(parsed.type === 'text') aiText += parsed.content;
                                else if(parsed.type === 'tool' || parsed.type === 'tool_res') aiTool += parsed.content;
                                else if(parsed.type === 'finish') {
                                    loadChats(); 
                                    continue; 
                                }

                                let html = '';
                                if(aiThink) html += `<div class="think-text">${aiThink}</div>`;                     
                                if(aiTool) html += `<div class="tool-text">${aiTool}</div>`;
                                if(aiText) html += `<div>${aiText}</div>`;
                                aiDiv.innerHTML = html;

                                document.getElementById('chatArea').scrollTop = document.getElementById('chatArea').scrollHeight;

                            } catch(e) {}
                        }
                    }
                }
            } catch(e) {
                if (e.name === 'AbortError') {
                    console.log("Generación detenida por el usuario.");
                } else {
                    console.error("Conexión finalizada o error:", e);
                }
            } finally {
                // --- AGREGAR ESTO AQUÍ ---
                currentController = null;
                document.getElementById('sendBtn').style.display = 'flex';
                document.getElementById('stopBtn').style.display = 'none';
                loadChats();
                // -------------------------
            }
        }

        function appendMessageUI(rol, text) {
            const container = document.getElementById('chatContainer');
            const div = document.createElement('div');
            div.className = `message ${rol === 'user' ? 'msg-user' : 'msg-ia'}`;
            if(rol === 'assistant') {
                text = text.replace(/\\n\\[Ejecutando/g, '<div class="tool-text">\\n[Ejecutando').replace(/Salida:\\n.*\\n\\]\\n/g, '</div>');
            }
            div.innerHTML = text;
            container.appendChild(div);
            document.getElementById('chatArea').scrollTop = document.getElementById('chatArea').scrollHeight;
        }

        async function openSettings() {
            const res = await fetch('/api/settings');
            const data = await res.json();

            document.getElementById('setModelo').value = data.modelo;
            document.getElementById('setKeepAlive').value = data.keep_alive;
            document.getElementById('setNumGpu').value = data.options.num_gpu;
            document.getElementById('setNumCtx').value = data.options.num_ctx;
            document.getElementById('setTemp').value = data.options.temperature;
            document.getElementById('setTopK').value = data.options.top_k;
            document.getElementById('setTopP').value = data.options.top_p;
            document.getElementById('setThread').value = data.options.num_thread;
            document.getElementById('setPredict').value = data.options.num_predict;

            const dl = document.getElementById('modelList');
            dl.innerHTML = '';
            data.modelos_locales.forEach(m => {
                const opt = document.createElement('option');
                opt.value = m;
                dl.appendChild(opt);
            });

            const dot = document.getElementById('modelStatus');
            if(data.modelo_cargado) dot.classList.add('active');
            else dot.classList.remove('active');

            document.getElementById('settingsModal').style.display = 'flex';
        }

        function closeSettings() { document.getElementById('settingsModal').style.display = 'none'; }

        async function saveSettings() {
            const payload = {
                modelo: document.getElementById('setModelo').value,
                keep_alive: document.getElementById('setKeepAlive').value,
                think: false,
                options: {
                    num_gpu: parseInt(document.getElementById('setNumGpu').value),
                    num_ctx: parseInt(document.getElementById('setNumCtx').value),
                    temperature: parseFloat(document.getElementById('setTemp').value),
                    top_k: parseInt(document.getElementById('setTopK').value),
                    top_p: parseFloat(document.getElementById('setTopP').value),
                    num_thread: parseInt(document.getElementById('setThread').value),
                    num_predict: parseInt(document.getElementById('setPredict').value),
                }
            };

            await fetch('/api/settings', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            alert('Ajustes guardados permanentemente en el script.');
            closeSettings();
        }

        async function pullModel() {
            const mod = document.getElementById('setModelo').value;
            if(!mod) return alert("Escribe un nombre de modelo");
            alert("Descarga en segundo plano iniciada. Verifica la consola.");
            await fetch('/api/pull_model', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({modelo: mod})
            });
        }

        // Configuración inicial al cargar la página
        window.onload = () => {
            loadChats();
            if(window.innerWidth <= 768) {
                document.getElementById('appSidebar').classList.add('collapsed');
            }
        };
    </script>
</body>
</html>
"""


@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

def precargar_modelo():
    try:
        print(f"Precargando modelo {MODELO} en RAM/VRAM...")
        ollama.generate(model=MODELO, prompt="", keep_alive=KEEP_ALIVE, options=OLLAMA_OPTIONS)
        print("¡Modelo precargado con éxito!")
    except Exception as e:
        print(f"No se pudo precargar el modelo: {e}")


if __name__ == '__main__':
    # Hilo para precargar el modelo en segundo plano sin bloquear el arranque de Flask
    threading.Thread(target=precargar_modelo, daemon=True).start()

    print("\n=== SERVIDOR WEB INICIADO CON EJECUCIÓN PERSISTENTE ===")
    print("Abre tu navegador en: http://127.0.0.1:3001")
    app.run(host='0.0.0.0', port=3001, debug=True, use_reloader=False)