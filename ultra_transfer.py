#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ULTRA TRANSFER: Automatización LFTP (Seedbox) -> Transfer.it
Descarga archivos o carpetas desde el seedbox usando LFTP (pget multisegmentado / mirror)
y los sube automáticamente a transfer.it usando la CLI oficial instalada.
"""

import os
import sys
import pty
import re
import json
import time
import shutil
import subprocess
import argparse
import urllib.request
from pathlib import Path
from datetime import datetime

CONFIG_PATH = Path("/root/.config/ultra/config.json")
DEFAULT_CONFIG = {
    "remote_host": "SE_HOST_O_IP",
    "remote_user": "tu_usuario",
    "remote_pass": "tu_password",
    "remote_dir": "/home/usuario/downloads/rtorrent",
    "local_dir": "/root/ultra",
    "links_dir": "/root/ultra",
    "transferit_bin": "/root/.local/bin/transferit",
    "pget_connections": 5,
    "transferit_concurrency": 4,
    "auto_delete_local": False,
    "discord_webhook": "https://discord.com/api/webhooks/TU_WEBHOOK_URL",
    "discord_include_link": True
}

LOG_FILE = Path("/root/ultra/ultra.log")

def log_msg(msg: str, level: str = "INFO"):
    """Registra eventos y errores en /root/ultra/ultra.log con marca temporal."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{level.upper()}] {msg}\n"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

# Colores ANSI
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_RED = "\033[91m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_BLUE = "\033[94m"
C_CYAN = "\033[96m"
C_DIM = "\033[2m"

def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                cfg = {**DEFAULT_CONFIG, **data}
                return cfg
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()

def ensure_lftp_bookmark(cfg):
    """Asegura que el bookmark 'ultra' exista en LFTP para conexión directa."""
    bm_file = Path("/root/.local/share/lftp/bookmarks")
    bm_file.parent.mkdir(parents=True, exist_ok=True)
    
    # URL encode password for bookmark
    import urllib.parse
    encoded_pass = urllib.parse.quote(cfg["remote_pass"], safe="")
    target_entry = f"ultra\tsftp://{cfg['remote_user']}:{encoded_pass}@{cfg['remote_host']}/"
    
    exists = False
    if bm_file.exists():
        lines = bm_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        for i, line in enumerate(lines):
            if line.startswith("ultra\t"):
                lines[i] = target_entry
                exists = True
                break
        if not exists:
            lines.append(target_entry)
        bm_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        bm_file.write_text(target_entry + "\n", encoding="utf-8")

def check_disk_space(local_dir):
    usage = shutil.disk_usage(local_dir)
    free_gb = usage.free / (1024**3)
    total_gb = usage.total / (1024**3)
    used_gb = usage.used / (1024**3)
    used_pct = (usage.used / usage.total) * 100
    return {
        "free_gb": free_gb,
        "total_gb": total_gb,
        "used_gb": used_gb,
        "used_pct": used_pct
    }

def send_discord_notify(cfg, item_name, status="OK", link=None):
    """
    Envía notificación minimalista a Discord.
    Formato:
      OK:             <item_name>: OK (+ enlace si está configurado)
      ERROR:          <item_name>: ERROR
      Otras acciones: <item_name>: <status>
    """
    webhook_url = cfg.get("discord_webhook")
    if not webhook_url:
        return
    clean_name = Path(item_name).name
    try:
        status_up = status.upper().strip()
        if status_up == "OK":
            if link and cfg.get("discord_include_link", True):
                content = f"{clean_name}: OK\n{link}"
            else:
                content = f"{clean_name}: OK"
        elif status_up == "ERROR":
            content = f"{clean_name}: ERROR"
        else:
            content = f"{clean_name}: {status}"

        payload = json.dumps({"content": content}).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "UltraTransfer/1.0"
            }
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            pass
        log_msg(f"Discord notificado ({status}): {clean_name}")
        print(f"    {C_DIM}[+] Notificación Discord enviada: {clean_name}: {status}{C_RESET}")
    except Exception as e:
        log_msg(f"Fallo al enviar notificación Discord para '{clean_name}': {e}", level="WARNING")
        print(f"{C_DIM}[!] Aviso Discord: {e}{C_RESET}")

def print_banner():
    print(f"{C_CYAN}{C_BOLD}" + "="*65)
    print("       ULTRA TRANSFER: Automatización LFTP + Transfer.it")
    print("="*65 + f"{C_RESET}")

def run_cmd_pty(cmd_args, input_data=None):
    """Ejecuta un comando en un pseudo-terminal para conservar barras de progreso vivas."""
    master, slave = pty.openpty()
    
    stdin_mode = subprocess.PIPE if input_data is not None else subprocess.DEVNULL
    proc = subprocess.Popen(
        cmd_args,
        stdin=stdin_mode,
        stdout=slave,
        stderr=slave,
        close_fds=True
    )
    os.close(slave)
    
    if input_data is not None and proc.stdin:
        try:
            proc.stdin.write(input_data.encode("utf-8"))
            proc.stdin.flush()
            proc.stdin.close()
        except Exception:
            pass

    captured = []
    while True:
        try:
            data = os.read(master, 1024)
            if not data:
                break
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
            captured.append(data.decode("utf-8", errors="ignore"))
        except OSError:
            break
            
    proc.wait()
    os.close(master)
    return proc.returncode, "".join(captured)

def list_remote_items(cfg, filter_query=None):
    """Lista los archivos y carpetas remotos disponibles en el rtorrent."""
    ensure_lftp_bookmark(cfg)
    remote_dir = cfg["remote_dir"].rstrip("/")
    lftp_cmd = f"cls -F '{remote_dir}/'; bye"
    
    res = subprocess.run(
        ["lftp", "ultra", "-e", lftp_cmd],
        capture_output=True,
        text=True
    )
    
    items = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        is_dir = line.endswith("/")
        clean_path = line.rstrip("/")
        name = clean_path.split("/")[-1]
        
        if not name:
            continue
            
        if filter_query and filter_query.lower() not in name.lower():
            continue
            
        items.append({
            "name": name,
            "is_dir": is_dir,
            "remote_path": f"{remote_dir}/{name}"
        })
        
    return items

