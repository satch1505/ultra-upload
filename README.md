# Ultra Transfer: Automatización LFTP (Seedbox) -> Transfer.it

Herramienta integral de automatización para descargar contenido desde el Seedbox remoto usando **LFTP** (con descargas multisegmentadas y soporte para carpetas) y subirlo automáticamente a **Transfer.it** utilizando la librería oficial (`transferit-py`), generando los enlaces de descarga y gestionando el espacio en disco.

---

## 🚀 Comandos Rápidos

| Comando | Descripción |
| :--- | :--- |
| `ultra` o `ultra-transfer` | Abre el menú interactivo (búsqueda, selector, cola y gestión). |
| `ultra -q` o `ultra queue` | **Modo Cola**: abre el editor para escribir/pegar la lista y procesarla. |
| `ultra -q archivo.txt` | **Modo Cola**: procesa directamente la lista especificada en el archivo `.txt`. |
| `ultra "nombre"` | Descarga por LFTP y sube a Transfer.it automáticamente. |
| `ultra "nombre" -d` | Descarga, sube y **elimina la copia local** para ahorrar disco. |
| `ultra -s "palabra"` | Busca archivos o carpetas en el Seedbox por palabra clave. |
| `ultra -l` | Lista todos los archivos y carpetas remotos del Seedbox. |
| `ultra -u "ruta"` | Sube un archivo o carpeta que ya esté en `/root/ultra/`. |
| `ultra --links` | Muestra el historial de todos los enlaces generados. |
| `ultra --disk` | Muestra el espacio libre y porcentaje de uso del disco. |
| `ultra --logs [N]` | Muestra las últimas N líneas del registro de logs (`ultra.log`). |
| `ultra --test-discord` | Envía una notificación de prueba al webhook de Discord. |
| `ultra --readme` | Muestra la guía y manual completo directamente en la terminal. |
| `ultra --help` | Muestra la ayuda y opciones disponibles. |

---

## 📋 Modo Cola (Queue Mode)

El Modo Cola permite automatizar el procesamiento por lotes de una lista de archivos y carpetas:

1. **Entrada de lista (.txt)**: Al ejecutar `ultra -q` (o seleccionar la opción 4 en el menú), se abre el editor `nano` con una plantilla en `/root/ultra/queue.txt`. Puedes pegar los nombres tal como aparecen en el seedbox (uno por línea). También puedes pasar tu propio archivo con `ultra -q mi_lista.txt`.
2. **Verificación y Match previo**: Antes de descargar nada, el script consulta el seedbox por LFTP y comprueba cada ítem de la lista. Muestra claramente:
   - `[MATCH OK] [DIR] / [FILE]` para los encontrados.
   - `[NO ENCONTRADO]` para los ausentes.
   - Presenta un resumen del total y solicita confirmación antes de arrancar.
3. **Descarga y Subida ordenada**:
   - **Descargas al VPS**: De 1 en 1 (máx 2 si el disco lo permite) para proteger los ~21 GB disponibles en el VPS y evitar desbordar el disco.
   - **Subidas a Transfer.it**: Estrictamente **de 1 en 1** para no saturar el ancho de banda ni los sockets.
4. **Guardado inmediato y Limpieza de disco**:
   - En cuanto se obtiene el enlace de Transfer.it (`https://transfer.it/t/...`), se guarda en:
     - `/root/ultra/<nombre>.link.txt`
     - `/root/ultra/history/<nombre>.link.txt`
     - `/root/ultra/links.txt` (registro cronológico general)
     - `/root/ultra/transfer_history.json` (registro estructurado JSON)
   - El archivo o carpeta descargada en el VPS **se elimina inmediatamente** para liberar espacio antes del siguiente archivo.
   - La línea del ítem en `queue.txt` se marca como completada (`# [COMPLETADO ...]`).
5. **Reintentos y Fallback de emergencia**:
   - Si la subida o enlace falla, el sistema reintenta hasta **3 veces**.
   - Si tras 3 intentos no tiene éxito, se activa el **Fallback de emergencia**: **DETIENE TODO** el proceso de la cola para evitar saturación o fallos encadenados.

## 🔔 Notificaciones Discord

El sistema envía alertas minimalistas en tiempo real mediante el webhook configurado:
* **Subida exitosa**:
  ```text
  nombre_archivo: OK
  https://transfer.it/t/...
  ```
* **Fallo o Error**:
  ```text
  nombre_archivo: ERROR
  ```

Para verificar la conexión con Discord en cualquier momento ejecuta:
```bash
ultra --test-discord
```

---

## 🔄 Flujo de Trabajo Automatizado

1. **Consulta Remota**: El script consulta el Seedbox por SFTP usando el bookmark configurado `ultra`.
2. **Descarga con LFTP**:
   - **Archivos individuales**: Se descargan con `pget -c -n 5` (5 conexiones simultáneas con reanudación).
   - **Carpetas completas**: Se descargan recursivamente con `mirror -c --use-pget-n=5`.
3. **Subida a Transfer.it**:
   - Utiliza `/root/.local/bin/transferit` con concurrencia 4.
   - Cuenta con soporte para archivos grandes (>30 GB), rotación de pools de subida y hasta 5 reintentos automáticos por archivo si hay microcortes de red.
4. **Generación de Enlaces**:
   - Genera el archivo `<nombre>.link.txt` en `/root/ultra/`, `/root/ultra/history/` y `/transfer-it/`.
   - Registra el enlace en `links.txt` y en `transfer_history.json`.
5. **Limpieza Opcional / Automática**:
   - En cola o con `-d`: elimina la copia local tras la subida exitosa para no llenar el disco, conservando intactos los enlaces.

---

## 💡 Ejemplos Prácticos

### 1. Iniciar Modo Cola
```bash
# Abre el editor para escribir o pegar la lista:
ultra -q
# O usando el alias directo:
ultra queue
# O indicando un archivo .txt con la lista:
ultra -q /root/ultra/lista_series.txt
```

### 2. Modo Interactivo (Menú visual)
```bash
ultra
```
Aparecerá un menú con opciones para buscar archivos remotos, listarlos, subirlos, activar la cola o consultar enlaces.

### 3. Descargar y subir un archivo individual con borrado automático
```bash
ultra "A Face in the Crowd 1957 1080p BluRay FLAC HEVC.mkv" -d
```

### 4. Descargar y subir una carpeta completa ahorrando disco
```bash
ultra "Vagabond v01-37" -d
```

### 5. Buscar contenido en el Seedbox
```bash
ultra -s "Criterion"
ultra -s "1080p"
```

### 6. Subir un archivo ya descargado en `/root/ultra/`
```bash
ultra -u "Crash.1996.Criterion.1080p.BluRay.x264-OFT.mkv"
```

### 7. Consultar registros de actividad y errores
```bash
# Ver últimas 50 líneas de logs:
ultra --logs

# Ver últimas 100 líneas:
ultra --logs 100
```

---

## 📁 Archivos y Rutas Clave

* **Script principal**: `/root/ultra/ultra_transfer.py` (comandos `ultra` y `ultra-transfer`).
* **Archivo de Cola**: `/root/ultra/queue.txt`.
* **Registro de Logs**: `/root/ultra/ultra.log`.
* **Carpeta de Historial**: `/root/ultra/history/`.
* **Configuración**: `/root/.config/ultra/config.json`.
* **Bookmark LFTP**: `/root/.local/share/lftp/bookmarks` (conexión `ultra`).
* **Historial maestro de enlaces**: `/root/ultra/links.txt` y `/root/ultra/transfer_history.json`.
* **Fork de Transfer.it corregido**: `/transferit-fork.zip`.
