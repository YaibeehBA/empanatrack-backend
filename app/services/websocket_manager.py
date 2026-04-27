from __future__ import annotations
import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect, status

logger = logging.getLogger("ws_manager")

# ── Configuración ────────────────────────────────────────────────────────────

MAX_SUBS_POR_SESION = 10        # máx clientes por pedido/sesión
HEARTBEAT_INTERVAL  = 20        # segundos entre pings del servidor
HEARTBEAT_TIMEOUT   = 35        # segundos sin pong → conexión zombie

# ── Helpers internos ─────────────────────────────────────────────────────────

def _now_iso() -> str:
    """Timestamp ISO 8601 UTC — reemplaza el None original."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


async def _safe_send(ws: WebSocket, datos: str) -> bool:
    """
    Envía texto al WebSocket. Devuelve False si la conexión está muerta.
    Centraliza el manejo de excepciones para no repetirlo en cada método.
    """
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
    """
    Envía `datos` a todos los WebSockets en `lista`.
    Devuelve la lista de conexiones VIVAS (sin modificar la original).

    Usa asyncio.gather para enviar en paralelo, no serializado.
    """
    resultados = await asyncio.gather(
        *[_safe_send(ws, datos) for ws in lista],
        return_exceptions=False,
    )
    vivos = [ws for ws, ok in zip(lista, resultados) if ok]
    muertos = len(lista) - len(vivos)
    if muertos:
        logger.info("Limpiados %d websockets muertos", muertos)
    return vivos


# ── Manager principal ────────────────────────────────────────────────────────

class WebSocketManager:
    """
    Gestiona conexiones WebSocket activas por rol.

    Estructura interna:
        _vendedores:   {usuario_id: WebSocket}         — un socket por vendedor
        _suscriptores: {sesion_key: [WebSocket, ...]}  — N clientes por sesión
                       (unifica clientes_tracking y clientes_mapa)

    sesion_key puede ser pedido_id (tracking tradicional)
    o sesion_id (mapa de ruta) — el caller decide el namespace.
    Para evitar colisiones de IDs, usa prefijos:
        "pedido:{pedido_id}"
        "sesion:{sesion_id}"
    """

    def __init__(self) -> None:
        # Un vendedor puede tener UNA conexión activa (la más reciente gana)
        self._vendedores: Dict[str, WebSocket] = {}

        # Múltiples clientes pueden escuchar la misma sesión
        self._suscriptores: Dict[str, List[WebSocket]] = defaultdict(list)

        # Tareas de heartbeat activas: {ws_id: Task}
        self._heartbeat_tasks: Dict[int, asyncio.Task] = {}

    # ── Vendedor ─────────────────────────────────────────────────────────────

    async def conectar_vendedor(
        self,
        websocket: WebSocket,
        usuario_id: str,
        token_valido: bool = True,   # validar ANTES de llamar este método
    ) -> bool:
        """
        Acepta la conexión del vendedor.
        IMPORTANTE: validar el token antes de llamar — si token_valido=False
        rechaza con 4001 sin accept(), ahorrando recursos.
        """
        if not token_valido:
            await websocket.close(code=4001, reason="token_invalido")
            return False

        await websocket.accept()

        # Si había una conexión anterior del mismo vendedor, la cerramos limpio
        vieja = self._vendedores.get(usuario_id)
        if vieja:
            logger.warning("Vendedor %s reconectó — cerrando conexión anterior", usuario_id)
            try:
                await vieja.close(code=4000, reason="reconexion")
            except Exception:
                pass  # ya estaba muerta

        self._vendedores[usuario_id] = websocket
        self._iniciar_heartbeat(websocket, usuario_id, rol="vendedor")

        logger.info(
            "Vendedor %s conectado | total vendedores: %d",
            usuario_id, len(self._vendedores),
        )
        return True

    def desconectar_vendedor(self, websocket: WebSocket, usuario_id: str) -> None:
        ws_actual = self._vendedores.get(usuario_id)
        # Solo eliminamos si ES el mismo socket (evita borrar una reconexión)
        if ws_actual is websocket:
            del self._vendedores[usuario_id]
        self._cancelar_heartbeat(websocket)
        logger.info("Vendedor %s desconectado | total vendedores: %d",
                    usuario_id, len(self._vendedores))

    async def notificar_vendedor(self, usuario_id: str, mensaje: dict) -> bool:
        """Envía un mensaje a un vendedor específico. Devuelve False si no está conectado."""
        ws = self._vendedores.get(usuario_id)
        if not ws:
            return False
        ok = await _safe_send(ws, json.dumps(mensaje))
        if not ok:
            self.desconectar_vendedor(ws, usuario_id)
        return ok

    async def notificar_todos_vendedores(self, mensaje: dict) -> None:
        """Envía un mensaje a TODOS los vendedores. Limpia muertos en paralelo."""
        if not self._vendedores:
            return
        datos = json.dumps(mensaje)
        # Snapshot para no iterar sobre el dict mientras se modifica
        items = list(self._vendedores.items())
        resultados = await asyncio.gather(
            *[_safe_send(ws, datos) for _, ws in items],
        )
        for (uid, ws), ok in zip(items, resultados):
            if not ok:
                self.desconectar_vendedor(ws, uid)

    # ── Clientes (tracking + mapa unificados) ────────────────────────────────

    async def conectar_suscriptor(
        self,
        websocket: WebSocket,
        sesion_key: str,
        usuario_id: str,
        token_valido: bool = True,
    ) -> bool:
        """
        Conecta un cliente a una sesión (pedido o mapa).
        sesion_key debe tener prefijo: "pedido:XXX" o "sesion:XXX"
        """
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
            pass  # ya había sido removido (ej: limpieza de zombies)

        if not lista:
            del self._suscriptores[sesion_key]

        self._cancelar_heartbeat(websocket)

    # ── Broadcast de posición ─────────────────────────────────────────────────

    async def broadcast_ubicacion(
        self,
        sesion_key: str,
        vendedor_id: str,
        lat: float,
        lng: float,
        extra: Optional[dict] = None,
    ) -> int:
        """
        Envía la ubicación del vendedor a todos los suscriptores de una sesión.
        Devuelve el número de clientes que recibieron el mensaje.

        Args:
            sesion_key: "pedido:XXX" o "sesion:XXX"
            vendedor_id: ID del vendedor
            lat/lng: coordenadas
            extra: campos adicionales (ej: {"estado": "en_camino"})
        """
        lista = self._suscriptores.get(sesion_key)
        if not lista:
            return 0

        payload: dict = {
            "tipo": "ubicacion_vendedor",
            "vendedor_id": vendedor_id,
            "sesion_id": sesion_key,
            "lat": lat,
            "lng": lng,
            "timestamp": _now_iso(),   # ← corregido: era None en el original
        }
        if extra:
            payload.update(extra)

        datos = json.dumps(payload)
        # Envío en paralelo — no serializado
        vivos = await _broadcast_a_lista(lista, datos)

        # Actualizar lista sin crear nueva referencia (los demás métodos la usan)
        lista[:] = vivos

        if not lista:
            del self._suscriptores[sesion_key]

        return len(vivos)

    async def broadcast_masivo(self, ubicaciones: List[dict]) -> dict:
        """
        Envía múltiples posiciones a sus sesiones correspondientes.
        Versión O(n) — agrupa por sesión y hace un solo json.dumps por sesión.

        Args:
            ubicaciones: lista de dicts con keys:
                         sesion_key, vendedor_id, lat, lng, [extra]

        Returns:
            {"enviados": int, "sesiones": int}
        """
        # 1. Agrupar por sesión (una sola pasada)
        por_sesion: Dict[str, dict] = {}
        for ubic in ubicaciones:
            key = ubic["sesion_key"]
            # Si hay múltiples posiciones para la misma sesión en el batch,
            # solo mandamos la última (la más reciente)
            por_sesion[key] = ubic

        # 2. Preparar payloads únicos por sesión
        tareas = []
        for key, ubic in por_sesion.items():
            if key in self._suscriptores:
                tareas.append(
                    self.broadcast_ubicacion(
                        sesion_key=key,
                        vendedor_id=ubic["vendedor_id"],
                        lat=ubic["lat"],
                        lng=ubic["lng"],
                        extra=ubic.get("extra"),
                    )
                )

        resultados = await asyncio.gather(*tareas) if tareas else []

        return {
            "enviados": sum(resultados),
            "sesiones": len(tareas),
        }

    # ── Heartbeat (detecta zombies sin esperar al cliente) ────────────────────

    def _iniciar_heartbeat(
        self, websocket: WebSocket, user_id: str, rol: str
    ) -> None:
        """Lanza una tarea asyncio que hace ping periódico al cliente."""
        task = asyncio.create_task(
            self._heartbeat_loop(websocket, user_id, rol),
            name=f"hb-{rol}-{user_id}",
        )
        self._heartbeat_tasks[id(websocket)] = task

    def _cancelar_heartbeat(self, websocket: WebSocket) -> None:
        task = self._heartbeat_tasks.pop(id(websocket), None)
        if task and not task.done():
            task.cancel()

    async def _heartbeat_loop(
        self, websocket: WebSocket, user_id: str, rol: str
    ) -> None:
        """
        Envía ping cada HEARTBEAT_INTERVAL segundos.
        Si no recibe pong en HEARTBEAT_TIMEOUT, cierra la conexión zombie.
        
        NOTA: el cliente Flutter ya hace ping cada 20s — este heartbeat es
        una capa adicional del SERVIDOR para detectar muertes silenciosas
        (ej: app crasheada, red caída sin TCP RST).
        """
        ultimo_pong = time.monotonic()

        async def esperar_pong() -> None:
            nonlocal ultimo_pong
            try:
                async for msg in websocket.iter_text():
                    try:
                        data = json.loads(msg)
                        if data.get("tipo") == "pong":
                            ultimo_pong = time.monotonic()
                    except json.JSONDecodeError:
                        pass
            except Exception:
                pass  # WebSocket cerrado

        # Lanzar listener de pongs en background
        pong_task = asyncio.create_task(esperar_pong())

        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)

                # ¿Cuánto tiempo sin pong?
                silencio = time.monotonic() - ultimo_pong
                if silencio > HEARTBEAT_TIMEOUT:
                    logger.warning(
                        "Zombie detectado: %s %s (sin pong %.0fs)",
                        rol, user_id, silencio,
                    )
                    await websocket.close(code=4009, reason="heartbeat_timeout")
                    break

                # Enviar ping del servidor
                try:
                    await websocket.send_text(json.dumps({"tipo": "ping"}))
                except Exception:
                    break  # ya está muerto

        except asyncio.CancelledError:
            pass
        finally:
            pong_task.cancel()

    # ── Utilidades ────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Snapshot del estado actual — útil para /healthz o métricas."""
        return {
            "vendedores_activos": len(self._vendedores),
            "sesiones_activas": len(self._suscriptores),
            "total_suscriptores": sum(len(v) for v in self._suscriptores.values()),
            "heartbeats_activos": len(self._heartbeat_tasks),
        }


# Instancia global — singleton
ws_manager = WebSocketManager()