def check_remote_item(cfg, item_name):
    """Determina si un ítem remoto específico existe y si es archivo o carpeta."""
    ensure_lftp_bookmark(cfg)
    remote_dir = cfg["remote_dir"].rstrip("/")
    remote_path = f"{remote_dir}/{item_name}"
    
    lftp_cmd = f"cls -F -d '{remote_path}'; bye"
    res = subprocess.run(
        ["lftp", "ultra", "-e", lftp_cmd],
        capture_output=True,
        text=True
    )
    
    lines = [l.strip() for l in res.stdout.splitlines() if l.strip()]
    for line in lines:
        if line.endswith("/") and (item_name in line or line.rstrip("/").endswith(item_name)):
            return {"exists": True, "is_dir": True, "remote_path": remote_path, "name": item_name}
        elif item_name in line:
            return {"exists": True, "is_dir": False, "remote_path": remote_path, "name": item_name}
            
    # Intentar búsqueda aproximada
    all_items = list_remote_items(cfg, filter_query=item_name)
    for it in all_items:
        if it["name"] == item_name:
            return {"exists": True, "is_dir": it["is_dir"], "remote_path": it["remote_path"], "name": it["name"]}
            
    if all_items:
        return {"exists": True, "is_dir": all_items[0]["is_dir"], "remote_path": all_items[0]["remote_path"], "name": all_items[0]["name"]}
        
    return {"exists": False, "is_dir": False, "remote_path": remote_path, "name": item_name}

def download_with_lftp(cfg, item_info):
    """Descarga un archivo o directorio usando LFTP con pget multisegmentado o mirror."""
    ensure_lftp_bookmark(cfg)
    local_dir = Path(cfg["local_dir"]).resolve()
    local_dir.mkdir(parents=True, exist_ok=True)
    
    connections = cfg.get("pget_connections", 5)
    name = item_info["name"]
    is_dir = item_info["is_dir"]
    remote_path = item_info["remote_path"]
    
    # Manejar elementos que vengan con subcarpetas en su ruta (ej: Carpeta/Archivo.mkv)
    clean_name = Path(name).name
    target_local_path = local_dir / clean_name
    if is_dir:
        target_local_path.mkdir(parents=True, exist_ok=True)
        
    log_msg(f"Iniciando descarga LFTP: '{name}' ({'DIR' if is_dir else 'FILE'}) -> '{target_local_path}'")
    send_discord_notify(cfg, clean_name, status="INICIANDO DESCARGA LFTP")
    
    print(f"\n{C_BLUE}{C_BOLD}[*] Iniciando descarga con LFTP...{C_RESET}")
    print(f"    {C_DIM}Remoto:{C_RESET} {remote_path}")
    print(f"    {C_DIM}Destino:{C_RESET} {target_local_path}")
    print(f"    {C_DIM}Tipo:{C_RESET} {'Directorio (mirror con pget)' if is_dir else 'Archivo (pget 5 segmentos)'}")
    print(f"    {C_DIM}Conexiones:{C_RESET} {connections}\n")
    
    if is_dir:
        # Comando mirror para directorio
        lftp_script = f"""
set net:timeout 20
set net:max-retries 5
lcd "{local_dir}"
mirror -c --use-pget-n={connections} "{remote_path}" "{target_local_path}"
bye
"""
    else:
        # Comando pget para archivo especificando destino con -o
        lftp_script = f"""
set net:timeout 20
set net:max-retries 5
lcd "{local_dir}"
pget -c -n {connections} "{remote_path}" -o "{target_local_path}"
bye
"""

    start_time = time.time()
    code, output = run_cmd_pty(["lftp", "ultra"], input_data=lftp_script)
    elapsed = time.time() - start_time
    
    # Comprobar también ruta anidada por si existiera previamente
    alt_nested = local_dir / name
    if not target_local_path.exists() and alt_nested.exists():
        target_local_path = alt_nested

    if not target_local_path.exists():
        log_msg(f"Error LFTP: destino no existe tras descarga: '{target_local_path}' (codigo {code}). Salida: {output.strip()[-300:]}", level="ERROR")
        print(f"\n{C_RED}[-] Error: La descarga con LFTP fallo o el destino no existe: {target_local_path}{C_RESET}")
        return False, None
        
    log_msg(f"Descarga LFTP exitosa: '{target_local_path}' en {elapsed:.1f}s")
    print(f"\n{C_GREEN}[+] Descarga con LFTP completada con exito en {elapsed:.1f}s!{C_RESET}")
    send_discord_notify(cfg, clean_name, status="DESCARGA LFTP COMPLETADA")
    return True, target_local_path

