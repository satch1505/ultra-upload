# transferit — fork personal (v0.1.0rc1 + fixes para archivos >30 GB)

Fork personal del paquete `transferit` (MEGA/transfer.it WebSocket uploader)
con una serie de correcciones para subidas muy grandes (>30 GiB). Basado en
la versión 0.1.0rc1 instalada vía pipx.

## Contenido

- `transferit/`           — librería (fork completo)
- `transferit_cli/`       — capa CLI (fork completo)
- `transferit_py-0.1.0rc1.dist-info/` — metadatos del paquete
- `restore.sh`            — script para re-aplicar el fork tras un reinstall
- `README.md`             — este archivo

## Cambios respecto al original

Todos están en `transferit/_upload.py` salvo el nº 1, que también toca
`transferit_cli/_upload.py` (el default del flag `-c`).

1. Concurrencia por defecto: 8 -> 4
   (también `transferit_cli/_upload.py`, default del CLI alineado a 4)
2. Límite de buffer: 1_500_000 -> 5_000_000 (WS_BUFFER_LIMIT)
3. Nueva excepción `_WsDisconnect` + fast-fail cuando el servidor cierra el
   socket antes de enviar COMPLETE. Antes: el `async for` de websockets 17
   termina EN SILENCIO en un close normal y el código se colgaba 120s para
   acabar con "upload ended without completion token".
4. Espera de COMPLETE con `asyncio.wait(FIRST_COMPLETED)` en vez de
   `gather(done.wait(), recv_task)`: el servidor mantiene las conexiones del
   pool abiertas y en silencio, así que esperar al recv_task de cada worker
   colgaba para siempre (falso timeout en multi-conexión).
5. Timeout de COMPLETE por INACTIVIDAD, no fijo: el reloj se resetea con cada
   mensaje del servidor (ACKs/COMPLETE). Antes era un 120s fijo desde que cada
   worker acababa su parte -> el primer worker que terminaba reventaba mientras
   los demás seguían subiendo (fallo al 85% en archivos de 43 GiB).
6. Pacing por ACKs (WS_ACK_WINDOW = 1_500_000 por conexión): el cliente ya no
   inunda al servidor con chunks sin confirmar; la ventana del servidor no se
   llena, los ACKs no se secan. Evita el error "no server activity for 120s".

Nuevo flag de depuración: `TRANSFERIT_DEBUG=1` imprime el flujo de mensajes
WebSocket a stderr.

## Cómo restaurar tras un reinstall / actualización

Un `pipx reinstall transferit` (o un upgrade) SOBRESCRIBE estos cambios en el
site-packages del venv. Para recuperarlos:

```bash
unzip transferit-fork.zip
cd transferit-fork
bash restore.sh
```

El script localiza el venv de pipx (transferit-py), sustituye los paquetes por
el fork y verifica con un sanity-check que imprime "OK: fork active".

## Verificación rápida

```bash
/root/.local/share/pipx/venvs/transferit-py/bin/python -c \
  "from transferit._upload import DEFAULT_CONCURRENCY, WS_BUFFER_LIMIT, WS_ACK_WINDOW; print(DEFAULT_CONCURRENCY, WS_BUFFER_LIMIT, WS_ACK_WINDOW)"
```

Esperado: `4 5000000 1500000`.

## Nota

Si el paquete upstream cambia `_upload.py` de forma incompatible, restaurar
este fork sobrescribirá esos cambios (es la naturaleza de un fork). Revisa
entonces el diff antes de aplicar.
