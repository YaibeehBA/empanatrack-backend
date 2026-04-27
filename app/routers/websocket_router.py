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
    try:
        payload    = decodificar_token(token)
        usuario_id = payload.get("sub")
        if not usuario_id:
            await websocket.close(code=1008)
            return
        db      = next(get_db())
        usuario = db.query(Usuario).filter(
            Usuario.id == usuario_id).first()
        db.close()
        if not usuario:
            await websocket.close(code=1008)
            return
    except Exception:
        await websocket.close(code=1008)
        return

    # ── USA LA NUEVA API con prefijo "pedido:" ──
    conectado = await ws_manager.conectar_suscriptor(
        websocket    = websocket,
        sesion_key   = f"pedido:{pedido_id}",
        usuario_id   = str(usuario_id),
        token_valido = True,
    )
    if not conectado:
        return

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
        ws_manager.desconectar_suscriptor(
            websocket,
            f"pedido:{pedido_id}",
        )


@router.websocket("/ws/vendedor-ubicacion")
async def websocket_vendedor_ubicacion(
    websocket: WebSocket,
    token:     str = Query(...),
):
    try:
        payload    = decodificar_token(token)
        usuario_id = payload.get("sub")
        if not usuario_id:
            await websocket.close(code=1008)
            return
        db      = next(get_db())
        usuario = db.query(Usuario).filter(
            Usuario.id == usuario_id).first()
        db.close()
        if not usuario or usuario.rol not in (
                "vendedor", "administrador", "repartidor"):
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
                        # ── USA LA NUEVA API con prefijo "pedido:" ──
                        await ws_manager.broadcast_ubicacion(
                            sesion_key  = f"pedido:{pedido_id}",
                            vendedor_id = str(usuario_id),
                            lat         = float(lat),
                            lng         = float(lng),
                            extra       = {"estado": estado},
                        )
                elif msg.get("tipo") == "ping":
                    await websocket.send_text(
                        json.dumps({"tipo": "pong"}))
            except Exception as e:
                print(f"❌ [WS] Error procesando ubicación: {e}")
    except WebSocketDisconnect:
        print(f"🔌 [WS] Vendedor ubicación desconectado: {usuario_id}")
        
@router.websocket("/ws/mapa-cliente/{sesion_id}")
async def ws_mapa_cliente(
    websocket: WebSocket,
    sesion_id: str,
    token:     str = Query(...),
):
    try:
        payload    = decodificar_token(token)
        usuario_id = payload.get("sub")
        if not usuario_id:
            await websocket.close(code=1008)
            return
        db      = next(get_db())
        usuario = db.query(Usuario).filter(
            Usuario.id == usuario_id).first()
        db.close()
        if not usuario:
            await websocket.close(code=1008)
            return
    except Exception:
        await websocket.close(code=1008)
        return

    # ── USA LA NUEVA API: conectar_suscriptor con prefijo "sesion:" ──
    conectado = await ws_manager.conectar_suscriptor(
        websocket   = websocket,
        sesion_key  = f"sesion:{sesion_id}",
        usuario_id  = str(usuario_id),
        token_valido = True,
    )
    if not conectado:
        return

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
        ws_manager.desconectar_suscriptor(
            websocket,
            f"sesion:{sesion_id}",
        )


@router.websocket("/ws/ruta-vendedor")
async def ws_ruta_vendedor(
    websocket: WebSocket,
    token:     str = Query(...),
):
    try:
        payload    = decodificar_token(token)
        usuario_id = payload.get("sub")
        if not usuario_id:
            await websocket.close(code=1008)
            return
        db      = next(get_db())
        usuario = db.query(Usuario).filter(
            Usuario.id == usuario_id).first()
        db.close()
        if not usuario or usuario.rol not in (
                "vendedor", "administrador"):
            await websocket.close(code=1008)
            return
    except Exception:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    print(f"✅ [WS] Ruta vendedor conectado: {usuario_id}")

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("tipo") == "ubicacion_ruta":
                    lat       = msg.get("lat")
                    lng       = msg.get("lng")
                    sesion_id = msg.get("sesion_id")
                    if lat and lng and sesion_id:
                        # ── USA LA NUEVA API: broadcast_ubicacion ──
                        await ws_manager.broadcast_ubicacion(
                            sesion_key  = f"sesion:{sesion_id}",
                            vendedor_id = str(usuario_id),
                            lat         = float(lat),
                            lng         = float(lng),
                        )
                elif msg.get("tipo") == "ping":
                    await websocket.send_text(
                        json.dumps({"tipo": "pong"}))
            except Exception as e:
                print(f"❌ [WS] Error ruta-vendedor: {e}")
    except WebSocketDisconnect:
        print(f"🔌 [WS] Ruta vendedor desconectado: {usuario_id}")