def upload_to_transferit(cfg, local_path):
    """Sube el archivo o carpeta a transfer.it usando la CLI instalada."""
    transferit_bin = cfg.get("transferit_bin", "/root/.local/bin/transferit")
    if not os.path.exists(transferit_bin):
        transferit_bin = shutil.which("transferit") or "/root/.local/bin/transferit"
        
    concurrency = cfg.get("transferit_concurrency", 4)
    clean_name = Path(local_path).name
    
    log_msg(f"Iniciando subida Transfer.it para '{local_path}' (concurrencia: {concurrency})")
    send_discord_notify(cfg, clean_name, status="SUBIENDO A TRANSFER.IT")
    print(f"\n{C_CYAN}{C_BOLD}[*] Subiendo a Transfer.it...{C_RESET}")
    print(f"    {C_DIM}Ruta:{C_RESET} {local_path}")
    print(f"    {C_DIM}Herramienta:{C_RESET} {transferit_bin}")
    print(f"    {C_DIM}Concurrencia:{C_RESET} {concurrency}\n")
    
    start_time = time.time()
    cmd = [transferit_bin, "upload", str(local_path), "-c", str(concurrency)]
    code, output = run_cmd_pty(cmd)
    elapsed = time.time() - start_time
    
    # Extraer el enlace generado
    urls = re.findall(r"https://transfer\.it/t/[a-zA-Z0-9_-]+", output)
    if not urls:
        log_msg(f"Error en subida Transfer.it para '{local_path}'. Salida: {output.strip()[-300:]}", level="ERROR")
        print(f"\n{C_RED}[-] No se pudo detectar el enlace de Transfer.it en la salida.{C_RESET}")
        return None
        
    link = urls[-1]
    log_msg(f"Subida exitosa Transfer.it en {elapsed:.1f}s: '{local_path}' -> {link}")
    print(f"\n{C_GREEN}{C_BOLD}[+] Subida completada exitosamente en {elapsed:.1f}s!{C_RESET}")
    print(f"    {C_BOLD}Enlace: {C_CYAN}{link}{C_RESET}")
    return link

def save_link_info(cfg, item_name, local_path, link):
    """Guarda el archivo .link.txt y actualiza el historial global."""
    links_dir = Path(cfg.get("links_dir", "/root/ultra"))
    links_dir.mkdir(parents=True, exist_ok=True)
    
    clean_name = Path(item_name).name
    
    # Calcular tamaño
    if os.path.isdir(local_path):
        total_size = sum(f.stat().st_size for f in Path(local_path).glob('**/*') if f.is_file())
    else:
        total_size = os.path.getsize(local_path) if os.path.exists(local_path) else 0
        
    size_mb = total_size / (1024 * 1024)
    size_gb = size_mb / 1024
    size_str = f"{size_gb:.2f} GB ({size_mb:.2f} MB)" if size_gb >= 1 else f"{size_mb:.2f} MB"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 1. Archivo individual <clean_name>.link.txt
    link_file = links_dir / f"{clean_name}.link.txt"
    with open(link_file, "w", encoding="utf-8") as f:
        f.write(f"Dosya: {clean_name}\n")
        f.write(f"Boyut: {size_str}\n")
        f.write(f"Link: {link}\n")
        f.write(f"Tarih: {now_str}\n")
    print(f"    {C_DIM}[+] Enlace guardado en: {link_file}{C_RESET}")

    # Copia en carpeta de historial /root/ultra/history/
    history_dir = links_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    try:
        with open(history_dir / f"{clean_name}.link.txt", "w", encoding="utf-8") as f:
            f.write(f"Dosya: {clean_name}\n")
            f.write(f"Boyut: {size_str}\n")
            f.write(f"Link: {link}\n")
            f.write(f"Tarih: {now_str}\n")
    except Exception:
        pass

    # Copia de cortesía en /transfer-it si la carpeta existe
    alt_dir = Path("/transfer-it")
    if alt_dir.exists() and alt_dir != links_dir:
        try:
            with open(alt_dir / f"{clean_name}.link.txt", "w", encoding="utf-8") as f:
                f.write(f"Dosya: {clean_name}\n")
                f.write(f"Boyut: {size_str}\n")
                f.write(f"Link: {link}\n")
                f.write(f"Tarih: {now_str}\n")
        except Exception:
            pass
            
    # 2. Maestro links.txt
    master_links = links_dir / "links.txt"
    with open(master_links, "a", encoding="utf-8") as f:
        f.write(f"[{now_str}] {clean_name} -> {link} ({size_str})\n")
        
    # 3. JSON de historial
    history_file = links_dir / "transfer_history.json"
    history = []
    if history_file.exists():
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = []
            
    history.append({
        "name": clean_name,
        "raw_name": item_name,
        "path": str(local_path),
        "size_bytes": total_size,
        "size_human": size_str,
        "link": link,
        "timestamp": now_str
    })
    
    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

    log_msg(f"Enlace guardado: '{clean_name}' -> {link} ({size_str})")

    # 4. Notificación Discord
    send_discord_notify(cfg, clean_name, status="OK", link=link)

def process_item(cfg, item_name, delete_after=None):
    """Ejecuta el flujo completo: Descarga LFTP -> Subida Transferit -> Guardado."""
    print(f"\n{C_BOLD}Procesando: {C_YELLOW}{item_name}{C_RESET}")
    
    # 1. Verificar espacio en disco
    disk = check_disk_space(cfg["local_dir"])
    if disk["free_gb"] < 15.0:
        print(f"{C_RED}{C_BOLD}[!] ADVERTENCIA: Espacio libre bajo: {disk['free_gb']:.1f} GB disponibles ({disk['used_pct']:.1f}% usado){C_RESET}")
    else:
        print(f"{C_DIM}[i] Espacio en disco: {disk['free_gb']:.1f} GB libres de {disk['total_gb']:.1f} GB{C_RESET}")
        
    # 2. Consultar ítem remoto
    item_info = check_remote_item(cfg, item_name)
    if not item_info["exists"]:
        print(f"{C_RED}[-] Error: No se encontro el archivo o carpeta '{item_name}' en el servidor remoto.{C_RESET}")
        send_discord_notify(cfg, item_name, status="ERROR")
        return False
        
    # 3. Descargar con LFTP
    ok, local_path = download_with_lftp(cfg, item_info)
    if not ok:
        send_discord_notify(cfg, item_name, status="ERROR")
        return False
        
    # 4. Subir a Transfer.it
    link = upload_to_transferit(cfg, local_path)
    if not link:
        send_discord_notify(cfg, item_name, status="ERROR")
        return False
        
    # 5. Guardar enlace
    save_link_info(cfg, item_info["name"], local_path, link)
    
    # 6. Limpieza si se solicita
    should_delete = delete_after if delete_after is not None else cfg.get("auto_delete_local", False)
    if should_delete:
        print(f"\n{C_YELLOW}[*] Eliminando archivo local descargado para ahorrar espacio...{C_RESET}")
        try:
            if os.path.isdir(local_path):
                shutil.rmtree(local_path)
            else:
                os.remove(local_path)
            print(f"{C_GREEN}[+] Archivo local eliminado. Enlace y registro conservados.{C_RESET}")
        except Exception as e:
            print(f"{C_RED}[-] No se pudo eliminar el archivo local: {e}{C_RESET}")
            
    print(f"\n{C_GREEN}{C_BOLD}" + "="*65)
    print(f"  [OK] COMPLETADO EXITOSAMENTE:")
    print(f"  Item: {item_info['name']}")
    print(f"  Link: {link}")
    print("="*65 + f"{C_RESET}\n")
    return True

