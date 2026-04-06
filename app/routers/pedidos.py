from datetime  import datetime, timezone
from decimal   import Decimal
from typing    import List, Optional
from uuid      import UUID

from fastapi        import APIRouter, Depends, HTTPException, Query
from pydantic       import BaseModel
from sqlalchemy     import text
from sqlalchemy.orm import Session
from sqlalchemy     import func

from app.database           import get_db
from app.models.pedido      import Configuracion, Pedido, PedidoItem
from app.models.producto    import Producto
from app.models.cliente     import Cliente
from app.models.vendedor    import Vendedor
from app.models.usuario     import Usuario
from app.core.dependencies  import get_usuario_actual, requiere_vendedor
from app.services.notificaciones import enviar_notificacion

router = APIRouter(prefix="/pedidos", tags=["Pedidos"])


# ══════════════════════════════════════════════════════════
#  SCHEMAS
# ══════════════════════════════════════════════════════════
class ItemPedido(BaseModel):
    producto_id: str
    cantidad:    int = 1


class CrearPedido(BaseModel):
    items:            List[ItemPedido]
    tipo_pago:        str = "contraentrega"
    direccion_entrega: Optional[str]   = None
    latitud_entrega:  Optional[float]  = None
    longitud_entrega: Optional[float]  = None
    notas:            Optional[str]    = None


class ActualizarEstado(BaseModel):
    estado: str


# ══════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════
def _item_dict(item: PedidoItem) -> dict:
    return {
        "id":           str(item.id),
        "producto_id":  str(item.producto_id),
        "nombre":       item.producto.nombre if item.producto else "",
        "imagen_url":   item.producto.imagen_url if item.producto else None,
        "cantidad":     item.cantidad,
        "precio_unit":  float(item.precio_unit),
        "subtotal":     float(item.subtotal),
    }


def _pedido_dict(p: Pedido) -> dict:
    return {
        "id":                str(p.id),
        "cliente_id":        str(p.cliente_id),
        "cliente_nombre":    p.cliente.nombre if p.cliente else "",
        "cliente_telefono":  p.cliente.telefono if p.cliente else None,
        "vendedor_id":       str(p.vendedor_id) if p.vendedor_id else None,
        "vendedor_nombre":   p.vendedor.nombre_completo
                             if p.vendedor else None,
        "estado":            p.estado,
        "tipo_pago":         p.tipo_pago,
        "costo_envio":      float(p.costo_envio) if p.costo_envio else 0.0,
        "total":             float(p.total),
        "direccion_entrega": p.direccion_entrega,
        "latitud_entrega":   float(p.latitud_entrega)
                             if p.latitud_entrega else None,
        "longitud_entrega":  float(p.longitud_entrega)
                             if p.longitud_entrega else None,
        "notas":             p.notas,
        "comprobante_url":   p.comprobante_url,
        "aceptado_en":       p.aceptado_en.isoformat()
                             if p.aceptado_en else None,
        "creado_en":         p.creado_en.isoformat()
                             if p.creado_en else None,
        "items":             [_item_dict(i) for i in p.items],
    }


