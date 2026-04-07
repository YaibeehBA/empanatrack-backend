from fastapi  import APIRouter, WebSocket, WebSocketDisconnect, Query
from app.services.websocket_manager import ws_manager
from app.database import get_db
from app.models.usuario  import Usuario
from app.models.vendedor import Vendedor
from app.core.security import decodificar_token  
import json

router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws/vendedor")
async def websocket_vendedor(
    websocket: WebSocket,
    token:     str = Query(...),
):
    """
    WebSocket para vendedores.
    El cliente se conecta con: ws://host/ws/vendedor?token=JWT
    """
    # Verificar token
    try:
        payload     = decodificar_token(token)
        usuario_id  = payload.get("sub")
        if not usuario_id:
            await websocket.close(code=1008)
            return

        # Verificar que sea vendedor o admin
        db       = next(get_db())
        usuario  = db.query(Usuario).filter(
            Usuario.id == usuario_id
        ).first()
        db.close()

        if not usuario or usuario.rol not in ("vendedor", "administrador"):
            await websocket.close(code=1008)
            return

    except Exception:
        await websocket.close(code=1008)
        return

    # Conectar
    await ws_manager.conectar_vendedor(websocket, str(usuario_id))

    try:
        # Mantener conexión viva — esperar mensajes del cliente
        # (ping/pong para mantener activa la conexión)
        while True:
            data = await websocket.receive_text()
            # El cliente puede enviar {"tipo": "ping"}
            try:
                msg = json.loads(data)
                if msg.get("tipo") == "ping":
                    await websocket.send_text(
                        json.dumps({"tipo": "pong"}))
            except Exception:
                pass

    except WebSocketDisconnect:
        ws_manager.desconectar_vendedor(websocket, str(usuario_id))

@router.websocket("/ws/tracking/{pedido_id}")
async def websocket_tracking_cliente(
    websocket: WebSocket,
    pedido_id: str,
    token:     str = Query(...),
):
    """
    WebSocket para clientes que siguen su pedido en tiempo real.
    El vendedor envía su posición y el cliente la recibe aquí.
    """
    try:
        payload    = decodificar_token(token)
        usuario_id = payload.get("sub")
        if not usuario_id:
            await websocket.close(code=1008)
            return
        db      = next(get_db())
        usuario = db.query(Usuario).filter(
            Usuario.id == usuario_id
        ).first()
        db.close()
        if not usuario:
            await websocket.close(code=1008)
            return
    except Exception:
        await websocket.close(code=1008)
        return

    await ws_manager.conectar_cliente_tracking(websocket, pedido_id)

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("tipo") == "ping":
                    await websocket.send_text(
                        json.dumps({"tipo": "pong"}))
            except Exception:
                pass
    except WebSocketDisconnect:
        ws_manager.desconectar_cliente_tracking(websocket, pedido_id)


@router.websocket("/ws/vendedor-ubicacion")
async def websocket_vendedor_ubicacion(
    websocket: WebSocket,
    token:     str = Query(...),
):
    """
    El vendedor envía su ubicación aquí.
    El backend la reenvía a los clientes que tienen pedidos activos.
    """
    try:
        payload    = decodificar_token(token)
        usuario_id = payload.get("sub")
        if not usuario_id:
            await websocket.close(code=1008)
            return
        db       = next(get_db())
        usuario  = db.query(Usuario).filter(
            Usuario.id == usuario_id
        ).first()
        db.close()
        if not usuario or usuario.rol not in ("vendedor", "administrador"):
            await websocket.close(code=1008)
            return
    except Exception:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    print(f"✅ [WS] Vendedor ubicación conectado: {usuario_id}")

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("tipo") == "ubicacion":
                    pedido_id = msg.get("pedido_id")
                    lat       = msg.get("lat")
                    lng       = msg.get("lng")
                    estado    = msg.get("estado", "en_camino")
                    if pedido_id and lat and lng:
                        await ws_manager.enviar_ubicacion_vendedor(
                            pedido_id, lat, lng, estado)
                elif msg.get("tipo") == "ping":
                    await websocket.send_text(
                        json.dumps({"tipo": "pong"}))
            except Exception as e:
                print(f"❌ [WS] Error procesando ubicación: {e}")
    except WebSocketDisconnect:
        print(f"🔌 [WS] Vendedor ubicación desconectado: {usuario_id}")