def upload_local_only(cfg, local_target, delete_after=False):
    """Sube un archivo o directorio que ya existe localmente en /root/ultra."""
    target_path = Path(local_target).resolve()
    if not target_path.exists():
        target_path = Path(cfg["local_dir"]) / local_target
        if not target_path.exists():
            print(f"{C_RED}[-] No se encontro el archivo local: {local_target}{C_RESET}")
            return False
            
    name = target_path.name
    link = upload_to_transferit(cfg, target_path)
    if not link:
        send_discord_notify(cfg, name, status="ERROR")
        return False
        
    save_link_info(cfg, name, target_path, link)
    
    if delete_after:
        print(f"{C_YELLOW}[*] Eliminando archivo local tras subida exitosa...{C_RESET}")
        if os.path.isdir(target_path):
            shutil.rmtree(target_path)
        else:
            os.remove(target_path)
        print(f"{C_GREEN}[+] Limpieza local completada.{C_RESET}")
        
    print(f"\n{C_GREEN}{C_BOLD}" + "="*65)
    print(f"  [OK] SUBIDA LOCAL COMPLETADA:")
    print(f"  Item: {name}")
    print(f"  Link: {link}")
    print("="*65 + f"{C_RESET}\n")
    return True

DEFAULT_QUEUE_FILE = Path("/root/ultra/queue.txt")

def open_queue_file(queue_file: Path | str | None = None) -> Path:
    """Abre el archivo de cola en el editor para que el usuario escriba o pegue la lista."""
    q_path = Path(queue_file) if queue_file else DEFAULT_QUEUE_FILE
    if not q_path.exists():
        q_path.parent.mkdir(parents=True, exist_ok=True)
        template = """# ==============================================================================
# ULTRA TRANSFER: COLA DE TRANSFERENCIAS
# ==============================================================================
# Escribe o pega abajo los nombres de los archivos o carpetas del Seedbox a transferir.
# - Un archivo o carpeta por línea.
# - Las líneas que empiezan por '#' o vacías son ignoradas.
# - Guarda los cambios y cierra el editor para iniciar la transferencia automática.
# ==============================================================================

"""
        q_path.write_text(template, encoding="utf-8")
        
    editor = os.environ.get("EDITOR") or shutil.which("nano") or shutil.which("vim") or "vi"
    try:
        subprocess.run([editor, str(q_path)])
    except Exception as e:
        print(f"{C_RED}[-] Error al abrir editor ({editor}): {e}{C_RESET}")
        print(f"{C_YELLOW}[i] Puedes editar directamente el archivo: {q_path}{C_RESET}")
    return q_path

def read_queue_items(queue_path: Path) -> list[str]:
    """Lee las líneas del archivo de cola ignorando comentarios y líneas vacías."""
    if not queue_path.exists():
        return []
    lines = queue_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    items = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        items.append(line.strip("\"'"))
    return items

def mark_queue_item_completed(queue_path: Path, completed_item: str):
    """Marca la línea del ítem en queue.txt como completada para persistir el progreso."""
    if not queue_path.exists():
        return
    try:
        lines = queue_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        new_lines = []
        marked = False
        for line in lines:
            stripped = line.strip().strip("\"'")
            if not marked and stripped == completed_item:
                new_lines.append(f"# [COMPLETADO {datetime.now().strftime('%Y-%m-%d %H:%M')}] {line}")
                marked = True
            else:
                new_lines.append(line)
        queue_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    except Exception:
        pass