# ══════════════════════════════════════════════════════════
#  GET /pedidos/configuracion  — datos banco + WhatsApp
# ══════════════════════════════════════════════════════════
@router.get("/configuracion")
def obtener_configuracion(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    claves = ["whatsapp_numero", "cuenta_banco", "cuenta_titular",  "costo_envio"]
    resultado = {}
    for clave in claves:
        cfg = db.query(Configuracion).filter(
            Configuracion.clave == clave
        ).first()
        resultado[clave] = cfg.valor if cfg else ""
    return resultado


# ══════════════════════════════════════════════════════════
#  POST /pedidos/  — crear pedido (cliente)
# ══════════════════════════════════════════════════════════
@router.post("/")
def crear_pedido(
    datos:   CrearPedido,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    if usuario.rol != "cliente":
        raise HTTPException(
            status_code=403,
            detail="Solo los clientes pueden crear pedidos.")

    cliente = db.query(Cliente).filter(
        Cliente.usuario_id == usuario.id
    ).first()
    if not cliente:
        raise HTTPException(status_code=404,
                            detail="Cliente no encontrado.")

    if not datos.items:
        raise HTTPException(status_code=400,
                            detail="El pedido debe tener al menos un producto.")

    if datos.tipo_pago not in ("transferencia", "contraentrega"):
        raise HTTPException(status_code=400,
                            detail="Tipo de pago inválido.")

    # Calcular total y crear items
    total      = Decimal("0")
    items_data = []
    for item in datos.items:
        if item.cantidad <= 0:
            raise HTTPException(
                status_code=400,
                detail="La cantidad debe ser mayor a 0.")

        producto = db.query(Producto).filter(
            Producto.id          == item.producto_id,
            Producto.esta_activo == True,
        ).first()
        if not producto:
            raise HTTPException(
                status_code=404,
                detail=f"Producto {item.producto_id} no encontrado.")

        subtotal = Decimal(str(producto.precio)) * item.cantidad
        total   += subtotal
        items_data.append({
            "producto_id": producto.id,
            "cantidad":    item.cantidad,
            "precio_unit": Decimal(str(producto.precio)),
            "subtotal":    subtotal,
        })

    # ── Costo de envío ────────────────────────────────────
    cfg_envio = db.query(Configuracion).filter(
        Configuracion.clave == "costo_envio"
    ).first()
    costo_envio = Decimal(str(cfg_envio.valor or "0")) \
        if cfg_envio else Decimal("0")
    total += costo_envio

    # Crear pedido
    pedido = Pedido(
        cliente_id        = cliente.id,
        tipo_pago         = datos.tipo_pago,
        total             = total,
        costo_envio       = costo_envio,
        direccion_entrega = datos.direccion_entrega,
        latitud_entrega   = datos.latitud_entrega,
        longitud_entrega  = datos.longitud_entrega,
        notas             = datos.notas,
    )
    db.add(pedido)
    db.flush()

    for item_d in items_data:
        db.add(PedidoItem(
            pedido_id   = pedido.id,
            producto_id = item_d["producto_id"],
            cantidad    = item_d["cantidad"],
            precio_unit = item_d["precio_unit"],
            subtotal    = item_d["subtotal"],
        ))

    db.commit()
    db.refresh(pedido)

    # Notificar a todos los vendedores
    _notificar_vendedores(db, pedido)

    return _pedido_dict(pedido)


# ══════════════════════════════════════════════════════════
#  GET /pedidos/disponibles  — pedidos pendientes (vendedores)
# ══════════════════════════════════════════════════════════
@router.get("/disponibles")
def pedidos_disponibles(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    pedidos = db.query(Pedido).filter(
        Pedido.estado == "pendiente"
    ).order_by(Pedido.creado_en.desc()).all()
    return [_pedido_dict(p) for p in pedidos]


# ══════════════════════════════════════════════════════════
#  POST /pedidos/{id}/aceptar  — aceptar con lock atómico
# ══════════════════════════════════════════════════════════
@router.post("/{pedido_id}/aceptar")
def aceptar_pedido(
    pedido_id: str,
    db:        Session = Depends(get_db),
    usuario:   Usuario = Depends(requiere_vendedor),
):
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id
    ).first()
    if not vendedor:
        raise HTTPException(status_code=404,
                            detail="Vendedor no encontrado.")

    # ── NUEVO: verificar que no tenga pedido activo ────
    pedido_activo = db.query(Pedido).filter(
        Pedido.vendedor_id == vendedor.id,
        Pedido.estado.in_(["aceptado", "en_camino"]),
    ).first()
    if pedido_activo:
        raise HTTPException(
            status_code=400,
            detail="Ya tienes un pedido activo. "
                   "Debes entregarlo antes de aceptar otro.")

    # Lock atómico
    resultado = db.execute(
        text("""
            UPDATE pedidos
            SET    estado      = 'aceptado',
                   vendedor_id = :vid,
                   aceptado_en = NOW()
            WHERE  id     = :pid
              AND  estado = 'pendiente'
            RETURNING id
        """),
        {"vid": str(vendedor.id), "pid": pedido_id},
    ).fetchone()

    if not resultado:
        raise HTTPException(
            status_code=409,
            detail="Este pedido ya fue aceptado por otro vendedor.")

    db.commit()
    pedido = db.query(Pedido).filter(Pedido.id == pedido_id).first()
    _notificar_cliente_aceptado(db, pedido, vendedor)
    return _pedido_dict(pedido)


# ══════════════════════════════════════════════════════════
#  GET /pedidos/vendedor/activo  — pedido activo del vendedor
# ══════════════════════════════════════════════════════════
@router.get("/vendedor/activo")
def pedido_activo_vendedor(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id
    ).first()
    if not vendedor:
        return None

    pedido = db.query(Pedido).filter(
        Pedido.vendedor_id == vendedor.id,
        Pedido.estado.in_(["aceptado", "en_camino"]),
    ).order_by(Pedido.aceptado_en.desc()).first()

    return _pedido_dict(pedido) if pedido else None


# ══════════════════════════════════════════════════════════
#  PUT /pedidos/{id}/estado  — actualizar estado
# ══════════════════════════════════════════════════════════
@router.put("/{pedido_id}/estado")
def actualizar_estado(
    pedido_id: str,
    datos:     ActualizarEstado,
    db:        Session = Depends(get_db),
    usuario:   Usuario = Depends(requiere_vendedor),
):
    estados_validos = {"en_camino", "entregado", "cancelado"}
    if datos.estado not in estados_validos:
        raise HTTPException(
            status_code=400,
            detail=f"Estado inválido. Usa: {', '.join(estados_validos)}")

    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id
    ).first()

    pedido = db.query(Pedido).filter(
        Pedido.id == pedido_id
    ).first()
    if not pedido:
        raise HTTPException(status_code=404,
                            detail="Pedido no encontrado.")

    # Solo el vendedor asignado puede cambiar el estado
    if str(pedido.vendedor_id) != str(vendedor.id):
        raise HTTPException(
            status_code=403,
            detail="No puedes modificar este pedido.")

    pedido.estado = datos.estado
    db.commit()

    # Notificar al cliente si fue entregado
    if datos.estado == "entregado":
        _notificar_cliente_entregado(db, pedido)

    return _pedido_dict(pedido)


# ══════════════════════════════════════════════════════════
#  GET /pedidos/mis-pedidos  — historial del cliente
# ══════════════════════════════════════════════════════════
@router.get("/mis-pedidos")
def mis_pedidos(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    if usuario.rol != "cliente":
        raise HTTPException(status_code=403,
                            detail="Solo clientes.")

    cliente = db.query(Cliente).filter(
        Cliente.usuario_id == usuario.id
    ).first()
    if not cliente:
        return []

    pedidos = db.query(Pedido).filter(
        Pedido.cliente_id == cliente.id
    ).order_by(Pedido.creado_en.desc()).all()

    return [_pedido_dict(p) for p in pedidos]



@router.get("/historial-vendedor")
def historial_pedidos_vendedor(
    desde:   str     = Query(...),
    hasta:   str     = Query(...),
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id
    ).first()
    if not vendedor:
        return []

    pedidos = db.query(Pedido).filter(
        Pedido.vendedor_id == vendedor.id,
        Pedido.estado.in_(['entregado', 'en_camino', 'aceptado']),
        func.date(Pedido.creado_en) >= desde,
        func.date(Pedido.creado_en) <= hasta,
    ).order_by(Pedido.creado_en.desc()).all()

    return [_pedido_dict(p) for p in pedidos]


# ══════════════════════════════════════════════════════════
#  HELPERS FCM
# ══════════════════════════════════════════════════════════
from app.services.websocket_manager import ws_manager

def _run_async(coro):
    """
    Ejecuta corrutina async desde endpoint sync de FastAPI.
    FastAPI/Uvicorn ya tiene un loop — usamos run_coroutine_threadsafe.
    """
    import asyncio
    import threading

    result_container = []
    error_container  = []
    done_event       = threading.Event()

    def run_in_thread():
        try:
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                result = new_loop.run_until_complete(coro)
                result_container.append(result)
            finally:
                new_loop.close()
        except Exception as e:
            error_container.append(e)
        finally:
            done_event.set()

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()
    done_event.wait(timeout=5)

    if error_container:
        raise error_container[0]



def _notificar_vendedores(db, pedido):
    """Notifica a todos los vendedores — WS + FCM fallback."""

    mensaje_ws = {
        "tipo":      "nuevo_pedido",
        "pedido_id": str(pedido.id),
        "cliente":   pedido.cliente.nombre if pedido.cliente else "",
        "total":     float(pedido.total),
        "tipo_pago": pedido.tipo_pago,
        "creado_en": pedido.creado_en.isoformat()
                     if pedido.creado_en else "",
    }

    # ── WebSocket ─────────────────────────────────────
    try:
        _run_async(ws_manager.notificar_todos_vendedores(mensaje_ws))
        print(f"✅ [WS] Notificado nuevo pedido a todos los vendedores")
    except Exception as e:
        print(f"❌ [WS] Error notificando vendedores: {e}")

    # ── FCM fallback ──────────────────────────────────
    try:
        from app.models.fcm_token import FcmToken
        vendedores = db.query(Vendedor).filter(
            Vendedor.esta_activo == True
        ).all()
        for v in vendedores:
            tokens = db.query(FcmToken).filter(
                FcmToken.usuario_id == v.usuario_id
            ).all()
            for t in tokens:
                try:
                    enviar_notificacion(
                        db         = db,
                        usuario_id = str(v.usuario_id),
                        titulo     = "🛒 Nuevo pedido disponible",
                        cuerpo     = f"Pedido de "
                                     f"{pedido.cliente.nombre if pedido.cliente else 'cliente'}"
                                     f" por ${float(pedido.total):.2f}",
                        datos = {
                            "tipo":      "nuevo_pedido",
                            "pedido_id": str(pedido.id),
                        },
                    )
                except Exception as ex:
                    print(f"❌ [FCM] Error enviando a vendedor: {ex}")
    except Exception as e:
        print(f"❌ [FCM] Error en FCM fallback: {e}")


def _notificar_cliente_aceptado(db, pedido, vendedor):
    """Notifica al cliente que su pedido fue aceptado + al vendedor."""

    # ── WS al vendedor que aceptó ─────────────────────
    mensaje_vendedor = {
        "tipo":      "pedido_asignado",
        "pedido_id": str(pedido.id),
        "cliente":   pedido.cliente.nombre if pedido.cliente else "",
        "total":     float(pedido.total),
        "tipo_pago": pedido.tipo_pago,
    }
    try:
        _run_async(ws_manager.notificar_vendedor(
            str(vendedor.usuario_id), mensaje_vendedor))
        print(f"✅ [WS] Notificado pedido asignado al vendedor")
    except Exception as e:
        print(f"❌ [WS] Error notificando vendedor: {e}")

    # ── FCM al cliente ────────────────────────────────
    try:
        if pedido.cliente and pedido.cliente.usuario_id:
            enviar_notificacion(
                db         = db,
                usuario_id = str(pedido.cliente.usuario_id),
                titulo     = "✅ ¡Pedido aceptado!",
                cuerpo     = f"{vendedor.nombre_completo} "
                             f"está preparando tu pedido.",
                datos = {
                    "tipo":      "pedido_aceptado",
                    "pedido_id": str(pedido.id),
                },
            )
    except Exception as e:
        print(f"❌ [FCM] Error notificando cliente: {e}")

    # ── FCM al vendedor (backup) ──────────────────────
    try:
        enviar_notificacion(
            db         = db,
            usuario_id = str(vendedor.usuario_id),
            titulo     = "📦 Pedido asignado",
            cuerpo     = f"Tienes un nuevo pedido de "
                         f"{pedido.cliente.nombre if pedido.cliente else 'cliente'}.",
            datos = {
                "tipo":      "pedido_asignado",
                "pedido_id": str(pedido.id),
            },
        )
    except Exception as e:
        print(f"❌ [FCM] Error notificando vendedor FCM: {e}")


def _notificar_cliente_entregado(db, pedido):
    """Notifica al cliente que su pedido fue entregado."""
    try:
        if pedido.cliente and pedido.cliente.usuario_id:
            enviar_notificacion(
                db         = db,
                usuario_id = str(pedido.cliente.usuario_id),
                titulo     = "🎉 ¡Pedido entregado!",
                cuerpo     = "Tu pedido ha sido entregado. "
                             "¡Gracias por tu compra!",
                datos = {
                    "tipo":      "pedido_entregado",
                    "pedido_id": str(pedido.id),
                },
            )
    except Exception as e:
        print(f"❌ [FCM] Error notificando entrega: {e}")