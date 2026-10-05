import os
import sys
import time
import sqlite3
import datetime
import threading
import queue
import json
import re
import socket
from urllib.parse import urlparse
from flask import Flask, request, jsonify, render_template_string, Response, redirect, url_for
import requests
from requests.auth import HTTPDigestAuth

app = Flask(__name__)

# Configuración de rutas
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "acs_database.db")
FW_DIR = "/var/www/html/fw"
os.makedirs(FW_DIR, exist_ok=True)

# Cola global para logs en tiempo real (SSE)
log_queue = queue.Queue()


# --- BASE DE DATOS SQLITE CON AUTO-MIGRACIÓN ---
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except Exception:
        pass
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS devices (
        serial_number TEXT PRIMARY KEY,
        model_name TEXT DEFAULT 'Sagemcom ONT',
        software_version TEXT DEFAULT 'N/A',
        connection_request_url TEXT,
        ip_address TEXT,
        last_inform DATETIME DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'Online'
    );

    CREATE TABLE IF NOT EXISTS pending_commands (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        serial_number TEXT NOT NULL,
        rpc_method TEXT NOT NULL,
        parameter_name TEXT,
        parameter_value TEXT,
        parameter_type TEXT,
        retry_count INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'PENDING'
    );

    CREATE TABLE IF NOT EXISTS execution_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        serial_number TEXT,
        rpc_method TEXT,
        status TEXT,
        log_detail TEXT
    );

    CREATE TABLE IF NOT EXISTS firmware_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT UNIQUE,
        upload_date DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS default_config (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        inform_interval INTEGER DEFAULT 3600,
        conn_req_user TEXT DEFAULT 'sagemcom',
        conn_req_pass TEXT DEFAULT 'sagemcom'
    );
    """)

    cursor.execute("PRAGMA table_info(devices)")
    existing_cols = [row[1] for row in cursor.fetchall()]
    new_cols = {
        "is_bootstrap_done": "INTEGER DEFAULT 0",
        "protocol_root": "TEXT DEFAULT 'Device.'",
        "manufacturer": "TEXT DEFAULT 'N/A'",
        "gpon_serial": "TEXT DEFAULT 'N/A'",
        "uptime": "TEXT DEFAULT 'N/A'",
        "hardware_version": "TEXT DEFAULT 'N/A'",
        "ram_total": "TEXT DEFAULT 'N/A'",
        "ram_free": "TEXT DEFAULT 'N/A'",
        "lan1": "TEXT DEFAULT 'N/A'",
        "lan2": "TEXT DEFAULT 'N/A'",
        "lan3": "TEXT DEFAULT 'N/A'",
        "lan4": "TEXT DEFAULT 'N/A'",
        "lan_status": "TEXT DEFAULT 'N/A'",
        "ssid_24g": "TEXT DEFAULT 'N/A'",
        "ssid_5g": "TEXT DEFAULT 'N/A'",
        "ssid_1": "TEXT DEFAULT 'N/A'",
        "ssid_2": "TEXT DEFAULT 'N/A'",
        "ssid_3": "TEXT DEFAULT 'N/A'",
        "ssid_4": "TEXT DEFAULT 'N/A'",
        "ssid_5": "TEXT DEFAULT 'N/A'",
        "ssid_6": "TEXT DEFAULT 'N/A'",
        "ssid_7": "TEXT DEFAULT 'N/A'",
        "ssid_8": "TEXT DEFAULT 'N/A'",
        "ssid_key_1": "TEXT DEFAULT 'N/A'",
        "ssid_key_2": "TEXT DEFAULT 'N/A'",
        "ssid_key_3": "TEXT DEFAULT 'N/A'",
        "ssid_key_4": "TEXT DEFAULT 'N/A'",
        "ssid_key_5": "TEXT DEFAULT 'N/A'",
        "ssid_key_6": "TEXT DEFAULT 'N/A'",
        "ssid_key_7": "TEXT DEFAULT 'N/A'",
        "ssid_key_8": "TEXT DEFAULT 'N/A'",
        "voice_enable": "TEXT DEFAULT 'N/A'",
        "voice_profile": "TEXT DEFAULT 'N/A'",
        "sip_proxy": "TEXT DEFAULT 'N/A'",
        "sip_number": "TEXT DEFAULT 'N/A'",
        "voip_status": "TEXT DEFAULT 'N/A'",
        "optical_status": "TEXT DEFAULT 'N/A'",
        "optical_rx": "TEXT DEFAULT 'N/A'",
        "optical_tx": "TEXT DEFAULT 'N/A'",
        "optical_temp": "TEXT DEFAULT 'N/A'",
        "optical_voltage": "TEXT DEFAULT 'N/A'",
        "vlan_info": "TEXT DEFAULT 'N/A'",
        "vlan_1": "TEXT DEFAULT 'N/A'",
        "vlan_2": "TEXT DEFAULT 'N/A'",
        "vlan_3": "TEXT DEFAULT 'N/A'",
        "vlan_4": "TEXT DEFAULT 'N/A'",
        "vlan_5": "TEXT DEFAULT 'N/A'",
        "ip_data": "TEXT DEFAULT 'N/A'",
        "ip_mgmt": "TEXT DEFAULT 'N/A'",
        "ip_iptv": "TEXT DEFAULT 'N/A'",
        "ip_voip": "TEXT DEFAULT 'N/A'",
        "dns_servers": "TEXT DEFAULT 'N/A'",
        "host_count": "TEXT DEFAULT 'N/A'",
        "host_name": "TEXT DEFAULT 'N/A'",
        "host_ip_mac": "TEXT DEFAULT 'N/A'",
        "host_signal": "TEXT DEFAULT 'N/A'",
        "host_1": "TEXT DEFAULT 'N/A'",
        "host_2": "TEXT DEFAULT 'N/A'",
        "host_3": "TEXT DEFAULT 'N/A'",
        "host_4": "TEXT DEFAULT 'N/A'",
        "host_5": "TEXT DEFAULT 'N/A'",
        "host_6": "TEXT DEFAULT 'N/A'",
        "host_7": "TEXT DEFAULT 'N/A'"
    }
    for col_name, col_type in new_cols.items():
        if col_name not in existing_cols:
            cursor.execute(f"ALTER TABLE devices ADD COLUMN {col_name} {col_type}")

    cursor.execute("""
        INSERT OR IGNORE INTO default_config (id, inform_interval, conn_req_user, conn_req_pass)
        VALUES (1, 3600, 'sagemcom', 'sagemcom')
    """)
    conn.commit()
    conn.close()


init_db()


def push_log(serial, rpc, status, detail):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = {
        "timestamp": timestamp,
        "serial": serial or "SYSTEM",
        "rpc": rpc,
        "status": status,
        "detail": detail
    }

    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO execution_logs (timestamp, serial_number, rpc_method, status, log_detail) VALUES (?, ?, ?, ?, ?)",
            (timestamp, serial, rpc, status, detail)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error guardando log en BD: {e}")

    log_queue.put(log_entry)
    print(f"[{timestamp}] [{serial}] [{rpc}] [{status}]")


# --- PLANTILLAS SOAP TR-069 ---
INFORM_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:cwmp="urn:dslforum-org:cwmp-1-0">
   <SOAP-ENV:Header>
      <cwmp:ID SOAP-ENV:mustUnderstand="1">0</cwmp:ID>
   </SOAP-ENV:Header>
   <SOAP-ENV:Body>
      <cwmp:InformResponse>
         <MaxEnvelopes>1</MaxEnvelopes>
      </cwmp:InformResponse>
   </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""

TRANSFER_COMPLETE_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:cwmp="urn:dslforum-org:cwmp-1-0">
   <SOAP-ENV:Header>
      <cwmp:ID SOAP-ENV:mustUnderstand="1">1</cwmp:ID>
   </SOAP-ENV:Header>
   <SOAP-ENV:Body>
      <cwmp:TransferCompleteResponse/>
   </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""


def build_set_per_soap(interval, user, password, root="Device."):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:cwmp="urn:dslforum-org:cwmp-1-0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:SOAP-ENC="http://schemas.xmlsoap.org/soap/encoding/">
   <SOAP-ENV:Header><cwmp:ID SOAP-ENV:mustUnderstand="1">51</cwmp:ID></SOAP-ENV:Header>
   <SOAP-ENV:Body>
      <cwmp:SetParameterValues>
         <ParameterList SOAP-ENC:arrayType="cwmp:ParameterValueStruct[3]">
            <ParameterValueStruct>
               <Name>{root}ManagementServer.PeriodicInformInterval</Name>
               <Value xsi:type="xsd:unsignedInt">{interval}</Value>
            </ParameterValueStruct>
            <ParameterValueStruct>
               <Name>{root}ManagementServer.ConnectionRequestUsername</Name>
               <Value xsi:type="xsd:string">{user}</Value>
            </ParameterValueStruct>
            <ParameterValueStruct>
               <Name>{root}ManagementServer.ConnectionRequestPassword</Name>
               <Value xsi:type="xsd:string">{password}</Value>
            </ParameterValueStruct>
         </ParameterList>
         <ParameterKey>500000132</ParameterKey>
      </cwmp:SetParameterValues>
   </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""


def format_parameter_name(p_name, root):
    if not p_name:
        return root
    parts = [p.strip() for p in p_name.split(",") if p.strip()]
    formatted_parts = []
    for p in parts:
        if p.startswith("Device.") or p.startswith("InternetGatewayDevice."):
            formatted_parts.append(p)
        else:
            if not p.startswith(root):
                p = root + p
            formatted_parts.append(p)
    return ",".join(formatted_parts) if "," in p_name or len(formatted_parts) > 1 else (
        formatted_parts[0] if formatted_parts else root)

#------------------------------------------------------------------------------------------------
def build_soap_request(rpc_method, param_name="", param_val="", param_type="string"):
    if rpc_method == "GetParameterValues":
        params = [p.strip() for p in re.split(r'[,\|\n]', param_name) if p.strip()]
        if not params:
            params = ["Device."]

        strings_xml = "".join([f"\n            <string>{p}</string>" for p in params])
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:cwmp="urn:dslforum-org:cwmp-1-0" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:SOAP-ENC="http://schemas.xmlsoap.org/soap/encoding/">
   <SOAP-ENV:Header><cwmp:ID SOAP-ENV:mustUnderstand="1">1</cwmp:ID></SOAP-ENV:Header>
   <SOAP-ENV:Body>
      <cwmp:GetParameterValues>
         <ParameterNames SOAP-ENC:arrayType="xsd:string[{len(params)}]">
            {strings_xml}
         </ParameterNames>
      </cwmp:GetParameterValues>
   </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""

    elif rpc_method == "SetParameterValues":
        names = [n.strip() for n in param_name.split("|") if n.strip()]
        vals = param_val.split("|")
        types = param_type.split("|")

        count = len(names)
        structs_xml = ""
        for i in range(count):
            n = names[i]
            v = vals[i].strip() if i < len(vals) else ""
            t = types[i].strip() if i < len(types) else "string"
            structs_xml += f"""
            <ParameterValueStruct>
               <Name>{n}</Name>
               <Value xsi:type="xsd:{t}">{v}</Value>
            </ParameterValueStruct>"""

        return f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:cwmp="urn:dslforum-org:cwmp-1-0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:SOAP-ENC="http://schemas.xmlsoap.org/soap/encoding/">
   <SOAP-ENV:Header><cwmp:ID SOAP-ENV:mustUnderstand="1">1</cwmp:ID></SOAP-ENV:Header>
   <SOAP-ENV:Body>
      <cwmp:SetParameterValues>
         <ParameterList SOAP-ENC:arrayType="cwmp:ParameterValueStruct[{count}]">{structs_xml}
         </ParameterList>
         <ParameterKey>500000132</ParameterKey>
      </cwmp:SetParameterValues>
   </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""

        # ... el resto de elifs de esta función (GetParameterAttributes, AddObject, etc.) se quedan exactamente igual ...
    elif rpc_method == "GetParameterAttributes":
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:SOAP-ENC="http://schemas.xmlsoap.org/soap/encoding/" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:cwmp="urn:dslforum-org:cwmp-1-0">
   <SOAP-ENV:Header><cwmp:ID SOAP-ENV:mustUnderstand="1">51</cwmp:ID></SOAP-ENV:Header>
   <SOAP-ENV:Body>
      <cwmp:GetParameterAttributes>
         <ParameterNames SOAP-ENC:arrayType="xsd:string[1]">
            <string>{param_name}</string>
         </ParameterNames>
      </cwmp:GetParameterAttributes>
   </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""

    elif rpc_method == "AddObject":
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:SOAP-ENC="http://schemas.xmlsoap.org/soap/encoding/" xmlns:cwmp="urn:dslforum-org:cwmp-1-0">
   <SOAP-ENV:Header><cwmp:ID SOAP-ENV:mustUnderstand="1">1</cwmp:ID></SOAP-ENV:Header>
   <SOAP-ENV:Body>
      <cwmp:AddObject>
         <ObjectName>{param_name}</ObjectName>
         <ParameterKey></ParameterKey>
      </cwmp:AddObject>
   </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""

    elif rpc_method in ["DeleteObject", "DelObject"]:
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:SOAP-ENC="http://schemas.xmlsoap.org/soap/encoding/" xmlns:cwmp="urn:dslforum-org:cwmp-1-0">
   <SOAP-ENV:Header><cwmp:ID SOAP-ENV:mustUnderstand="1">1</cwmp:ID></SOAP-ENV:Header>
   <SOAP-ENV:Body>
      <cwmp:DeleteObject>
         <ObjectName>{param_name}</ObjectName>
         <ParameterKey></ParameterKey>
      </cwmp:DeleteObject>
   </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""

    elif rpc_method == "FirmwareUpgrade":
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:cwmp="urn:dslforum-org:cwmp-1-0">
   <SOAP-ENV:Header><cwmp:ID SOAP-ENV:mustUnderstand="1">D-1-101</cwmp:ID></SOAP-ENV:Header>
   <SOAP-ENV:Body>
      <cwmp:Download>
         <CommandKey>FW_UPDATE</CommandKey>
         <FileType>1 Firmware Upgrade Image</FileType>
         <URL>http://192.168.35.9/fw/{param_name}</URL>
         <Username></Username>
         <Password></Password>
         <FileSize>0</FileSize>
         <TargetFileName></TargetFileName>
         <DelaySeconds>0</DelaySeconds>
         <SuccessURL></SuccessURL>
         <FailureURL></FailureURL>
      </cwmp:Download>
   </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""

    elif rpc_method == "Reboot":
        return """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:cwmp="urn:dslforum-org:cwmp-1-0">
   <SOAP-ENV:Header><cwmp:ID SOAP-ENV:mustUnderstand="1">1</cwmp:ID></SOAP-ENV:Header>
   <SOAP-ENV:Body>
      <cwmp:Reboot><CommandKey>REBOOT</CommandKey></cwmp:Reboot>
   </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""

    elif rpc_method == "FactoryDefault":
        return """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:cwmp="urn:dslforum-org:cwmp-1-0">
   <SOAP-ENV:Header><cwmp:ID SOAP-ENV:mustUnderstand="1">1</cwmp:ID></SOAP-ENV:Header>
   <SOAP-ENV:Body>
      <cwmp:FactoryReset/>
   </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""

    return ""


def extract_serial_from_xml_or_db(xml_str, client_ip=None):
    m = re.search(r'<SerialNumber[^>]*>(.*?)</SerialNumber>', xml_str, re.DOTALL)
    if m and m.group(1).strip():
        return m.group(1).strip()

    m = re.search(
        r'<Name>[^<]*(?:DeviceInfo|GatewayInfo)\.(?:SerialNumber|GponSerialNumber)</Name>\s*<Value[^>]*>(.*?)</Value>',
        xml_str, re.DOTALL)
    if m and m.group(1).strip():
        return m.group(1).strip()

    try:
        conn = get_db()
        cursor = conn.cursor()
        if client_ip:
            cursor.execute(
                "SELECT serial_number FROM devices WHERE ip_address=? OR ip_data=? OR ip_mgmt=? ORDER BY last_inform DESC LIMIT 1",
                (client_ip, client_ip, client_ip))
            row = cursor.fetchone()
            if row and row['serial_number']:
                conn.close()
                return row['serial_number']

        cursor.execute("SELECT serial_number FROM devices ORDER BY last_inform DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        if row and row['serial_number']:
            return row['serial_number']
    except Exception:
        pass

    return "CPE"


def parse_cwmp_inform(xml_str):
    serial, model, software, conn_req_url = "UNKNOWN", "Sagemcom ONT", "N/A", ""
    is_bootstrap = False
    try:
        m_sn = re.search(r'<SerialNumber[^>]*>(.*?)</SerialNumber>', xml_str, re.DOTALL)
        if m_sn:
            serial = m_sn.group(1).strip()

        m_pc = re.search(r'<ProductClass[^>]*>(.*?)</ProductClass>', xml_str, re.DOTALL)
        m_mn = re.search(r'<Name>[^<]*ModelName</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str, re.DOTALL)
        if m_mn and m_mn.group(1).strip():
            model = m_mn.group(1).strip()
        elif m_pc and m_pc.group(1).strip():
            model = m_pc.group(1).strip()

        m_sw = re.search(r'<Name>[^<]*SoftwareVersion</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str, re.DOTALL)
        if m_sw:
            software = m_sw.group(1).strip()

        m_curl = re.search(r'<Name>[^<]*ConnectionRequestURL</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str, re.DOTALL)
        if m_curl:
            conn_req_url = m_curl.group(1).strip()

        if "1 BOOTSTRAP" in xml_str or "0 BOOT" in xml_str:
            is_bootstrap = True

    except Exception as e:
        print(f"Error parseando Inform XML: {e}")

    return serial, model, software, conn_req_url, is_bootstrap


def extract_device_metrics(serial, xml_str):
    if not serial or serial == "CPE":
        serial = extract_serial_from_xml_or_db(xml_str, request.remote_addr if request else None)

    if not serial or serial == "CPE":
        return

    conn = get_db()
    cursor = conn.cursor()

    m_man = re.search(r'<Name>[^<]*Manufacturer</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str, re.DOTALL)
    if m_man and m_man.group(1).strip():
        cursor.execute("UPDATE devices SET manufacturer=? WHERE serial_number=?", (m_man.group(1).strip(), serial))

    m_gpons = re.search(r'<Name>[^<]*GponSerialNumber</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str, re.DOTALL)
    if m_gpons and m_gpons.group(1).strip():
        cursor.execute("UPDATE devices SET gpon_serial=? WHERE serial_number=?", (m_gpons.group(1).strip(), serial))

    m_curl = re.search(
        r'<Name>(?:Device|InternetGatewayDevice)\.ManagementServer\.ConnectionRequestURL</Name>\s*<Value[^>]*>(.*?)</Value>',
        xml_str, re.DOTALL)
    if m_curl and m_curl.group(1).strip():
        curl_val = m_curl.group(1).strip()
        try:
            parsed_url = urlparse(curl_val)
            ip_extracted = parsed_url.hostname
            if ip_extracted:
                cursor.execute(
                    "UPDATE devices SET ip_data=?, ip_mgmt=?, connection_request_url=? WHERE serial_number=?",
                    (ip_extracted, ip_extracted, curl_val, serial))
        except Exception:
            cursor.execute("UPDATE devices SET connection_request_url=? WHERE serial_number=?", (curl_val, serial))

    m_uptime = re.search(r'<Name>[^<]*UpTime</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str, re.DOTALL)
    if m_uptime:
        cursor.execute("UPDATE devices SET uptime=? WHERE serial_number=?", (m_uptime.group(1).strip(), serial))

    m_hw = re.search(r'<Name>[^<]*HardwareVersion</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str, re.DOTALL)
    if m_hw and m_hw.group(1).strip():
        cursor.execute("UPDATE devices SET hardware_version=? WHERE serial_number=?", (m_hw.group(1).strip(), serial))

    m_ram = re.search(r'<Name>[^<]*MemoryStatus\.Total</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str, re.DOTALL)
    if m_ram:
        try:
            ram_kb = int(m_ram.group(1).strip())
            cursor.execute("UPDATE devices SET ram_total=? WHERE serial_number=?", (f"{ram_kb // 1024} MB", serial))
        except ValueError:
            pass

    m_ram_free = re.search(r'<Name>[^<]*MemoryStatus\.Free</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str, re.DOTALL)
    if m_ram_free:
        try:
            ram_f_kb = int(m_ram_free.group(1).strip())
            cursor.execute("UPDATE devices SET ram_free=? WHERE serial_number=?", (f"{ram_f_kb // 1024} MB", serial))
        except ValueError:
            pass

    for i in range(1, 9):
        m_ssid = re.search(rf'<Name>[^<]*SSID\.{i}\.(?:SSID|SSIDName)</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str,
                           re.DOTALL)
        if m_ssid:
            val = m_ssid.group(1).strip()
            cursor.execute(f"UPDATE devices SET ssid_{i}=? WHERE serial_number=?", (val if val else "N/A", serial))
            if i == 1:
                cursor.execute("UPDATE devices SET ssid_24g=? WHERE serial_number=?", (val if val else "N/A", serial))
            elif i == 2:
                cursor.execute("UPDATE devices SET ssid_5g=? WHERE serial_number=?", (val if val else "N/A", serial))

        m_key = re.search(rf'<Name>[^<]*AccessPoint\.{i}\.Security\.KeyPassphrase</Name>\s*<Value[^>]*>(.*?)</Value>',
                          xml_str, re.DOTALL)
        if m_key:
            val_key = m_key.group(1).strip()
            cursor.execute(f"UPDATE devices SET ssid_key_{i}=? WHERE serial_number=?",
                           (val_key if val_key else "N/A", serial))

    vlan_summaries = []
    for i in range(1, 6):
        v_name = re.search(rf'<Name>[^<]*VLANTermination\.{i}\.Name</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str,
                           re.DOTALL)
        v_id = re.search(rf'<Name>[^<]*VLANTermination\.{i}\.VLANID</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str,
                         re.DOTALL)
        v_status = re.search(rf'<Name>[^<]*VLANTermination\.{i}\.Status</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str,
                             re.DOTALL)
        d_ip = re.search(rf'<Name>[^<]*DHCPv4\.Client\.{i}\.IPAddress</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str,
                         re.DOTALL)
        d_mask = re.search(rf'<Name>[^<]*DHCPv4\.Client\.{i}\.SubnetMask</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str,
                           re.DOTALL)
        d_gateway = re.search(rf'<Name>[^<]*DHCPv4\.Client\.{i}\.IPRouters</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str,
                              re.DOTALL)
        d_dns = re.search(rf'<Name>[^<]*DHCPv4\.Client\.{i}\.DNSServers</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str,
                          re.DOTALL)

        id_val = v_id.group(1).strip() if v_id else "N/A"
        name_val = v_name.group(1).strip() if v_name else f"VLAN{i}"
        st_val = v_status.group(1).strip() if v_status else "N/A"
        ip_val = d_ip.group(1).strip() if d_ip else "N/A"
        mask_val = d_mask.group(1).strip() if d_mask else "N/A"
        gw_val = d_gateway.group(1).strip() if d_gateway else "N/A"
        dns_val = d_dns.group(1).strip() if d_dns else "N/A"

        if id_val != "N/A" or ip_val != "N/A" or st_val != "N/A":
            v_str = f"VLAN {id_val} ({name_val}) [St:{st_val}] - IP: {ip_val}, Msk: {mask_val}, GW: {gw_val}, DNS: {dns_val}"
            cursor.execute(f"UPDATE devices SET vlan_{i}=? WHERE serial_number=?", (v_str, serial))
            vlan_summaries.append(v_str)
        else:
            cursor.execute(f"UPDATE devices SET vlan_{i}=? WHERE serial_number=?", ("N/A", serial))

    if vlan_summaries:
        cursor.execute("UPDATE devices SET vlan_info=? WHERE serial_number=?", (" | ".join(vlan_summaries), serial))

    ip_matches = re.findall(r'<Name>[^<]*IPAddress</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str, re.DOTALL)
    valid_ips = [ip.strip() for ip in ip_matches if ip.strip() and ip.strip() not in ("0.0.0.0", "127.0.0.1", "N/A")]
    if len(valid_ips) >= 1:
        cursor.execute("UPDATE devices SET ip_data=? WHERE serial_number=?", (valid_ips[0], serial))
    if len(valid_ips) >= 2:
        cursor.execute("UPDATE devices SET ip_mgmt=? WHERE serial_number=?", (valid_ips[1], serial))
    if len(valid_ips) >= 3:
        cursor.execute("UPDATE devices SET ip_iptv=? WHERE serial_number=?", (valid_ips[2], serial))
    if len(valid_ips) >= 4:
        cursor.execute("UPDATE devices SET ip_voip=? WHERE serial_number=?", (valid_ips[3], serial))

    m_dns = re.search(r'<Name>[^<]*DNSServers</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str, re.DOTALL)
    if m_dns and m_dns.group(1).strip():
        cursor.execute("UPDATE devices SET dns_servers=? WHERE serial_number=?", (m_dns.group(1).strip(), serial))

    lan_ports = re.findall(
        r'<Name>(?:Device|InternetGatewayDevice)\.Bridging\.Bridge\.1\.Port\.(\d+)\.Status</Name>\s*<Value[^>]*>(.*?)</Value>',
        xml_str, re.DOTALL)
    for p_idx, p_st in lan_ports:
        idx_int = int(p_idx)
        if idx_int in [2, 3, 4, 5]:
            cursor.execute(f"UPDATE devices SET lan{idx_int - 1}=? WHERE serial_number=?", (p_st.strip(), serial))
        elif idx_int in [1, 2, 3, 4]:
            cursor.execute(f"UPDATE devices SET lan{idx_int}=? WHERE serial_number=?", (p_st.strip(), serial))

    if lan_ports:
        cursor.execute("UPDATE devices SET lan_status=? WHERE serial_number=?",
                       (", ".join([f"LAN{p[0]}:{p[1].strip()}" for p in lan_ports]), serial))

    m_voice_en = re.search(r'<Name>[^<]*VoiceService\.1\.Enable</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str, re.DOTALL)
    if m_voice_en and m_voice_en.group(1).strip():
        cursor.execute("UPDATE devices SET voice_enable=? WHERE serial_number=?", (m_voice_en.group(1).strip(), serial))

    m_voice_pr = re.search(r'<Name>[^<]*VoiceProfile\.1\.Status</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str, re.DOTALL)
    if m_voice_pr and m_voice_pr.group(1).strip():
        cursor.execute("UPDATE devices SET voice_profile=? WHERE serial_number=?",
                       (m_voice_pr.group(1).strip(), serial))

    m_sip_prx = re.search(r'<Name>[^<]*SIP\.ProxyServer</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str, re.DOTALL)
    if m_sip_prx and m_sip_prx.group(1).strip():
        cursor.execute("UPDATE devices SET sip_proxy=? WHERE serial_number=?", (m_sip_prx.group(1).strip(), serial))

    m_sip_num = re.search(r'<Name>[^<]*DirectoryNumber</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str, re.DOTALL)
    if m_sip_num and m_sip_num.group(1).strip():
        cursor.execute("UPDATE devices SET sip_number=? WHERE serial_number=?", (m_sip_num.group(1).strip(), serial))

    voip_st = re.findall(
        r'<Name>(?:Device|InternetGatewayDevice)\.Services\.VoiceService\.1\.(?:PhyInterface\.(\d+)\.X_SAGEMCOM_Status|VoiceProfile\.1\.Line\.1\.Status)</Name>\s*<Value[^>]*>(.*?)</Value>',
        xml_str, re.DOTALL)
    if voip_st:
        cursor.execute("UPDATE devices SET voip_status=? WHERE serial_number=?",
                       (", ".join([f"FXS{v[0]}:{v[1].strip()}" for v in voip_st if v[1].strip()]), serial))

    opt_st = re.search(r'<Name>[^<]*Optical.*Status</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str, re.DOTALL)
    if opt_st and opt_st.group(1).strip():
        cursor.execute("UPDATE devices SET optical_status=? WHERE serial_number=?", (opt_st.group(1).strip(), serial))

    opt_rx = re.search(
        r'<Name>[^<]*Optical.*(?:RxPower|OpticalPowerRx|ReceiveOpticalLevel|OpticalSignalLevel)[^<]*</Name>\s*<Value[^>]*>(.*?)</Value>',
        xml_str, re.DOTALL)
    if opt_rx and opt_rx.group(1).strip():
        cursor.execute("UPDATE devices SET optical_rx=? WHERE serial_number=?", (opt_rx.group(1).strip(), serial))

    opt_tx = re.search(
        r'<Name>[^<]*Optical.*(?:TxPower|OpticalPowerTx|TransmitOpticalLevel)[^<]*</Name>\s*<Value[^>]*>(.*?)</Value>',
        xml_str, re.DOTALL)
    if opt_tx and opt_tx.group(1).strip():
        cursor.execute("UPDATE devices SET optical_tx=? WHERE serial_number=?", (opt_tx.group(1).strip(), serial))

    opt_temp = re.search(r'<Name>[^<]*Optical.*(?:Temperature|Temp)[^<]*</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str,
                         re.DOTALL)
    if opt_temp and opt_temp.group(1).strip():
        cursor.execute("UPDATE devices SET optical_temp=? WHERE serial_number=?", (opt_temp.group(1).strip(), serial))

    opt_volt = re.search(r'<Name>[^<]*Optical.*Voltage[^<]*</Name>\s*<Value[^>]*>(.*?)</Value>', xml_str, re.DOTALL)
    if opt_volt and opt_volt.group(1).strip():
        cursor.execute("UPDATE devices SET optical_voltage=? WHERE serial_number=?",
                       (opt_volt.group(1).strip(), serial))

    host_entries_match = re.search(r'<Name>[^<]*Hosts\.HostNumberOfEntries</Name>\s*<Value[^>]*>(\d+)</Value>', xml_str,
                                   re.DOTALL)
    if host_entries_match:
        cursor.execute("UPDATE devices SET host_count=? WHERE serial_number=?",
                       (host_entries_match.group(1).strip(), serial))

    host_blocks = re.findall(r'(<ParameterValueStruct>.*?<\/ParameterValueStruct>)', xml_str, re.DOTALL)
    if host_blocks:
        hosts_dict = {}
        for block in host_blocks:
            m_name = re.search(r'<Name>(.*?)</Name>', block)
            m_val = re.search(r'<Value[^>]*>(.*?)</Value>', block, re.DOTALL)
            if m_name and m_val:
                p_path = m_name.group(1).strip()
                p_val = m_val.group(1).strip()
                match_host = re.search(r'Hosts\.Host\.(\d+)\.(.*)', p_path)
                if match_host:
                    h_idx = match_host.group(1)
                    h_prop = match_host.group(2)
                    if h_idx not in hosts_dict:
                        hosts_dict[h_idx] = {}
                    hosts_dict[h_idx][h_prop] = p_val

        if hosts_dict:
            summary_names = []
            summary_ip_mac = []
            for i in range(1, 8):
                h = hosts_dict.get(str(i), {})
                h_name = h.get('HostName', '')
                h_ip = h.get('IPAddress', '')
                h_mac = h.get('PhysAddress', '')
                active_st = h.get('Active', 'false')

                if h_ip or h_mac or h_name:
                    h_str = f"{h_name or 'Unknown'} - IP: {h_ip or 'N/A'}, MAC: {h_mac or 'N/A'} [Act:{active_st}]"
                    cursor.execute(f"UPDATE devices SET host_{i}=? WHERE serial_number=?", (h_str, serial))
                else:
                    cursor.execute(f"UPDATE devices SET host_{i}=? WHERE serial_number=?", ("N/A", serial))

                if active_st.lower() in ('true', '1'):
                    if h_name:
                        summary_names.append(h_name)
                    if h_ip or h_mac:
                        summary_ip_mac.append(f"{h_ip} ({h_mac})")

            if summary_names:
                cursor.execute("UPDATE devices SET host_name=? WHERE serial_number=?",
                               (", ".join(summary_names), serial))
            if summary_ip_mac:
                cursor.execute("UPDATE devices SET host_ip_mac=? WHERE serial_number=?",
                               (" | ".join(summary_ip_mac), serial))

    conn.commit()
    conn.close()


def device_poller_thread():
    while True:
        time.sleep(30)
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT serial_number, connection_request_url, ip_address FROM devices")
            devices = cursor.fetchall()
            conn.close()

            for dev in devices:
                sn = dev['serial_number']
                conn_url = dev['connection_request_url']
                ip_addr = dev['ip_address']

                target_ip = None
                target_port = 7547

                if conn_url:
                    try:
                        parsed = urlparse(conn_url)
                        target_ip = parsed.hostname
                        target_port = parsed.port if parsed.port else 7547
                    except Exception:
                        target_ip = None

                if not target_ip and ip_addr:
                    target_ip = ip_addr

                is_alive = False
                if target_ip:
                    try:
                        s = socket.create_connection((target_ip, target_port), timeout=2)
                        s.close()
                        is_alive = True
                    except Exception:
                        is_alive = False

                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("UPDATE devices SET status=? WHERE serial_number=?",
                               ('Online' if is_alive else 'Offline', sn))
                conn.commit()
                conn.close()
        except Exception as e:
            print(f"Error en poller: {e}")


threading.Thread(target=device_poller_thread, daemon=True).start()

@app.route('//MyAcs/Default_stb.aspx', methods=['POST'])
def tr069_acs_endpoint():
    raw_xml = request.data.decode('utf-8', errors='ignore')
    client_ip = request.remote_addr

    if "cwmp:TransferComplete" in raw_xml or "<cwmp:TransferComplete>" in raw_xml:
        serial = extract_serial_from_xml_or_db(raw_xml, client_ip)
        cwmp_id = extract_cwmp_id(raw_xml)
        resp_xml = build_transfer_complete_response(cwmp_id)

        push_log(serial, "TransferComplete", "RECEIVED_XML", raw_xml)
        push_log(serial, "TransferCompleteResponse", "SENT_XML", resp_xml)
        return Response(resp_xml, content_type='text/xml')

    if "cwmp:Inform" in raw_xml or "<Inform>" in raw_xml:
        serial, model, software, conn_url, is_bootstrap = parse_cwmp_inform(raw_xml)
        detected_protocol = "InternetGatewayDevice." if "InternetGatewayDevice" in raw_xml else "Device."

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT is_bootstrap_done FROM devices WHERE serial_number=?", (serial,))
        row = cursor.fetchone()
        already_bootstrapped = row['is_bootstrap_done'] if row else 0

        cursor.execute("""
            INSERT INTO devices (serial_number, model_name, software_version, connection_request_url, ip_address, protocol_root, last_inform, status)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'), 'Online')
            ON CONFLICT(serial_number) DO UPDATE SET
                model_name=COALESCE(EXCLUDED.model_name, model_name),
                software_version=COALESCE(EXCLUDED.software_version, software_version),
                connection_request_url=CASE WHEN EXCLUDED.connection_request_url != '' THEN EXCLUDED.connection_request_url ELSE connection_request_url END,
                ip_address=EXCLUDED.ip_address,
                protocol_root=EXCLUDED.protocol_root,
                last_inform=datetime('now', 'localtime'),
                status='Online';
        """, (serial, model, software, conn_url, client_ip, detected_protocol))
        conn.commit()
        conn.close()

        push_log(serial, "Inform", "RECEIVED_XML", raw_xml)
        extract_device_metrics(serial, raw_xml)

        if is_bootstrap or already_bootstrapped == 0:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("UPDATE devices SET is_bootstrap_done=1 WHERE serial_number=?", (serial,))
            # Encolar la autoconfiguración de forma asíncrona
            cursor.execute("""
                INSERT INTO pending_commands (serial_number, rpc_method, parameter_name, parameter_value, parameter_type)
                VALUES (?, 'SetPerAuto', '', '', 'string')
            """, (serial,))
            conn.commit()
            conn.close()

        # SIEMPRE responder con InformResponse estándar al procesar un Inform
        #push_log(serial, "InformResponse", "SENT_XML", INFORM_RESPONSE)
        #return Response(INFORM_RESPONSE, content_type='text/xml')

        # Detectar si la ONT usa 'soap-env' o 'SOAP-ENV' dinámicamente
        prefix_match = re.search(r'<([a-zA-Z0-9_-]+):Envelope', raw_xml)
        soap_prefix = prefix_match.group(1) if prefix_match else "SOAP-ENV"

        cwmp_id = extract_cwmp_id(raw_xml)
        resp_xml = build_inform_response(cwmp_id, soap_prefix)

        # SIEMPRE responder con InformResponse usando el cwmp:ID dinámico
        push_log(serial, "InformResponse", "SENT_XML", resp_xml)
        return Response(resp_xml, content_type='text/xml')

    serial = extract_serial_from_xml_or_db(raw_xml, client_ip)

    if raw_xml.strip():
        push_log(serial, "SOAP_RESPONSE", "RECEIVED_XML", raw_xml)
        extract_device_metrics(serial, raw_xml)

        if "9005" in raw_xml or "Invalid parameter name" in raw_xml or "Fault" in raw_xml:
            conn_ac = get_db()
            cur_ac = conn_ac.cursor()
            cur_ac.execute("SELECT * FROM pending_commands WHERE serial_number=? ORDER BY id DESC LIMIT 1", (serial,))
            last_cmd = cur_ac.fetchone()

            if last_cmd and last_cmd['rpc_method'] == 'GetParameterValues':
                retry_count = last_cmd['retry_count'] if 'retry_count' in last_cmd.keys() else 0
                if retry_count < 1:
                    current_param = last_cmd['parameter_name'] or ""
                    alt_root = "Device." if "InternetGatewayDevice." in current_param else "InternetGatewayDevice."
                    cur_ac.execute("UPDATE devices SET protocol_root=? WHERE serial_number=?", (alt_root, serial))
                    cur_ac.execute("UPDATE pending_commands SET retry_count=1 WHERE id=?", (last_cmd['id'],))
                    conn_ac.commit()

                    rpc = last_cmd['rpc_method']
                    p_name = format_parameter_name(current_param, alt_root)
                    soap_req = build_soap_request(rpc, p_name, last_cmd['parameter_value'] or "",
                                                  last_cmd['parameter_type'] or "string")
                    conn_ac.close()
                    push_log(serial, f"{rpc} (Reintento con {alt_root})", "SENT_XML", soap_req)
                    return Response(soap_req, content_type='text/xml')

            conn_ac.close()
            push_log(serial, "AutoCorrection", "SKIPPED_FAULT",
                     "Parámetro no soportado por el CPE. Omitiendo y continuando con la cola.")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM pending_commands WHERE serial_number=? AND status='PENDING' ORDER BY id ASC LIMIT 1",
                   (serial,))
    cmd = cursor.fetchone()

    if cmd:
        cmd_id = cmd['id']
        sn = cmd['serial_number']
        rpc = cmd['rpc_method']
        p_name = cmd['parameter_name'] or ""
        p_val = cmd['parameter_value'] or ""
        p_type = cmd['parameter_type'] or "string"

        cursor.execute("UPDATE pending_commands SET status='EXECUTED' WHERE id=?", (cmd_id,))
        conn.commit()


        cursor.execute("SELECT protocol_root FROM devices WHERE serial_number=?", (sn,))
        dev_proto_row = cursor.fetchone()
        device_root = dev_proto_row['protocol_root'] if (
                    dev_proto_row and dev_proto_row['protocol_root']) else "Device."
        conn.close()

        if rpc == "SetPerAuto":
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT inform_interval, conn_req_user, conn_req_pass FROM default_config WHERE id=1")
            cfg = cursor.fetchone()
            conn.close()
            soap_req = build_set_per_soap(cfg['inform_interval'], cfg['conn_req_user'], cfg['conn_req_pass'],
                                          device_root)
            push_log(sn, "SetParameterValues (set_per)", "SENT_XML", soap_req)
            return Response(soap_req, content_type='text/xml')

        if rpc != "FirmwareUpgrade":
            formatted_names = [format_parameter_name(p, device_root) for p in p_name.split("|")]
            p_name = "|".join(formatted_names)

        soap_req = build_soap_request(rpc, p_name, p_val, p_type)
        push_log(sn, rpc, "SENT_XML", soap_req)
        return Response(soap_req, content_type='text/xml')

    conn.close()
    push_log(serial, "Session", "COMPLETED", "Sesión TR-069 finalizada. HTTP 204.")
    return Response(status=204)


def trigger_connection_request(serial, conn_url):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT conn_req_user, conn_req_pass FROM default_config WHERE id=1")
        cfg = cursor.fetchone()
        conn.close()

        user = cfg['conn_req_user'] if cfg else 'sagemcom'
        password = cfg['conn_req_pass'] if cfg else 'sagemcom'

        push_log(serial, "ConnectionRequest", "SENT_HTTP",
                 f"Enviando HTTP GET (Connection Request) a:\n{conn_url} [User: {user}]")

        response = requests.get(conn_url, auth=HTTPDigestAuth(user, password), timeout=5)

        push_log(serial, "ConnectionRequest", "RECEIVED_HTTP",
                 f"CPE respondió correctamente con HTTP {response.status_code}")
    except Exception as e:
        push_log(serial, "ConnectionRequest", "TIMEOUT/ERROR", f"No se pudo contactar al CPE: {e}")


@app.route('/api/devices', methods=['GET'])
def get_devices():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM devices WHERE last_inform < datetime('now', '-7 days')")
    conn.commit()

    cursor.execute("""
        SELECT serial_number, model_name, software_version, connection_request_url, ip_address, 
               last_inform, status, manufacturer, gpon_serial, uptime, hardware_version, 
               ram_total, ram_free, lan1, lan2, lan3, lan4, lan_status, 
               ssid_24g, ssid_5g, ssid_1, ssid_2, ssid_3, ssid_4, ssid_5, ssid_6, ssid_7, ssid_8,
               ssid_key_1, ssid_key_2, ssid_key_3, ssid_key_4, ssid_key_5, ssid_key_6, ssid_key_7, ssid_key_8,
               voice_enable, voice_profile, sip_proxy, sip_number, voip_status,
               optical_status, optical_rx, optical_tx, optical_temp, optical_voltage,
               vlan_info, vlan_1, vlan_2, vlan_3, vlan_4, vlan_5, ip_data, ip_mgmt, ip_iptv, ip_voip, dns_servers, protocol_root,
               host_count, host_name, host_ip_mac, host_signal,
               host_1, host_2, host_3, host_4, host_5, host_6, host_7
        FROM devices ORDER BY last_inform DESC
    """)
    rows = cursor.fetchall()
    devices = [dict(row) for row in rows]
    for d in devices:
        d['is_online'] = (d['status'] == 'Online')
        d['status_badge'] = "Online" if d['is_online'] else "Offline"
    conn.close()
    return jsonify(devices)


@app.route('/api/device/refresh/<serial>', methods=['POST'])
def refresh_device_details(serial):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT connection_request_url, model_name, protocol_root FROM devices WHERE serial_number=?",
                   (serial,))
    dev = cursor.fetchone()

    if not dev:
        conn.close()
        return jsonify({"status": "error", "message": "Dispositivo no encontrado."}), 404

    dev_root = dev['protocol_root'] if dev and 'protocol_root' in dev and dev['protocol_root'] else "Device."

    if dev_root == "InternetGatewayDevice.":
        params_to_get = [
            "InternetGatewayDevice.DeviceInfo.Manufacturer",
            "InternetGatewayDevice.DeviceInfo.ModelName",
            "InternetGatewayDevice.DeviceInfo.SerialNumber",
            "InternetGatewayDevice.DeviceInfo.GponSerialNumber",
            "InternetGatewayDevice.DeviceInfo.HardwareVersion",
            "InternetGatewayDevice.DeviceInfo.SoftwareVersion",
            "InternetGatewayDevice.DeviceInfo.UpTime",
            "InternetGatewayDevice.DeviceInfo.MemoryStatus.Total",
            "InternetGatewayDevice.DeviceInfo.MemoryStatus.Free",
            "InternetGatewayDevice.Bridging.Bridge.1.Port.2.Status",
            "InternetGatewayDevice.Bridging.Bridge.1.Port.3.Status",
            "InternetGatewayDevice.Bridging.Bridge.1.Port.4.Status",
            "InternetGatewayDevice.Bridging.Bridge.1.Port.5.Status",
            "InternetGatewayDevice.ManagementServer.ConnectionRequestURL",
            "InternetGatewayDevice.Services.VoiceService.1.Enable",
            "InternetGatewayDevice.Services.VoiceService.1.VoiceProfile.1.Status",
            "InternetGatewayDevice.Services.VoiceService.1.VoiceProfile.1.SIP.ProxyServer",
            "InternetGatewayDevice.Services.VoiceService.1.VoiceProfile.1.Line.1.DirectoryNumber",
            "InternetGatewayDevice.Optical.Interface.1.OpticalSignalLevel",
            "InternetGatewayDevice.X_SAGEMCOM_Optical.TxPower",
            "Device.Optical.Interface.1.X_MM_Temperature",
            "InternetGatewayDevice.Hosts.HostNumberOfEntries",
            "InternetGatewayDevice.Hosts.Host.1.",
            "InternetGatewayDevice.Hosts.Host.2.",
            "InternetGatewayDevice.Hosts.Host.3.",
            "InternetGatewayDevice.Hosts.Host.4.",
            "InternetGatewayDevice.Hosts.Host.5.",
            "InternetGatewayDevice.Hosts.Host.6.",
            "InternetGatewayDevice.Hosts.Host.7."
        ]
        for i in range(1, 9):
            params_to_get.append(f"InternetGatewayDevice.LANDevice.1.WLANConfiguration.{i}.SSID")
            params_to_get.append(f"InternetGatewayDevice.LANDevice.1.WLANConfiguration.{i}.PreSharedKey.1.PreSharedKey")
    else:
        params_to_get = [
            "Device.DeviceInfo.Manufacturer",
            "Device.DeviceInfo.ModelName",
            "Device.DeviceInfo.SerialNumber",
            "Device.DeviceInfo.GponSerialNumber",
            "Device.DeviceInfo.HardwareVersion",
            "Device.DeviceInfo.SoftwareVersion",
            "Device.DeviceInfo.UpTime",
            "Device.DeviceInfo.MemoryStatus.Total",
            "Device.DeviceInfo.MemoryStatus.Free",
            "Device.Bridging.Bridge.1.Port.2.Status",
            "Device.Bridging.Bridge.1.Port.3.Status",
            "Device.Bridging.Bridge.1.Port.4.Status",
            "Device.Bridging.Bridge.1.Port.5.Status",
            "Device.ManagementServer.ConnectionRequestURL",
            "Device.Services.VoiceService.1.Enable",
            "Device.Services.VoiceService.1.VoiceProfile.1.Status",
            "Device.Services.VoiceService.1.VoiceProfile.1.SIP.ProxyServer",
            "Device.Services.VoiceService.1.VoiceProfile.1.Line.1.DirectoryNumber",
            "Device.Optical.Interface.1.Status",
            "Device.Optical.Interface.1.OpticalSignalLevel",
            "Device.Optical.Interface.1.TransmitOpticalLevel",
            "Device.Optical.Interface.1.X_MM_Temperature",
            "Device.Optical.Interface.1.Voltage",
            "Device.Hosts.HostNumberOfEntries",
            "Device.Hosts.Host.1.",
            "Device.Hosts.Host.2.",
            "Device.Hosts.Host.3.",
            "Device.Hosts.Host.4.",
            "Device.Hosts.Host.5.",
            "Device.Hosts.Host.6.",
            "Device.Hosts.Host.7."
        ]
        for i in range(1, 9):
            params_to_get.append(f"Device.WiFi.SSID.{i}.SSID")
            params_to_get.append(f"Device.WiFi.AccessPoint.{i}.Security.KeyPassphrase")

        for i in range(1, 5):
            params_to_get.extend([
                f"Device.Ethernet.VLANTermination.{i}.Name",
                f"Device.Ethernet.VLANTermination.{i}.VLANID",
                f"Device.Ethernet.VLANTermination.{i}.Alias",
                f"Device.Ethernet.VLANTermination.{i}.Status",
                f"Device.DHCPv4.Client.{i}.Enable",
                f"Device.DHCPv4.Client.{i}.Alias",
                f"Device.DHCPv4.Client.{i}.Status",
                f"Device.DHCPv4.Client.{i}.IPAddress",
                f"Device.DHCPv4.Client.{i}.SubnetMask",
                f"Device.DHCPv4.Client.{i}.IPRouters",
                f"Device.DHCPv4.Client.{i}.DNSServers"
            ])

    for param in params_to_get:
        cursor.execute(
            "INSERT INTO pending_commands (serial_number, rpc_method, parameter_name, parameter_value, parameter_type, retry_count) VALUES (?, 'GetParameterValues', ?, '', 'string', 0)",
            (serial, param)
        )

    conn.commit()
    conn.close()

    push_log(serial, "GetParameterValues", "QUEUED",
             f"Se encolaron {len(params_to_get)} parámetros de forma individual (1 en 1).")

    if dev['connection_request_url']:
        threading.Thread(target=trigger_connection_request, args=(serial, dev['connection_request_url'])).start()

    return jsonify({"status": "success", "message": "Parámetros encolados uno a uno."})


@app.route('/api/default_config', methods=['GET', 'POST'])
def manage_default_config():
    conn = get_db()
    cursor = conn.cursor()
    if request.method == 'POST':
        interval = request.form.get('inform_interval', 3600, type=int)
        user = request.form.get('conn_req_user', 'sagemcom').strip()
        password = request.form.get('conn_req_pass', 'sagemcom').strip()
        cursor.execute("UPDATE default_config SET inform_interval=?, conn_req_user=?, conn_req_pass=? WHERE id=1",
                       (interval, user, password))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "Actualizado."})
    cursor.execute("SELECT inform_interval, conn_req_user, conn_req_pass FROM default_config WHERE id=1")
    cfg = dict(cursor.fetchone())
    conn.close()
    return jsonify(cfg)


@app.route('/api/execute', methods=['POST'])
def execute_rpc():
    serial = request.form.get('serialseleccionado', '').strip()
    rpc = request.form.get('seleccion', '').strip()
    param_name = request.form.get('parametro', '').strip()
    param_val = request.form.get('valor', '').strip()
    param_type = request.form.get('tipo', 'string').strip()

    if not serial or not rpc:
        return jsonify({"status": "error", "message": "Faltan datos."}), 400

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

    push_log(serial, rpc, "QUEUED", f"Comando {rpc} encolado.")
    if dev and dev['connection_request_url']:
        threading.Thread(target=trigger_connection_request, args=(serial, dev['connection_request_url'])).start()

    return jsonify({"status": "success", "message": "Comando encolado."})


@app.route('/api/upload', methods=['POST'])
def upload_firmware():
    if 'fileToUpload' in request.files:
        f = request.files['fileToUpload']
        if f.filename:
            f.save(os.path.join(FW_DIR, f.filename))
    return redirect("/")


@app.route('/api/pending_commands', methods=['GET'])
def get_pending_commands():
    conn = get_db()
    cursor = conn.cursor()
    # Limpieza automática de comandos antiguos (ejecutados/fallidos de más de 7 días)
    #cursor.execute("DELETE FROM pending_commands WHERE created_at < datetime('now', '-7 days') AND status != 'PENDING'")
    #conn.commit()
    #cursor.execute("SELECT id, serial_number, rpc_method, parameter_name, parameter_value, status, created_at FROM pending_commands ORDER BY id DESC LIMIT 500")
    cursor.execute("SELECT id, serial_number, rpc_method, parameter_name, parameter_value, created_at FROM pending_commands WHERE status='PENDING' ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])

@app.route('/api/pending_commands/cancel/<int:cmd_id>', methods=['POST'])
def cancel_pending_command(cmd_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM pending_commands WHERE id=? AND status='PENDING'", (cmd_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "success", "message": "Comando cancelado."})

@app.route('/api/pending_commands/cancel_all', methods=['POST'])
def cancel_all_pending_commands():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM pending_commands WHERE status='PENDING'")
    conn.commit()
    conn.close()
    return jsonify({"status": "success", "message": "Todos los comandos pendientes han sido cancelados."})


@app.route('/api/logs/stream')
def stream_logs():
    def event_stream():
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT timestamp, serial_number, rpc_method, status, log_detail FROM execution_logs ORDER BY id DESC LIMIT 15")
        rows = cursor.fetchall()[::-1]
        conn.close()
        for r in rows:
            yield f"data: {json.dumps(dict(r))}\n\n"
        while True:
            try:
                data = log_queue.get(timeout=15)
                yield f"data: {json.dumps(data)}\n\n"
            except queue.Empty:
                yield ": keep-alive\n\n"

    return Response(event_stream(), mimetype="text/event-stream", headers={
        'Cache-Control': 'no-cache, no-transform',
        'X-Accel-Buffering': 'no',
        'Connection': 'keep-alive'
    })


@app.route('/api/logs/clear', methods=['POST'])
def clear_logs():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM execution_logs")
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})


@app.route('/api/logs/download', methods=['GET'])
def download_logs():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT timestamp, serial_number, rpc_method, status, log_detail FROM execution_logs ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    text = "".join([
        f"[{r['timestamp']}] [{r['serial_number']}] [{r['rpc_method']}] [{r['status']}]\n{r['log_detail']}\n" + "-" * 80 + "\n"
        for r in rows])
    return Response(text, mimetype="text/plain", headers={"Content-Disposition": "attachment;filename=logs.txt"})

@app.route('/api/logs/json', methods=['GET'])
def get_logs_json():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT timestamp, serial_number as serial, rpc_method as rpc, status, log_detail as detail FROM execution_logs ORDER BY id DESC LIMIT 15")
    rows = cursor.fetchall()[::-1]
    conn.close()
    return jsonify([dict(row) for row in rows])


@app.route('/api/device/delete/<serial>', methods=['POST'])
def delete_device(serial):
    conn = get_db()
    cursor = conn.cursor()
    # Borramos el dispositivo de la tabla devices
    cursor.execute("DELETE FROM devices WHERE serial_number=?", (serial,))
    # Opcional: También puedes limpiar los comandos pendientes asociados a este equipo
    cursor.execute("DELETE FROM pending_commands WHERE serial_number=?", (serial,))
    conn.commit()
    conn.close()

    push_log(serial, "DELETE_DEVICE", "SUCCESS", f"Dispositivo {serial} eliminado manualmente desde el panel.")
    return jsonify({"status": "success", "message": f"Dispositivo {serial} eliminado correctamente."})

def extract_cwmp_id(xml_str):
    m = re.search(r'<cwmp:ID[^>]*>(.*?)</cwmp:ID>', xml_str, re.DOTALL | re.IGNORECASE)
    if m and m.group(1).strip():
        return m.group(1).strip()
    return "0"

def build_inform_response(cwmp_id="0", soap_prefix="SOAP-ENV"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<{soap_prefix}:Envelope xmlns:{soap_prefix}="http://schemas.xmlsoap.org/soap/envelope/" xmlns:cwmp="urn:dslforum-org:cwmp-1-0">
   <{soap_prefix}:Header>
      <cwmp:ID {soap_prefix}:mustUnderstand="1">{cwmp_id}</cwmp:ID>
   </{soap_prefix}:Header>
   <{soap_prefix}:Body>
      <cwmp:InformResponse>
         <MaxEnvelopes>1</MaxEnvelopes>
      </cwmp:InformResponse>
   </{soap_prefix}:Body>
</{soap_prefix}:Envelope>"""

