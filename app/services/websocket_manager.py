from fastapi import WebSocket
from typing  import Dict, List
import json

class WebSocketManager:
    """
    Maneja conexiones WebSocket activas por rol.
    vendedores: {vendedor_usuario_id: [WebSocket, ...]}
    """
    def __init__(self):
        self.vendedores: Dict[str, List[WebSocket]] = {}

    async def conectar_vendedor(
            self, websocket: WebSocket, usuario_id: str):
        await websocket.accept()
        if usuario_id not in self.vendedores:
            self.vendedores[usuario_id] = []
        self.vendedores[usuario_id].append(websocket)
        print(f"✅ [WS] Vendedor {usuario_id} conectado. "
              f"Total conexiones: {self._total()}")

    def desconectar_vendedor(
            self, websocket: WebSocket, usuario_id: str):
        if usuario_id in self.vendedores:
            self.vendedores[usuario_id] = [
                ws for ws in self.vendedores[usuario_id]
                if ws != websocket
            ]
            if not self.vendedores[usuario_id]:
                del self.vendedores[usuario_id]
        print(f"❌ [WS] Vendedor {usuario_id} desconectado. "
              f"Total conexiones: {self._total()}")

    async def notificar_todos_vendedores(self, mensaje: dict):
        """Envía un mensaje a TODOS los vendedores conectados."""
        datos    = json.dumps(mensaje)
        caidos   = []

        for usuario_id, conexiones in self.vendedores.items():
            for ws in conexiones:
                try:
                    await ws.send_text(datos)
                except Exception:
                    caidos.append((usuario_id, ws))

        # Limpiar conexiones caídas
        for usuario_id, ws in caidos:
            self.desconectar_vendedor(ws, usuario_id)

    async def notificar_vendedor(
            self, usuario_id: str, mensaje: dict):
        """Envía un mensaje a UN vendedor específico."""
        if usuario_id not in self.vendedores:
            return
        datos  = json.dumps(mensaje)
        caidos = []
        for ws in self.vendedores[usuario_id]:
            try:
                await ws.send_text(datos)
            except Exception:
                caidos.append(ws)
        for ws in caidos:
            self.desconectar_vendedor(ws, usuario_id)

    def _total(self) -> int:
        return sum(len(v) for v in self.vendedores.values())


# Instancia global — singleton
ws_manager = WebSocketManager()