def run_queue_mode(cfg, queue_file_arg=None, auto_confirm=False):
    """
    Ejecuta el modo cola:
    1. Abre o carga el archivo de cola (.txt).
    2. Realiza match en el Seedbox remoto afirmando coincidencias.
    3. Descarga (LFTP) y sube (Transfer.it) de 1 en 1 (o máx 2 en VPS si el disco lo permite).
    4. Guarda enlace y elimina el archivo local para no saturar disco.
    5. Si falla la subida o enlace: reintenta hasta 3 veces; si no lo logra, fallback: para todo.
    """
    print(f"\n{C_CYAN}{C_BOLD}" + "="*65)
    print("                 MODO COLA (QUEUE MODE)")
    print("="*65 + f"{C_RESET}\n")

    if queue_file_arg:
        q_path = Path(queue_file_arg).resolve()
        if not q_path.exists():
            print(f"{C_RED}[-] El archivo de cola no existe: {q_path}{C_RESET}")
            return False
    else:
        print(f"{C_YELLOW}[*] Abriendo archivo de cola en el editor...{C_RESET}")
        print(f"{C_DIM}    Guarda y sal del editor para empezar la transferencia.{C_RESET}\n")
        q_path = open_queue_file()

    items = read_queue_items(q_path)
    if not items:
        print(f"{C_YELLOW}[i] La cola está vacía o no contiene elementos pendientes.{C_RESET}")
        return False

    print(f"\n{C_CYAN}{C_BOLD}[*] Verificando y haciendo match con el Seedbox (LFTP)...{C_RESET}")
    matched_items = []
    not_found_items = []

    for it in items:
        info = check_remote_item(cfg, it)
        if info["exists"]:
            matched_items.append(info)
            tipo = f"{C_BLUE}[DIR]{C_RESET}" if info["is_dir"] else f"{C_GREEN}[FILE]{C_RESET}"
            print(f"  {C_GREEN}[MATCH OK]{C_RESET}  {tipo} {info['name']}")
        else:
            not_found_items.append(it)
            print(f"  {C_RED}[NO ENCONTRADO]{C_RESET} {it}")

    print(f"\n{C_BOLD}Resumen de Verificación:{C_RESET}")
    print(f"  Total solicitados: {len(items)}")
    print(f"  {C_GREEN}Coincidencias encontradas: {len(matched_items)}{C_RESET}")
    if not_found_items:
        print(f"  {C_RED}No encontrados en el Seedbox: {len(not_found_items)}{C_RESET}")

    if not matched_items:
        print(f"\n{C_RED}[-] Ningún elemento de la cola fue encontrado en el Seedbox. Abortando.{C_RESET}")
        return False

    if not auto_confirm:
        confirm = input(f"\n{C_CYAN}¿Deseas iniciar la transferencia de estos {len(matched_items)} elementos? [S/n]: {C_RESET}").strip().lower()
        if confirm and confirm != "s":
            print("Cola cancelada por el usuario.")
            return False

    total_items = len(matched_items)
    results = []

    for idx, item_info in enumerate(matched_items, 1):
        item_name = item_info["name"]
        print(f"\n{C_CYAN}{C_BOLD}" + "="*65)
        print(f" [COLA {idx}/{total_items}] Procesando: {C_YELLOW}{item_name}{C_CYAN}")
        print("="*65 + f"{C_RESET}")

        item_success = False
        max_item_attempts = 3

        for attempt in range(1, max_item_attempts + 1):
            if attempt > 1:
                print(f"\n{C_YELLOW}[!] Intento {attempt}/{max_item_attempts} para: {item_name}{C_RESET}")

            # 1. Comprobación de disco en VPS
            disk = check_disk_space(cfg["local_dir"])
            if disk["free_gb"] < 6.0:
                print(f"{C_RED}[!] ADVERTENCIA CRÍTICA: Solo quedan {disk['free_gb']:.1f} GB libres en disco.{C_RESET}")
                print(f"{C_YELLOW}[*] Esperando 10s antes de descargar...{C_RESET}")
                time.sleep(10)

            # 2. Descargar con LFTP
            ok, local_path = download_with_lftp(cfg, item_info)
            if not ok:
                print(f"{C_RED}[-] Falló la descarga por LFTP (intento {attempt}/{max_item_attempts}){C_RESET}")
                time.sleep(3)
                continue

            # 3. Subir a Transfer.it (estrictamente 1 a la vez para no saturar conexión)
            link = upload_to_transferit(cfg, local_path)
            if link:
                save_link_info(cfg, item_name, local_path, link)

                # 4. En cuanto se tenga el link, se guarda y el archivo se borra para continuar con el loop
                print(f"\n{C_YELLOW}[*] Borrando copia local de '{item_name}' para liberar disco...{C_RESET}")
                try:
                    if os.path.isdir(local_path):
                        shutil.rmtree(local_path)
                    else:
                        os.remove(local_path)
                    print(f"{C_GREEN}[+] Archivo local eliminado. Espacio en disco recuperado.{C_RESET}")
                except Exception as e:
                    print(f"{C_RED}[-] Error al borrar archivo local: {e}{C_RESET}")

                mark_queue_item_completed(q_path, item_name)
                item_success = True
                results.append((item_name, link))
                break
            else:
                print(f"{C_RED}[-] No se obtuvo enlace de Transfer.it (intento {attempt}/{max_item_attempts}){C_RESET}")
                # Borrar copia local si falló para reintentar limpiamente
                try:
                    if local_path and local_path.exists():
                        if os.path.isdir(local_path): shutil.rmtree(local_path)
                        else: os.remove(local_path)
                except Exception:
                    pass
                time.sleep(4)

        # 5. Fallback si no lo logra en 3 intentos: PARAR TODO
        if not item_success:
            send_discord_notify(cfg, item_name, status="ERROR")
            print(f"\n{C_RED}{C_BOLD}" + "!"*65)
            print(f" [FALLBACK DE EMERGENCIA: COLA DETENIDA]")
            print(f" El elemento '{item_name}' falló tras {max_item_attempts} intentos.")
            print(f" Se detiene el proceso completo de la cola para evitar saturación o fallos.")
            print("!"*65 + f"{C_RESET}\n")
            return False

    print(f"\n{C_GREEN}{C_BOLD}" + "="*65)
    print(f"  [OK] COLA COMPLETADA EXITOSAMENTE ({len(results)}/{total_items} items procesados)")
    print("="*65 + f"{C_RESET}")
    for name, lnk in results:
        print(f"  • {name} -> {lnk}")
    print()
    return True

def show_recent_logs(num_lines=50):
    """Muestra las últimas líneas del archivo de logs /root/ultra/ultra.log."""
    if not LOG_FILE.exists():
        print(f"{C_YELLOW}[i] Aún no existe el archivo de logs ({LOG_FILE}){C_RESET}")
        return
    try:
        content = LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
        recent = content[-num_lines:] if len(content) > num_lines else content
        print(f"\n{C_CYAN}{C_BOLD}=== ÚLTIMOS {len(recent)} REGISTROS DE LOG ({LOG_FILE}) ==={C_RESET}")
        for line in recent:
            if "[ERROR]" in line:
                print(f"{C_RED}{line}{C_RESET}")
            elif "[WARNING]" in line:
                print(f"{C_YELLOW}{line}{C_RESET}")
            elif "exitosa" in line or "completado" in line or "OK" in line:
                print(f"{C_GREEN}{line}{C_RESET}")
            else:
                print(f"{C_DIM}{line}{C_RESET}")
        print(f"{C_CYAN}{C_BOLD}" + "="*65 + f"{C_RESET}\n")
    except Exception as e:
        print(f"{C_RED}[-] Error al leer logs: {e}{C_RESET}")

