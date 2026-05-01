from __future__ import annotations
import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect, status

logger = logging.getLogger("ws_manager")

# ── Configuración ─────────────────────────────────────────────────────────────
MAX_SUBS_POR_SESION = 10
HEARTBEAT_INTERVAL  = 20        # segundos entre pings del servidor
HEARTBEAT_TIMEOUT   = 35        # segundos sin pong → conexión zombie


# ── Helpers internos ──────────────────────────────────────────────────────────

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


async def _safe_send(ws: WebSocket, datos: str) -> bool:
    try:
        await ws.send_text(datos)
        return True
    except Exception as exc:
        logger.debug("Conexión muerta al enviar: %s", exc)
        return False


async def _broadcast_a_lista(
    lista: List[WebSocket],
    datos: str,
) -> List[WebSocket]:
    resultados = await asyncio.gather(
        *[_safe_send(ws, datos) for ws in lista],
        return_exceptions=False,
    )
    vivos   = [ws for ws, ok in zip(lista, resultados) if ok]
    muertos = len(lista) - len(vivos)
    if muertos:
        logger.info("Limpiados %d websockets muertos", muertos)
    return vivos


# ── Manager principal ─────────────────────────────────────────────────────────

class WebSocketManager:

    def __init__(self) -> None:
        self._vendedores:      Dict[str, WebSocket]        = {}
        self._suscriptores:    Dict[str, List[WebSocket]]  = defaultdict(list)
        self._heartbeat_tasks: Dict[int, asyncio.Task]     = {}
        # ── NUEVO: timestamps del último pong recibido por socket ──
        self._ultimo_pong:     Dict[int, float]            = {}

    # ── Vendedor ──────────────────────────────────────────────────────────────

    async def conectar_vendedor(
        self,
        websocket:    WebSocket,
        usuario_id:   str,
        token_valido: bool = True,
    ) -> bool:
        if not token_valido:
            await websocket.close(code=4001, reason="token_invalido")
            return False

        await websocket.accept()

        vieja = self._vendedores.get(usuario_id)
        if vieja:
            logger.warning(
                "Vendedor %s reconectó — cerrando conexión anterior", usuario_id)
            try:
                await vieja.close(code=4000, reason="reconexion")
            except Exception:
                pass

        self._vendedores[usuario_id] = websocket
        self._iniciar_heartbeat(websocket, usuario_id, rol="vendedor")

        logger.info(
            "Vendedor %s conectado | total vendedores: %d",
            usuario_id, len(self._vendedores),
        )
        return True

    def desconectar_vendedor(self, websocket: WebSocket, usuario_id: str) -> None:
        ws_actual = self._vendedores.get(usuario_id)
        if ws_actual is websocket:
            del self._vendedores[usuario_id]
        self._cancelar_heartbeat(websocket)
        logger.info(
            "Vendedor %s desconectado | total vendedores: %d",
            usuario_id, len(self._vendedores),
        )

    async def notificar_vendedor(self, usuario_id: str, mensaje: dict) -> bool:
        ws = self._vendedores.get(usuario_id)
        if not ws:
            return False
        ok = await _safe_send(ws, json.dumps(mensaje))
        if not ok:
            self.desconectar_vendedor(ws, usuario_id)
        return ok

    async def notificar_todos_vendedores(self, mensaje: dict) -> None:
        if not self._vendedores:
            return
        datos = json.dumps(mensaje)
        items = list(self._vendedores.items())
        resultados = await asyncio.gather(
            *[_safe_send(ws, datos) for _, ws in items],
        )
        for (uid, ws), ok in zip(items, resultados):
            if not ok:
                self.desconectar_vendedor(ws, uid)

    # ── Clientes ──────────────────────────────────────────────────────────────

    async def conectar_suscriptor(
        self,
        websocket:    WebSocket,
        sesion_key:   str,
        usuario_id:   str,
        token_valido: bool = True,
    ) -> bool:
        if not token_valido:
            await websocket.close(code=4001, reason="token_invalido")
            return False

        lista = self._suscriptores[sesion_key]
        if len(lista) >= MAX_SUBS_POR_SESION:
            await websocket.close(code=4008, reason="limite_conexiones_alcanzado")
            logger.warning(
                "Sesión %s rechazó conexión: límite %d alcanzado",
                sesion_key, MAX_SUBS_POR_SESION,
            )
            return False

        await websocket.accept()
        lista.append(websocket)
        self._iniciar_heartbeat(websocket, usuario_id, rol="cliente")

        logger.info(
            "Cliente %s suscrito a %s | suscriptores: %d",
            usuario_id, sesion_key, len(lista),
        )
        return True

    def desconectar_suscriptor(self, websocket: WebSocket, sesion_key: str) -> None:
        lista = self._suscriptores.get(sesion_key)
        if not lista:
            return
        try:
            lista.remove(websocket)
        except ValueError:
            pass
        if not lista:
            del self._suscriptores[sesion_key]
        self._cancelar_heartbeat(websocket)

    # ── Broadcast ─────────────────────────────────────────────────────────────

    async def broadcast_ubicacion(
        self,
        sesion_key:  str,
        vendedor_id: str,
        lat:         float,
        lng:         float,
        extra:       Optional[dict] = None,
    ) -> int:
        lista = self._suscriptores.get(sesion_key)
        if not lista:
            return 0

        payload: dict = {
            "tipo":       "ubicacion_vendedor",
            "vendedor_id": vendedor_id,
            "sesion_id":  sesion_key,
            "lat":        lat,
            "lng":        lng,
            "timestamp":  _now_iso(),
        }
        if extra:
            payload.update(extra)

        datos = json.dumps(payload)
        vivos = await _broadcast_a_lista(lista, datos)
        lista[:] = vivos

        if not lista:
            del self._suscriptores[sesion_key]

        return len(vivos)

    async def broadcast_masivo(self, ubicaciones: List[dict]) -> dict:
        por_sesion: Dict[str, dict] = {}
        for ubic in ubicaciones:
            key = ubic["sesion_key"]
            por_sesion[key] = ubic

        tareas = []
        for key, ubic in por_sesion.items():
            if key in self._suscriptores:
                tareas.append(
                    self.broadcast_ubicacion(
                        sesion_key  = key,
                        vendedor_id = ubic["vendedor_id"],
                        lat         = ubic["lat"],
                        lng         = ubic["lng"],
                        extra       = ubic.get("extra"),
                    )
                )

        resultados = await asyncio.gather(*tareas) if tareas else []
        return {
            "enviados": sum(resultados),
            "sesiones": len(tareas),
        }

    # ── Pong externo — el ROUTER lo llama al recibir {"tipo":"pong"} ──────────

    def registrar_pong(self, websocket: WebSocket) -> None:
        """
        El router debe llamar esto cuando recibe tipo='pong' del cliente.
        Así el heartbeat sabe que la conexión sigue viva SIN consumir
        el stream del WebSocket desde aquí.
        """
        self._ultimo_pong[id(websocket)] = time.monotonic()

    # ── Heartbeat ─────────────────────────────────────────────────────────────

    def _iniciar_heartbeat(
        self, websocket: WebSocket, user_id: str, rol: str
    ) -> None:
        # Registrar timestamp inicial
        self._ultimo_pong[id(websocket)] = time.monotonic()
        task = asyncio.create_task(
            self._heartbeat_loop(websocket, user_id, rol),
            name=f"hb-{rol}-{user_id}",
        )
        self._heartbeat_tasks[id(websocket)] = task

    def _cancelar_heartbeat(self, websocket: WebSocket) -> None:
        task = self._heartbeat_tasks.pop(id(websocket), None)
        if task and not task.done():
            task.cancel()
        # Limpiar timestamp
        self._ultimo_pong.pop(id(websocket), None)

    async def _heartbeat_loop(
        self, websocket: WebSocket, user_id: str, rol: str
    ) -> None:
        """
        Solo envía pings periódicos.
        NO lee del socket — el router es el único que llama receive_text().
        Los pongs se registran desde el router vía registrar_pong().
        """
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)

                silencio = time.monotonic() - self._ultimo_pong.get(
                    id(websocket), time.monotonic()
                )
                if silencio > HEARTBEAT_TIMEOUT:
                    logger.warning(
                        "Zombie detectado: %s %s (sin pong %.0fs)",
                        rol, user_id, silencio,
                    )
                    try:
                        await websocket.close(
                            code=4009, reason="heartbeat_timeout")
                    except Exception:
                        pass
                    break

                # Solo enviar ping — no leer aquí
                try:
                    await websocket.send_text(
                        json.dumps({"tipo": "ping"}))
                except Exception:
                    break  # socket muerto

        except asyncio.CancelledError:
            pass

    # ── Utilidades ────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "vendedores_activos":  len(self._vendedores),
            "sesiones_activas":    len(self._suscriptores),
            "total_suscriptores":  sum(
                len(v) for v in self._suscriptores.values()),
            "heartbeats_activos":  len(self._heartbeat_tasks),
        }


# Instancia global
ws_manager = WebSocketManager()