def build_transfer_complete_response(cwmp_id="1"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:cwmp="urn:dslforum-org:cwmp-1-0">
   <SOAP-ENV:Header>
      <cwmp:ID SOAP-ENV:mustUnderstand="1">{cwmp_id}</cwmp:ID>
   </SOAP-ENV:Header>
   <SOAP-ENV:Body>
      <cwmp:TransferCompleteResponse/>
   </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>ACS Sagemcom Control Panel v2.0</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <style>
        body { background-color: #f4f6f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
        .acs-header { background-color: #1e293b; color: #ffffff; padding: 16px 24px; border-radius: 8px 8px 0 0; }
        .acs-url-badge { background-color: #334155; padding: 6px 12px; border-radius: 6px; font-size: 0.85rem; display: inline-flex; align-items: center; gap: 8px; border: 1px solid #475569; }
        .acs-url-badge code { color: #38bdf8; font-family: monospace; font-weight: bold; }
        #terminal { background-color: #111827; color: #10b981; font-family: 'Consolas', monospace; padding: 15px; height: 580px; overflow-y: auto; border-radius: 0 0 6px 6px; font-size: 0.85rem; line-height: 1.4; }
        .log-header { color: #38bdf8; font-weight: bold; margin-top: 10px; }
        .xml-box { background-color: #1e293b; color: #ffffff; padding: 10px; border-left: 3px solid #3b82f6; border-radius: 4px; margin: 6px 0; white-space: pre-wrap; word-break: break-all; font-family: 'Consolas', monospace; }
        .xml-box-in { border-left-color: #10b981; }
        .xml-box-out { border-left-color: #f59e0b; }
        .status-dot { height: 12px; width: 12px; border-radius: 50%; display: inline-block; margin-right: 6px; }
        .dot-online { background-color: #10b981; box-shadow: 0 0 8px #10b981; }
        .dot-offline { background-color: #ef4444; box-shadow: 0 0 8px #ef4444; }
        .device-row { cursor: pointer; transition: background 0.2s; }
        .device-row:hover { background-color: #e2e8f0; }
        .card-detail-section { border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px; background-color: #f8fafc; height: 100%; }
        .card-detail-section h6 { font-size: 0.95rem; color: #2563eb; font-weight: 700; margin-bottom: 10px; border-bottom: 1px solid #e2e8f0; padding-bottom: 6px; }
        .detail-item { display: flex; justify-content: space-between; font-size: 0.85rem; margin-bottom: 6px; }
        .detail-label { color: #64748b; font-weight: 500; }
        .detail-val { font-weight: 600; color: #0f172a; text-align: right; }
        .tag-up { color: #16a34a; font-weight: bold; }
        #deviceDetailModal .modal-header { cursor: move; }
        .param-row { background: #f8fafc; border: 1px dashed #cbd5e1; padding: 8px; border-radius: 6px; }
    </style>
</head>
<body>
<div class="container-fluid py-4 px-4">
    <header class="acs-header d-flex justify-content-between align-items-center mb-0">
        <div><h1 class="h4 mb-0 fw-bold">ACS Sagemcom Control Panel v2.0</h1></div>
        <div class="acs-url-badge">
            <span>URL ACS CPE:</span>
            <code id="acsUrlCode">http://192.168.35.11:9034/MyAcs/Default_stb.aspx</code>
            <button class="btn btn-sm btn-primary py-0 px-2" onclick="copyAcsUrl()">Copiar</button>
        </div>
    </header>

    <div class="bg-dark text-white px-4 py-2 fs-7 fw-bold mb-4 border-bottom border-primary">Ejecución de Comandos TR-069</div>

    <div class="row g-4 mb-4">
        <div class="col-lg-5">
            <div class="card shadow-sm mb-4">
                <div class="card-header bg-secondary text-white fw-bold">Panel de Control TR-069</div>
                <div class="card-body">
                    <form id="acsForm">
                        <div class="mb-3">
                            <label class="form-label fw-bold">Dispositivo ONT / CPE:</label>
                            <select id="serialseleccionado" name="serialseleccionado" class="form-select" required>
                                <option value="">Cargando dispositivos...</option>
                            </select>
                        </div>
                        <div class="mb-3">
                            <label class="form-label fw-bold">Comando RPC:</label>
                            <select id="seleccion" name="seleccion" class="form-select" onchange="bloquearCampos()" required>
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

                        <!-- Sección de Campos Dinámicos -->
                        <div class="mb-3">
                            <div class="d-flex justify-content-between align-items-center mb-2">
                                <label class="form-label fw-bold mb-0">Parámetros / Valores:</label>
                                <button type="button" class="btn btn-sm btn-outline-success py-0 px-2 fw-bold" onclick="agregarFilaParametro()">+ Añadir Parámetro</button>
                            </div>
                            <div id="paramContainer"></div>
                        </div>
                        <button type="submit" class="btn btn-primary w-100 py-2 fw-bold">Ejecutar Comando</button>
                    </form>
                </div>
            </div>

            <div class="card shadow-sm">
                <div class="card-header bg-secondary text-white fw-bold d-flex justify-content-between align-items-center">
                    <span>Subir Firmware (/var/www/html/fw/)</span>
                    <a href="http://192.168.35.9/fw/" target="_blank" class="btn btn-sm btn-light text-dark py-0 px-2 fw-bold">Ver Firmwares</a>
                </div>
                <div class="card-body">
                    <form action="/api/upload" method="post" enctype="multipart/form-data" class="row g-3">
                        <div class="col-8"><input class="form-control" type="file" name="fileToUpload" required></div>
                        <div class="col-4"><button type="submit" class="btn btn-outline-dark w-100">Upload FW</button></div>
                    </form>
                </div>
            </div>
        </div>

        <div class="col-lg-7">
            <div class="card shadow-sm">
                <div class="card-header bg-dark text-white d-flex justify-content-between align-items-center fw-bold">
                    <span>Consola TR-069 & Traza XML en Vivo</span>
                    <div>
                        <a href="/api/logs/download" class="btn btn-sm btn-outline-success me-2" download>Descargar Log (.txt)</a>
                        <button class="btn btn-sm btn-outline-danger" onclick="limpiarTerminal()">Limpiar Log</button>
                    </div>
                </div>
                <div class="card-body p-0"><div id="terminal"></div></div>
            </div>
        </div>
    </div>

    <div class="row g-4">
        <div class="col-lg-4">
            <div class="card shadow-sm">
                <div class="card-header bg-primary text-white fw-bold">Auto-Aprovisionamiento (set_per)</div>
                <div class="card-body">
                    <form id="configForm">
                        <div class="mb-3">
                            <label class="form-label fw-bold">PeriodicInformInterval (s):</label>
                            <input type="number" id="inform_interval" name="inform_interval" class="form-control" required>
                        </div>
                        <div class="mb-3">
                            <label class="form-label fw-bold">Conn Request Username:</label>
                            <input type="text" id="conn_req_user" name="conn_req_user" class="form-control" required>
                        </div>
                        <div class="mb-3">
                            <label class="form-label fw-bold">Conn Request Password:</label>
                            <input type="text" id="conn_req_pass" name="conn_req_pass" class="form-control" required>
                        </div>
                        <button type="submit" class="btn btn-outline-primary w-100 fw-bold">Guardar Valores</button>
                    </form>
                </div>
            </div>
        </div>

        <div class="col-lg-8">
            <div class="card shadow-sm">
                <div class="card-header bg-dark text-white fw-bold d-flex justify-content-between align-items-center">
                    <span>Equipos ONT Registrados (Última Semana)</span>
                    <span class="badge bg-secondary" id="total_devices_count">0 Equipos</span>
                </div>
                <div class="card-body p-0 table-responsive" style="max-height: 330px;">
                    <table class="table table-hover align-middle mb-0">
                        <thead class="table-light">
                            <tr><th>Estado</th><th>Serial</th><th>Modelo</th><th>IP Datos</th><th>Firmware</th><th>Hardware</th><th>Último Inform</th><th>Acciones</th></tr>
                        </thead>
                        <tbody id="devicesTableBody"><tr><td colspan="8" class="text-center py-3">Cargando equipos...</td></tr></tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <div class="row g-4 mt-2">
        <div class="col-12">
            <div class="card shadow-sm">
                <div class="card-header bg-warning text-dark fw-bold d-flex justify-content-between align-items-center">
                    <span>Comandos Pendientes en Cola (Pending Commands)</span>
                    <div>
                        <span class="badge bg-dark me-2" id="total_pending_count">0 Pendientes</span>
                        <button class="btn btn-sm btn-outline-danger" onclick="cancelarTodosComandos()">Cancelar Todos</button>
                    </div>
                </div>
                <div class="card-body p-0 table-responsive" style="max-height: 250px;">
                    <table class="table table-hover align-middle mb-0">
                        <thead class="table-light">
                            <tr><th>ID</th><th>Serial ONT</th><th>Método RPC</th><th>Parámetro / Valor</th><th>Fecha Creación</th><th>Acción</th></tr>
                        </thead>
                        <tbody id="pendingCommandsTableBody"><tr><td colspan="6" class="text-center py-3">No hay comandos encolados.</td></tr></tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>
</div>

<div class="modal fade" id="deviceDetailModal" tabindex="-1" aria-hidden="true">
    <div class="modal-dialog modal-dialog-centered modal-xl">
        <div class="modal-content">
            <div class="modal-header bg-dark text-white">
                <h5 class="modal-title" id="modalDeviceTitle">📄 Detalles de la ONT</h5>
                <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="Close"></button>
            </div>
            <div class="modal-body">
                <div class="row g-3">
                    <div class="col-md-4">
                        <div class="card-detail-section">
                            <h6>1. Info General & Hardware</h6>
                            <div class="detail-item"><span class="detail-label">Fabricante:</span><span class="detail-val" id="m_manufacturer">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Modelo:</span><span class="detail-val" id="m_model">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Nro. Serie:</span><span class="detail-val" id="m_serial">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Serial GPON:</span><span class="detail-val" id="m_gpon_serial">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Versión HW:</span><span class="detail-val" id="m_hw_ver">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Versión FW:</span><span class="detail-val" id="m_firmware">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Uptime:</span><span class="detail-val" id="m_uptime">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">RAM Total:</span><span class="detail-val" id="m_ram">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">RAM Libre:</span><span class="detail-val" id="m_ram_free">N/A</span></div>
                        </div>
                    </div>
                    <div class="col-md-4">
                        <div class="card-detail-section">
                            <h6>2. Puertos Ethernet (LAN)</h6>
                            <div class="detail-item"><span class="detail-label">LAN 1:</span><span class="detail-val" id="m_lan1">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">LAN 2:</span><span class="detail-val" id="m_lan2">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">LAN 3:</span><span class="detail-val" id="m_lan3">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">LAN 4:</span><span class="detail-val" id="m_lan4">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Resumen:</span><span class="detail-val" id="m_lan_status">N/A</span></div>
                        </div>
                    </div>
                    <div class="col-md-4">
                        <div class="card-detail-section">
                            <h6>3. Clientes Conectados (Hosts 1-7)</h6>
                            <div class="detail-item"><span class="detail-label">Total Hosts:</span><span class="detail-val" id="m_host_count">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Host 1:</span><span class="detail-val tag-up" id="m_host1">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Host 2:</span><span class="detail-val tag-up" id="m_host2">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Host 3:</span><span class="detail-val tag-up" id="m_host3">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Host 4:</span><span class="detail-val tag-up" id="m_host4">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Host 5:</span><span class="detail-val tag-up" id="m_host5">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Host 6:</span><span class="detail-val tag-up" id="m_host6">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Host 7:</span><span class="detail-val tag-up" id="m_host7">N/A</span></div>
                        </div>
                    </div>
                    <div class="col-md-4">
                        <div class="card-detail-section">
                            <h6>4. Redes Inalámbricas (Wi-Fi & Passwords)</h6>
                            <div class="detail-item"><span class="detail-label">SSID 1 (2.4G):</span><span class="detail-val tag-up" id="m_ssid1">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Pass 1:</span><span class="detail-val" id="m_ssid_key1">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">SSID 2 (5G):</span><span class="detail-val tag-up" id="m_ssid2">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Pass 2:</span><span class="detail-val" id="m_ssid_key2">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">SSID 3:</span><span class="detail-val tag-up" id="m_ssid3">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Pass 3:</span><span class="detail-val" id="m_ssid_key3">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">SSID 4:</span><span class="detail-val tag-up" id="m_ssid4">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Pass 4:</span><span class="detail-val" id="m_ssid_key4">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">SSID 5:</span><span class="detail-val tag-up" id="m_ssid5">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Pass 5:</span><span class="detail-val" id="m_ssid_key5">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">SSID 6:</span><span class="detail-val tag-up" id="m_ssid6">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Pass 6:</span><span class="detail-val" id="m_ssid_key6">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">SSID 7:</span><span class="detail-val tag-up" id="m_ssid7">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Pass 7:</span><span class="detail-val" id="m_ssid_key7">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">SSID 8:</span><span class="detail-val tag-up" id="m_ssid8">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Pass 8:</span><span class="detail-val" id="m_ssid_key8">N/A</span></div>
                        </div>
                    </div>
                    <div class="col-md-4">
                        <div class="card-detail-section">
                            <h6>5. Telefonía (VoIP / SIP)</h6>
                            <div class="detail-item"><span class="detail-label">Estado Voz:</span><span class="detail-val" id="m_voice_enable">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Perfil VoIP:</span><span class="detail-val" id="m_voice_profile">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Proxy SIP:</span><span class="detail-val" id="m_sip_proxy">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Nro. Directorio:</span><span class="detail-val" id="m_sip_number">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Estado FXS:</span><span class="detail-val" id="m_voip_status">N/A</span></div>
                        </div>
                    </div>
                    <div class="col-md-4">
                        <div class="card-detail-section">
                            <h6>6. Módulo Óptico GPON</h6>
                            <div class="detail-item"><span class="detail-label">Estado Óptico:</span><span class="detail-val" id="m_opt_status">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Potencia Tx:</span><span class="detail-val" id="m_opt_tx">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Potencia Rx:</span><span class="detail-val tag-up" id="m_opt_rx">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Temperatura:</span><span class="detail-val" id="m_opt_temp">N/A</span></div>
                            <div class="detail-item"><span class="detail-label">Voltaje:</span><span class="detail-val" id="m_opt_voltage">3.3 V</span></div>
                        </div>
                    </div>
                    <div class="col-md-12">
                        <div class="card-detail-section">
                            <h6>7. VLANs (1-5) e Información IP por Servicio</h6>
                            <div class="row">
                                <div class="col-md-12">
                                    <div class="detail-item"><span class="detail-label">VLAN 1:</span><span class="detail-val" id="m_vlan1">N/A</span></div>
                                    <div class="detail-item"><span class="detail-label">VLAN 2:</span><span class="detail-val" id="m_vlan2">N/A</span></div>
                                    <div class="detail-item"><span class="detail-label">VLAN 3:</span><span class="detail-val" id="m_vlan3">N/A</span></div>
                                    <div class="detail-item"><span class="detail-label">VLAN 4:</span><span class="detail-val" id="m_vlan4">N/A</span></div>
                                    <div class="detail-item"><span class="detail-label">VLAN 5:</span><span class="detail-val" id="m_vlan5">N/A</span></div>
                                </div>
                                <div class="col-md-6 mt-2">
                                    <div class="detail-item"><span class="detail-label">IP WAN Datos:</span><span class="detail-val" id="m_ip_data">N/A</span></div>
                                    <div class="detail-item"><span class="detail-label">IP Gestión (TR-069):</span><span class="detail-val" id="m_ip_mgmt">N/A</span></div>
                                </div>
                                <div class="col-md-6 mt-2">
                                    <div class="detail-item"><span class="detail-label">IP IPTV:</span><span class="detail-val" id="m_ip_iptv">N/A</span></div>
                                    <div class="detail-item"><span class="detail-label">IP VoIP:</span><span class="detail-val" id="m_ip_voip">N/A</span></div>
                                    <div class="detail-item"><span class="detail-label">DNS:</span><span class="detail-val" id="m_dns">N/A</span></div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="modal-footer d-flex justify-content-between">
                <button type="button" class="btn btn-success fw-bold" id="btnRefrescarDetalles" onclick="refrescarDetallesActuales()">🔄 Obtener / Actualizar Valores</button>
                <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cerrar</button>
            </div>
        </div>
    </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
    let currentDevices = [];
    let activeModalSerial = null;
    let printedLogKeys = new Set();

    function agregarFilaParametro(paramVal = '', tipoVal = 'string', valorVal = '') {
        const container = document.getElementById('paramContainer');
        const rowId = 'prow_' + Date.now() + '_' + Math.floor(Math.random() * 1000);
        const isFirst = container.children.length === 0;

        const row = document.createElement('div');
        row.className = 'param-row mb-2';
        row.id = rowId;
        row.innerHTML = `
            <div class="row g-2 align-items-center">
                <div class="col-param">
                    <input type="text" class="form-control param-input" placeholder="Parámetro (Ej: Device.WiFi.SSID.1.SSID)" value="${paramVal}">
                </div>
                <div class="col-type">
                    <select class="form-select type-input">
                        <option value="string" ${tipoVal === 'string' ? 'selected' : ''}>string</option>
                        <option value="unsignedInt" ${tipoVal === 'unsignedInt' ? 'selected' : ''}>unsignedInt</option>
                        <option value="boolean" ${tipoVal === 'boolean' ? 'selected' : ''}>boolean</option>
                        <option value="int" ${tipoVal === 'int' ? 'selected' : ''}>int</option>
                    </select>
                </div>
                <div class="col-val">
                    <input type="text" class="form-control val-input" placeholder="Valor Set" value="${valorVal}">
                </div>
                <div class="col-auto text-end">
                    ${isFirst ? 
                        `<button type="button" class="btn btn-success btn-sm py-1 px-2 fw-bold" onclick="agregarFilaParametro()">+</button>` : 
                        `<button type="button" class="btn btn-danger btn-sm py-1 px-2 fw-bold" onclick="eliminarFilaParametro('${rowId}')">-</button>`
                    }
                </div>
            </div>
        `;
        container.appendChild(row);
        bloquearCampos();
    }

    function eliminarFilaParametro(rowId) {
        const row = document.getElementById(rowId);
        if (row) row.remove();
    }

    function bloquearCampos() {
        const sel = document.getElementById('seleccion').value;
        const rows = document.querySelectorAll('.param-row');

        rows.forEach(row => {
            const colParam = row.querySelector('.col-param');
            const colType = row.querySelector('.col-type');
            const colVal = row.querySelector('.col-val');

            if (['Reboot', 'FactoryDefault'].includes(sel)) {
                if (colParam) colParam.style.display = 'none';
                if (colType) colType.style.display = 'none';
                if (colVal) colVal.style.display = 'none';
            } else if (sel === 'SetParameterValues') {
                if (colParam) { colParam.style.display = 'block'; colParam.className = 'col-md-5 col-param'; }
                if (colType) { colType.style.display = 'block'; colType.className = 'col-md-3 col-type'; }
                if (colVal) { colVal.style.display = 'block'; colVal.className = 'col-md-3 col-val'; }
            } else {
                if (colParam) { colParam.style.display = 'block'; colParam.className = 'col-md-11 col-param'; }
                if (colType) { colType.style.display = 'none'; }
                if (colVal) { colVal.style.display = 'none'; }
            }
        });
    }



    function copyAcsUrl() {
        navigator.clipboard.writeText(document.getElementById('acsUrlCode').innerText).then(() => alert('URL copiada.'));
    }

    function formatUpTime(seconds) {
        if (!seconds || seconds === 'N/A' || isNaN(seconds)) return seconds || 'N/A';
        const sec = parseInt(seconds, 10);
        return `${Math.floor(sec / 86400)}d ${Math.floor((sec % 86400) / 3600)}h ${Math.floor((sec % 3600) / 60)}m ${sec % 60}s`;
    }

    function cargarConfiguracionDefecto() {
        fetch('/api/default_config').then(res => res.json()).then(data => {
            document.getElementById('inform_interval').value = data.inform_interval;
            document.getElementById('conn_req_user').value = data.conn_req_user;
            document.getElementById('conn_req_pass').value = data.conn_req_pass;
        });
    }

    document.getElementById('configForm').addEventListener('submit', function(e) {
        e.preventDefault();
        fetch('/api/default_config', { method: 'POST', body: new FormData(this) }).then(res => res.json()).then(data => alert(data.message));
    });

    function cargarDispositivos() {
        fetch('/api/devices').then(res => res.json()).then(devices => {
            currentDevices = devices;
            const select = document.getElementById('serialseleccionado');
            const valActual = select.value;
            select.innerHTML = '<option value="">Seleccione serial / modelo</option>';

            devices.forEach(d => {
                const opt = document.createElement('option');
                opt.value = d.serial_number;
                opt.textContent = `[${d.model_name}] ${d.serial_number} — FW: ${d.software_version} (${d.status_badge})`;
                select.appendChild(opt);
            });
            select.value = valActual;

            const tbody = document.getElementById('devicesTableBody');
            document.getElementById('total_devices_count').textContent = `${devices.length} Equipos`;
            tbody.innerHTML = devices.length === 0 ? '<tr><td colspan="8" class="text-center py-3">No hay equipos registrados.</td></tr>' : '';

            devices.forEach(d => {
                const tr = document.createElement('tr');
                tr.className = 'device-row';
                tr.onclick = () => mostrarDetallesONT(d.serial_number);
                tr.innerHTML = `
                    <td><span class="status-dot ${d.is_online ? 'dot-online' : 'dot-offline'}"></span>${d.status_badge}</td>
                    <td class="fw-bold">${d.serial_number}</td>
                    <td>${d.model_name}</td>
                    <td>${d.ip_data || d.ip_address || 'N/A'}</td>
                    <td>${d.software_version}</td>
                    <td>${d.hardware_version || 'N/A'}</td>
                    <td class="small text-muted">${d.last_inform}</td>
                    <td>
                        <a href="http://192.168.35.9:9038/cpe?serial=${d.serial_number}" target="_blank" class="btn btn-sm btn-info text-white py-0 px-2 me-1" onclick="event.stopPropagation();">Consola</a>
                        <button class="btn btn-sm btn-outline-primary py-0 px-2 me-1" onclick="event.stopPropagation(); mostrarDetallesONT('${d.serial_number}')">Detalles</button>
                        <button class="btn btn-sm btn-outline-danger py-0 px-2 me-1" onclick="event.stopPropagation(); rebootDevice('${d.serial_number}')">Reboot</button>
                        <button class="btn btn-sm btn-danger py-0 px-2" onclick="event.stopPropagation(); eliminarDispositivo('${d.serial_number}')">Borrar</button>
                    </td>
                `;
                tbody.appendChild(tr);
            });

            if (activeModalSerial) {
                const updatedDev = currentDevices.find(x => x.serial_number === activeModalSerial);
                if (updatedDev) llenarModalDetalles(updatedDev);
            }
        });
    }

    function eliminarDispositivo(serial) {
        if (!confirm(`¿Está completamente seguro de eliminar el equipo ${serial} de la base de datos?`)) return;
    
        fetch(`/api/device/delete/${serial}`, {
            method: 'POST'
        })
        .then(res => res.json())
        .then(data => {
            alert(data.message);
            cargarDispositivos(); // Recarga la tabla de equipos inmediatamente
        })
        .catch(err => {
            console.error("Error al eliminar el dispositivo:", err);
            alert("Ocurrió un error al intentar eliminar el dispositivo.");
        });
    }
    function rebootDevice(serial) {
        if (!confirm(`¿Está seguro de reiniciar la ONT con serial ${serial}?`)) return;
        const formData = new URLSearchParams();
        formData.append('serialseleccionado', serial);
        formData.append('seleccion', 'Reboot');
        formData.append('parametro', '');
        formData.append('valor', '');
        formData.append('tipo', 'string');

        fetch('/api/execute', {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: formData
        }).then(res => res.json()).then(data => {
            alert(`Comando Reboot encolado para ${serial}`);
            cargarComandosPendientes();
        });
    }

    function cargarComandosPendientes() {
        fetch('/api/pending_commands').then(res => res.json()).then(commands => {
            const tbody = document.getElementById('pendingCommandsTableBody');
            document.getElementById('total_pending_count').textContent = `${commands.length} Pendientes`;
            tbody.innerHTML = commands.length === 0 ? '<tr><td colspan="6" class="text-center py-3">No hay comandos encolados.</td></tr>' : '';

            commands.forEach(c => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td><code>#${c.id}</code></td>
                    <td class="fw-bold">${c.serial_number}</td>
                    <td><span class="badge bg-secondary">${c.rpc_method}</span></td>
                    <td class="small text-truncate" style="max-width: 250px;" title="${c.parameter_name} ${c.parameter_value ? '= ' + c.parameter_value : ''}">
                        ${c.parameter_name || 'N/A'} ${c.parameter_value ? '= ' + c.parameter_value : ''}
                    </td>
                    <td class="small text-muted">${c.created_at}</td>
                    <td><button class="btn btn-sm btn-outline-danger py-0 px-2" onclick="cancelarComando(${c.id})">Cancelar</button></td>
                `;
                tbody.appendChild(tr);
            });
        });
    }

    function cancelarComando(id) {
        fetch(`/api/pending_commands/cancel/${id}`, { method: 'POST' }).then(res => res.json()).then(() => {
            cargarComandosPendientes();
        });
    }

    function cancelarTodosComandos() {
        if (!confirm('¿Desea cancelar todos los comandos pendientes?')) return;
        fetch('/api/pending_commands/cancel_all', { method: 'POST' }).then(res => res.json()).then(() => {
            cargarComandosPendientes();
        });
    }

    function mostrarDetallesONT(serial) {
        activeModalSerial = serial;
        const d = currentDevices.find(x => x.serial_number === serial);
        if (!d) return;
        llenarModalDetalles(d);
        new bootstrap.Modal(document.getElementById('deviceDetailModal')).show();
    }

    function llenarModalDetalles(d) {
        document.getElementById('modalDeviceTitle').textContent = `📄 Detalles ONT — ${d.model_name} (${d.serial_number})`;
        document.getElementById('m_manufacturer').textContent = d.manufacturer || 'Sagemcom';
        document.getElementById('m_model').textContent = d.model_name || 'N/A';
        document.getElementById('m_serial').textContent = d.serial_number || 'N/A';
        document.getElementById('m_gpon_serial').textContent = d.gpon_serial || 'N/A';
        document.getElementById('m_hw_ver').textContent = d.hardware_version || 'N/A';
        document.getElementById('m_firmware').textContent = d.software_version || 'N/A';
        document.getElementById('m_uptime').textContent = formatUpTime(d.uptime);
        document.getElementById('m_ram').textContent = d.ram_total || 'N/A';
        document.getElementById('m_ram_free').textContent = d.ram_free || 'N/A';

        document.getElementById('m_lan1').textContent = d.lan1 || 'N/A';
        document.getElementById('m_lan2').textContent = d.lan2 || 'N/A';
        document.getElementById('m_lan3').textContent = d.lan3 || 'N/A';
        document.getElementById('m_lan4').textContent = d.lan4 || 'N/A';
        document.getElementById('m_lan_status').textContent = d.lan_status || 'N/A';

        document.getElementById('m_host_count').textContent = d.host_count || 'N/A';
        for (let i = 1; i <= 7; i++) {
            const elHost = document.getElementById(`m_host${i}`);
            if (elHost) elHost.textContent = d[`host_${i}`] || 'N/A';
        }

        for (let i = 1; i <= 8; i++) {
            const elSsid = document.getElementById(`m_ssid${i}`);
            const elKey = document.getElementById(`m_ssid_key${i}`);
            if (elSsid) elSsid.textContent = d[`ssid_${i}`] || 'N/A';
            if (elKey) elKey.textContent = d[`ssid_key_${i}`] || 'N/A';
        }

        document.getElementById('m_voice_enable').textContent = d.voice_enable || 'N/A';
        document.getElementById('m_voice_profile').textContent = d.voice_profile || 'N/A';
        document.getElementById('m_sip_proxy').textContent = d.sip_proxy || 'N/A';
        document.getElementById('m_sip_number').textContent = d.sip_number || 'N/A';
        document.getElementById('m_voip_status').textContent = d.voip_status || 'N/A';

        document.getElementById('m_opt_status').textContent = d.optical_status || 'Up';
        document.getElementById('m_opt_tx').textContent = d.optical_tx || 'N/A';
        document.getElementById('m_opt_rx').textContent = d.optical_rx || 'N/A';
        document.getElementById('m_opt_temp').textContent = d.optical_temp || 'N/A';
        document.getElementById('m_opt_voltage').textContent = d.optical_voltage || '3.3 V';

        for (let i = 1; i <= 5; i++) {
            const elVlan = document.getElementById(`m_vlan${i}`);
            if (elVlan) elVlan.textContent = d[`vlan_${i}`] || 'N/A';
        }
        document.getElementById('m_ip_data').textContent = d.ip_data || d.ip_address || 'N/A';
        document.getElementById('m_ip_mgmt').textContent = d.ip_mgmt || 'N/A';
        document.getElementById('m_ip_iptv').textContent = d.ip_iptv || 'N/A';
        document.getElementById('m_ip_voip').textContent = d.ip_voip || 'N/A';
        document.getElementById('m_dns').textContent = d.dns_servers || 'N/A';
    }

    function refrescarDetallesActuales() {
        if (!activeModalSerial) return;
        const btn = document.getElementById('btnRefrescarDetalles');
        btn.disabled = true;
        btn.textContent = 'Consultando CPE 1 por 1...';

        fetch(`/api/device/refresh/${activeModalSerial}`, { method: 'POST' }).then(() => {
            setTimeout(() => { btn.disabled = false; btn.textContent = '🔄 Obtener / Actualizar Valores'; }, 3000);
        }).catch(() => { btn.disabled = false; btn.textContent = '🔄 Obtener / Actualizar VALUES'; });
    }


    document.getElementById('acsForm').addEventListener('submit', function(e) {
        e.preventDefault();

        const serial = document.getElementById('serialseleccionado').value;
        const rpc = document.getElementById('seleccion').value;

        const params = [];
        const types = [];
        const values = [];

        document.querySelectorAll('.param-row').forEach(row => {
            const p = row.querySelector('.param-input')?.value.trim() || '';
            const t = row.querySelector('.type-input')?.value.trim() || 'string';
            const v = row.querySelector('.val-input')?.value.trim() || '';

            if (p || ['Reboot', 'FactoryDefault'].includes(rpc)) {
                params.push(p);
                types.push(t);
                values.push(v);
            }
        });

        const formData = new URLSearchParams();
        formData.append('serialseleccionado', serial);
        formData.append('seleccion', rpc);
        formData.append('parametro', params.join('|'));
        formData.append('tipo', types.join('|'));
        formData.append('valor', values.join('|'));

        fetch('/api/execute', {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: formData
        }).then(res => res.json()).then(data => {
            appendTerminal({ timestamp: new Date().toLocaleTimeString(), serial: 'SYSTEM', rpc: 'COMMAND', status: 'EXEC', detail: `>>> ${data.message}` });
            cargarComandosPendientes();
        });
    });

    function formatXml(xml) {
        let formatted = '';
        let reg = /(>)(<)(\/*)/g;
        xml = xml.replace(reg, '$1\\n$2$3');
        let pad = 0;
        xml.split('\\n').forEach(function(node) {
            let indent = 0;
            if (node.match(/.+<\\/\\w[^>]*>$/)) {
                indent = 0;
            } else if (node.match(/^<\\/\\w/)) {
                if (pad !== 0) pad -= 1;
            } else if (node.match(/^<\\w[^>]*[^\\/]>.*$/)) {
                indent = 1;
            } else {
                indent = 0;
            }
            let padding = '';
            for (let i = 0; i < pad; i++) {
                padding += '  ';
            }
            formatted += padding + node + '\\n';
            pad += indent;
        });
        return formatted.trim();
    }

    function appendTerminal(data) {
        const term = document.getElementById('terminal');
        const serial = data.serial || data.serial_number || 'SYSTEM';
        const rpc = data.rpc || data.rpc_method || 'UNKNOWN';
        const timestamp = data.timestamp || '';
        const detailText = data.detail || data.log_detail || '';
        const logKey = `${timestamp}_${serial}_${rpc}_${data.status}_${detailText.length}`;

        if (printedLogKeys.has(logKey)) return;
        printedLogKeys.add(logKey);

        const header = document.createElement('div');
        header.className = 'log-header';
        header.textContent = `[${timestamp}] [${serial}] [${rpc}] [${data.status}]`;
        term.appendChild(header);

        const isXml = detailText.includes('<') && (
            detailText.toLowerCase().includes('envelope') || 
            detailText.toLowerCase().includes('xml') || 
            detailText.toLowerCase().includes('cwmp')
        );

        if (isXml) {
            const xmlBox = document.createElement('pre');
            xmlBox.className = 'xml-box ' + (data.status.includes('RECEIVED') ? 'xml-box-in' : 'xml-box-out');
            xmlBox.textContent = formatXml(detailText);
            term.appendChild(xmlBox);
        } else if (detailText) {
            const line = document.createElement('div');
            line.style.color = '#cbd5e1';
            line.textContent = detailText;
            term.appendChild(line);
        }
        term.scrollTop = term.scrollHeight;
    }
    
    function limpiarTerminal() {
        fetch('/api/logs/clear', { method: 'POST' }).then(() => { 
            printedLogKeys.clear();
            document.getElementById('terminal').innerHTML = ''; 
        });
    }

    function iniciarStreamLogs() {
        const evtSource = new EventSource('/api/logs/stream');
        evtSource.onmessage = function(e) {
            if (e.data) appendTerminal(JSON.parse(e.data));
        };
        evtSource.onerror = function() {
            evtSource.close();
            setTimeout(iniciarStreamLogs, 3000);
        };
    }

    function sincronizarTerminalRespaldo() {
        fetch('/api/logs/json')
            .then(res => res.json())
            .then(logs => {
                logs.forEach(log => appendTerminal(log));
            })
            .catch(err => console.error("Error en respaldo de logs:", err));
    }

    iniciarStreamLogs();
    cargarConfiguracionDefecto();
    cargarDispositivos();
    cargarComandosPendientes();

    setInterval(() => {
        cargarDispositivos();
        cargarComandosPendientes();
        sincronizarTerminalRespaldo();
    }, 2000);
    agregarFilaParametro();
</script>
</body>
</html>
"""


@app.route('/')
def index_dashboard():
    return render_template_string(HTML_TEMPLATE)


if __name__ == '__main__':
    print("Iniciando ACS Server v2.0 (Consultas 1 en 1) en puerto 9034...")
    app.run(host='0.0.0.0', port=80, debug=True, use_reloader=False, threaded=True)