def interactive_mode(cfg):
    """Menú interactivo fácil para el usuario."""
    print_banner()
    while True:
        disk = check_disk_space(cfg["local_dir"])
        print(f"\n{C_BOLD}MENU PRINCIPAL{C_RESET} (Disco: {disk['free_gb']:.1f} GB libres)")
        print("  1. Buscar y transferir archivo o carpeta (LFTP -> Transfer.it)")
        print("  2. Ver lista de archivos recientes en el Seedbox (LFTP)")
        print("  3. Subir archivo ya descargado en /root/ultra a Transfer.it")
        print("  4. Modo Cola (abrir .txt, verificar match en Seedbox y procesar en lote)")
        print("  5. Ver historial de enlaces generados")
        print("  6. Ver estado de disco y archivos en /root/ultra")
        print("  7. Ver registro de actividad y errores (ultra.log)")
        print("  0. Salir")
        
        choice = input(f"\n{C_CYAN}Selecciona una opcion [0-7]: {C_RESET}").strip()
        
        if choice == "0":
            print("Hasta luego!")
            break
            
        elif choice == "1":
            q = input(f"{C_YELLOW}Ingresa nombre o palabra clave a buscar: {C_RESET}").strip()
            if not q:
                continue
            print(f"{C_DIM}[*] Consultando seedbox con LFTP...{C_RESET}")
            matches = list_remote_items(cfg, filter_query=q)
            if not matches:
                print(f"{C_RED}[-] No se encontraron coincidencias para '{q}'{C_RESET}")
                continue
                
            print(f"\n{C_BOLD}Resultados encontrados ({len(matches)}):{C_RESET}")
            for idx, it in enumerate(matches[:25], 1):
                tipo = f"{C_BLUE}[DIR]{C_RESET}" if it["is_dir"] else f"{C_GREEN}[FILE]{C_RESET}"
                print(f"  {idx:2d}. {tipo} {it['name']}")
                
            sel = input(f"\n{C_CYAN}Elige el numero del item (o Enter para cancelar): {C_RESET}").strip()
            if not sel or not sel.isdigit():
                continue
            idx_sel = int(sel) - 1
            if 0 <= idx_sel < len(matches):
                chosen = matches[idx_sel]
                del_opt = input(f"¿Deseas borrar el archivo local tras subirlo para ahorrar espacio? (s/N): ").strip().lower()
                delete_after = del_opt == "s"
                process_item(cfg, chosen["name"], delete_after=delete_after)
            else:
                print(f"{C_RED}[-] Seleccion fuera de rango.{C_RESET}")
                
        elif choice == "2":
            print(f"{C_DIM}[*] Obteniendo lista remota con LFTP...{C_RESET}")
            items = list_remote_items(cfg)
            print(f"\n{C_BOLD}Archivos y carpetas en Seedbox ({len(items)} items):{C_RESET}")
            for idx, it in enumerate(items[:40], 1):
                tipo = f"{C_BLUE}[DIR]{C_RESET}" if it["is_dir"] else f"{C_GREEN}[FILE]{C_RESET}"
                print(f"  {idx:2d}. {tipo} {it['name']}")
            if len(items) > 40:
                print(f"  ... y {len(items)-40} items mas. Usa la opcion 1 para buscar especificamente.")
                
        elif choice == "3":
            local_dir = Path(cfg["local_dir"])
            local_files = [f for f in local_dir.iterdir() if not f.name.endswith(".link.txt") and not f.name.endswith(".log") and not f.name.endswith(".json") and not f.name.endswith(".py")]
            if not local_files:
                print(f"{C_YELLOW}[i] No hay archivos en {local_dir}{C_RESET}")
                continue
            print(f"\n{C_BOLD}Archivos locales en {local_dir}:{C_RESET}")
            for idx, f in enumerate(local_files, 1):
                tipo = "[DIR]" if f.is_dir() else "[FILE]"
                size_mb = (sum(x.stat().st_size for x in f.glob('**/*') if x.is_file()) if f.is_dir() else f.stat().st_size) / (1024*1024)
                print(f"  {idx:2d}. {tipo} {f.name} ({size_mb:.1f} MB)")
                
            sel = input(f"\n{C_CYAN}Elige el numero a subir (o Enter para cancelar): {C_RESET}").strip()
            if not sel or not sel.isdigit():
                continue
            idx_sel = int(sel) - 1
            if 0 <= idx_sel < len(local_files):
                del_opt = input("¿Deseas borrar el archivo local tras subirlo? (s/N): ").strip().lower()
                upload_local_only(cfg, local_files[idx_sel], delete_after=(del_opt == "s"))
                
        elif choice == "4":
            run_queue_mode(cfg)
                
        elif choice == "5":
            master_links = Path(cfg["local_dir"]) / "links.txt"
            if master_links.exists():
                print(f"\n{C_BOLD}Historial de enlaces:{C_RESET}")
                print(master_links.read_text(encoding="utf-8"))
            else:
                print(f"{C_YELLOW}[i] Aún no se han generado enlaces.{C_RESET}")
                
        elif choice == "6":
            disk = check_disk_space(cfg["local_dir"])
            print(f"\n{C_BOLD}Estado del Disco:{C_RESET}")
            print(f"  Total: {disk['total_gb']:.1f} GB")
            print(f"  Usado: {disk['used_gb']:.1f} GB ({disk['used_pct']:.1f}%)")
            print(f"  Libre: {disk['free_gb']:.1f} GB")

        elif choice == "7":
            show_recent_logs()

