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