README_TEXT = f"""{C_CYAN}{C_BOLD}================================================================================
                    ULTRA TRANSFER: GUÍA Y MANUAL DE USO
================================================================================{C_RESET}

{C_BOLD}DESCRIPCIÓN:{C_RESET}
  Herramienta de automatización integral para transferir contenido desde tu
  Seedbox remoto usando {C_YELLOW}LFTP{C_RESET} (pget multisegmentado y mirror) y subirlo a
  {C_GREEN}Transfer.it{C_RESET} (librería oficial con soporte para archivos grandes y carpetas).
  Genera enlaces directos permanentes y guarda archivos .link.txt e historial.

{C_BOLD}FLUJO DE TRABAJO AUTOMATIZADO:{C_RESET}
  1. {C_CYAN}[SEEDBOX]{C_RESET}  -> Descarga por SFTP vía LFTP (5 conexiones paralelas por archivo / mirror)
  2. {C_CYAN}[LOCAL]{C_RESET}    -> Archivo almacenado temporal o definitivamente en {C_BOLD}/root/ultra/{C_RESET}
  3. {C_CYAN}[TRANSFER]{C_RESET} -> Subida a Transfer.it (concurrencia 4, control de buffer y reintentos)
  4. {C_CYAN}[ENLACES]{C_RESET}  -> Genera <nombre>.link.txt (en /root/ultra/ y /root/ultra/history/), links.txt y JSON
  5. {C_CYAN}[LIMPIEZA]{C_RESET} -> Borrado automático del archivo local tras el éxito para no saturar disco

{C_BOLD}MODO COLA (PROCESAMIENTO EN LOTE POR LISTA .TXT):{C_RESET}
  Permite definir una lista de archivos o carpetas a transferir de forma automatizada:
  1. {C_YELLOW}Editor .txt:{C_RESET} Abre el archivo de cola en nano/editor (o recibe un .txt existente).
  2. {C_YELLOW}Match y verificación:{C_RESET} Comprueba si cada elemento existe en el Seedbox por LFTP
     y muestra el resumen de elementos coincidentes antes de iniciar.
  3. {C_YELLOW}Descarga y Subida ordenada:{C_RESET} Descarga de 1 en 1 (máx 2 si el disco del VPS lo
     permite) y sube a Transfer.it {C_BOLD}estrictamente de 1 en 1{C_RESET} para evitar saturación.
  4. {C_YELLOW}Guardado y Limpieza:{C_RESET} En cuanto se obtiene el enlace, se almacena en el historial
     y se elimina de inmediato el archivo local para recuperar espacio en disco.
  5. {C_YELLOW}Reintentos y Fallback:{C_RESET} Si un ítem falla al subir, se reintenta hasta 3 veces. Si
     tras 3 intentos consecutivos no lo logra, se activa el fallback y se DETIENE TODO.

{C_BOLD}COMANDOS RÁPIDOS:{C_RESET}
  {C_GREEN}ultra{C_RESET}                         Abre el menú interactivo (búsqueda, selector, cola, historial)
  {C_GREEN}ultra-transfer{C_RESET}                Comando equivalente a 'ultra'
  {C_GREEN}ultra -q [archivo.txt]{C_RESET}        Inicia el Modo Cola (abre editor si no se pasa archivo)
  {C_GREEN}ultra queue [archivo.txt]{C_RESET}     Comando alternativo para iniciar el Modo Cola
  {C_GREEN}ultra "nombre"{C_RESET}                Descarga con LFTP y sube a Transfer.it automáticamente
  {C_GREEN}ultra "nombre" -d{C_RESET}             Descarga, sube y elimina la copia local tras el éxito
  {C_GREEN}ultra -s "termino"{C_RESET}            Busca archivos o carpetas en el Seedbox por palabra clave
  {C_GREEN}ultra -l{C_RESET}                      Lista todos los contenidos remotos en el Seedbox
  {C_GREEN}ultra -u "ruta_local"{C_RESET}         Sube un archivo o carpeta que ya esté en /root/ultra/
  {C_GREEN}ultra --links{C_RESET}                 Muestra todos los enlaces generados hasta la fecha
  {C_GREEN}ultra --disk{C_RESET}                  Muestra el estado actual del espacio en disco
  {C_GREEN}ultra --readme{C_RESET}                Muestra esta guía completa en la terminal

{C_BOLD}EJEMPLOS DE USO PRÁCTICO:{C_RESET}
  # 1. Menú interactivo (incluye opción 4 para Modo Cola):
  $ ultra

  # 2. Iniciar el Modo Cola editando la lista:
  $ ultra -q
  # o también:
  $ ultra queue

  # 3. Procesar un archivo de lista existente:
  $ ultra -q mis_peliculas.txt

  # 4. Descargar y subir un archivo individual con borrado automático:
  $ ultra "A Face in the Crowd 1957 1080p BluRay FLAC HEVC.mkv" -d

  # 5. Descargar y subir una carpeta completa con borrado automático:
  $ ultra "Vagabond v01-37" -d

  # 6. Buscar archivos en el seedbox remoto:
  $ ultra -s "Criterion"

  # 7. Consultar los links generados:
  $ ultra --links

{C_BOLD}ARCHIVOS Y RUTAS DEL SISTEMA:{C_RESET}
  Configuración:       /root/.config/ultra/config.json
  Bookmark LFTP:       /root/.local/share/lftp/bookmarks (ultra)
  Carpeta de descargas: /root/ultra/
  Archivo de cola:     /root/ultra/queue.txt
  Carpeta historial:   /root/ultra/history/
  Ficheros de enlace:  /root/ultra/<nombre>.link.txt
  Historial maestro:   /root/ultra/links.txt y transfer_history.json
================================================================================
"""

HELP_EPILOG = """
================================================================================
                           EJEMPLOS Y GUÍA RÁPIDA
================================================================================
  ultra                                 Menú interactivo (búsqueda, cola, historial)
  ultra -q [archivo.txt]                Modo Cola: abre editor o procesa lista .txt
  ultra queue [archivo.txt]             Comando alternativo para modo cola
  ultra "archivo_o_carpeta"             Descarga por LFTP y sube a Transfer.it
  ultra "archivo_o_carpeta" -d          Descarga, sube y borra copia local al terminar
  ultra -s "termino"                    Busca en el Seedbox por término
  ultra -l                              Lista todo el contenido en el Seedbox
  ultra -u "ruta_local"                 Sube archivo/carpeta local a Transfer.it
  ultra --links                         Ver historial de enlaces generados
  ultra --disk                          Ver espacio libre en disco
  ultra --logs [N]                      Ver últimas N líneas de logs (/root/ultra/ultra.log)
  ultra --test-discord                  Enviar notificación de prueba a Discord
  ultra --readme                        Ver guía detallada completa

Configuración: /root/.config/ultra/config.json
Archivo cola:  /root/ultra/queue.txt
Registro logs: /root/ultra/ultra.log
Historial:     /root/ultra/history/ y /root/ultra/links.txt
================================================================================
"""

def main():
    parser = argparse.ArgumentParser(
        prog="ultra",
        description="ULTRA TRANSFER: Automatización LFTP (Seedbox) -> Transfer.it",
        epilog=HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("items", nargs="*", help="Nombre(s) del archivo o carpeta remota a transferir")
    parser.add_argument("-q", "--queue", nargs="?", const="", metavar="FILE", help="Modo Cola: abre editor o procesa lista desde archivo .txt")
    parser.add_argument("-l", "--list", nargs="?", const="", help="Listar archivos y carpetas remotos en el seedbox")
    parser.add_argument("-s", "--search", metavar="TEXT", help="Buscar archivos en el seedbox por palabra clave")
    parser.add_argument("-u", "--upload-local", metavar="PATH", help="Subir archivo o carpeta ya presente en /root/ultra a Transfer.it")
    parser.add_argument("-d", "--delete", action="store_true", help="Eliminar copia local descargada tras subida exitosa (ahorro de disco)")
    parser.add_argument("-k", "--keep", action="store_true", help="Conservar copia local (comportamiento por defecto)")
    parser.add_argument("--links", action="store_true", help="Mostrar historial de todos los enlaces generados")
    parser.add_argument("-y", "--yes", action="store_true", help="Confirmar automáticamente sin preguntar en modo cola")
    parser.add_argument("--disk", action="store_true", help="Mostrar estado del espacio en disco")
    parser.add_argument("--logs", nargs="?", const=50, type=int, metavar="N", help="Mostrar las últimas N líneas del log de actividad (default 50)")
    parser.add_argument("--test-discord", action="store_true", help="Enviar una notificación de prueba al webhook de Discord")
    parser.add_argument("--readme", action="store_true", help="Mostrar la guía y manual completo del usuario")
    parser.add_argument("-i", "--interactive", action="store_true", help="Iniciar menú interactivo en la terminal")
    
    args = parser.parse_args()
    cfg = load_config()
    
    if args.readme:
        print(README_TEXT)
        sys.exit(0)
        
    if args.disk:
        disk = check_disk_space(cfg["local_dir"])
        print(f"Espacio libre: {disk['free_gb']:.1f} GB / {disk['total_gb']:.1f} GB ({disk['used_pct']:.1f}% usado)")
        sys.exit(0)

    if args.logs is not None:
        show_recent_logs(args.logs)
        sys.exit(0)
        
    if args.test_discord:
        print("[*] Enviando notificación de prueba a Discord...")
        send_discord_notify(cfg, "Prueba.Ultra.Transfer.mkv", status="OK", link="https://transfer.it/t/ejemplo")
        sys.exit(0)
        
    if args.links:
        master_links = Path(cfg["local_dir"]) / "links.txt"
        if master_links.exists():
            print(master_links.read_text(encoding="utf-8"))
        else:
            print("No hay enlaces guardados.")
        sys.exit(0)

    # Manejar comando posicional 'ultra queue [archivo.txt]'
    if args.items and args.items[0] == "queue":
        queue_file = args.items[1] if len(args.items) > 1 else None
        run_queue_mode(cfg, queue_file_arg=queue_file, auto_confirm=args.yes)
        sys.exit(0)

    # Manejar opción flag -q / --queue [archivo.txt]
    if args.queue is not None:
        queue_file = args.queue if args.queue else None
        run_queue_mode(cfg, queue_file_arg=queue_file, auto_confirm=args.yes)
        sys.exit(0)
        
    if args.list is not None:
        items = list_remote_items(cfg, filter_query=args.list if args.list else None)
        print(f"Archivos y carpetas en Seedbox ({len(items)} items):")
        for it in items:
            tipo = "[DIR]" if it["is_dir"] else "[FILE]"
            print(f"  {tipo} {it['name']}")
        sys.exit(0)
        
    if args.search:
        items = list_remote_items(cfg, filter_query=args.search)
        print(f"Coincidencias para '{args.search}' ({len(items)}):")
        for it in items:
            tipo = "[DIR]" if it["is_dir"] else "[FILE]"
            print(f"  {tipo} {it['name']}")
        sys.exit(0)
        
    if args.upload_local:
        delete_after = args.delete if args.delete else False
        upload_local_only(cfg, args.upload_local, delete_after=delete_after)
        sys.exit(0)
        
    if args.items:
        delete_after = True if args.delete else False
        for it in args.items:
            process_item(cfg, it, delete_after=delete_after)
        sys.exit(0)
        
    # Si no se pasaron argumentos o se pasó -i, abrir interactivo
    interactive_mode(cfg)

if __name__ == "__main__":
    main()
