from datetime  import datetime, timezone
from decimal   import Decimal
from typing    import List, Optional

from fastapi        import APIRouter, Depends, HTTPException, Query
from pydantic       import BaseModel
from sqlalchemy     import text, func
from sqlalchemy.orm import Session

from app.database              import get_db
from app.models.pedido         import Configuracion, Pedido, PedidoItem
from app.models.producto       import Producto
from app.models.cliente        import Cliente
from app.models.vendedor       import Vendedor
from app.models.repartidor     import Repartidor
from app.models.usuario        import Usuario
from app.core.dependencies     import (
    get_usuario_actual, requiere_vendedor, requiere_repartidor)
from app.services.notificaciones import enviar_notificacion
from app.services.websocket_manager import ws_manager

router = APIRouter(prefix="/pedidos", tags=["Pedidos"])


# ══════════════════════════════════════════════════════════
#  SCHEMAS
# ══════════════════════════════════════════════════════════
class ItemPedido(BaseModel):
    producto_id: str
    cantidad:    int = 1

class CrearPedido(BaseModel):
    items:             List[ItemPedido]
    tipo_pago:         str            = "contraentrega"
    tipo:              str            = "normal"    # normal | reserva
    empresa_id:        Optional[str]  = None
    direccion_entrega: Optional[str]  = None
    latitud_entrega:   Optional[float] = None
    longitud_entrega:  Optional[float] = None
    notas:             Optional[str]  = None

class ActualizarEstado(BaseModel):
    estado: str


# ══════════════════════════════════════════════════════════
#  HELPERS — serialización
# ══════════════════════════════════════════════════════════
def _item_dict(item: PedidoItem) -> dict:
    return {
        "id":          str(item.id),
        "producto_id": str(item.producto_id),
        "nombre":      item.producto.nombre    if item.producto else "",
        "imagen_url":  item.producto.imagen_url if item.producto else None,
        "cantidad":    item.cantidad,
        "precio_unit": float(item.precio_unit),
        "subtotal":    float(item.subtotal),
    }

def _pedido_dict(p: Pedido) -> dict:
    return {
        "id":                str(p.id),
        "tipo":              p.tipo,
        "cliente_id":        str(p.cliente_id),
        "cliente_nombre":    p.cliente.nombre    if p.cliente else "",
        "cliente_telefono":  p.cliente.telefono  if p.cliente else None,
        "vendedor_id":       str(p.vendedor_id)  if p.vendedor_id   else None,
        "vendedor_nombre":   p.vendedor.nombre_completo
                             if p.vendedor else None,
        "repartidor_id":     str(p.repartidor_id) if p.repartidor_id else None,
        "repartidor_nombre": p.repartidor.nombre_completo
                             if p.repartidor else None,
        "empresa_id":        str(p.empresa_id) if p.empresa_id else None,
        "empresa_nombre":    p.empresa.nombre  if p.empresa    else None,
        "estado":            p.estado,
        "tipo_pago":         p.tipo_pago,
        "costo_envio":       float(p.costo_envio) if p.costo_envio else 0.0,
        "total":             float(p.total),
        "direccion_entrega": p.direccion_entrega,
        "latitud_entrega":   float(p.latitud_entrega)  if p.latitud_entrega  else None,
        "longitud_entrega":  float(p.longitud_entrega) if p.longitud_entrega else None,
        "notas":             p.notas,
        "comprobante_url":   p.comprobante_url,
        "aceptado_en":       p.aceptado_en.isoformat() if p.aceptado_en else None,
        "creado_en":         p.creado_en.isoformat()   if p.creado_en   else None,
        "items":             [_item_dict(i) for i in p.items],
    }


# ══════════════════════════════════════════════════════════
#  HELPERS — async runner
# ══════════════════════════════════════════════════════════
def _run_async(coro):
    import asyncio, threading
    done  = threading.Event()
    errors = []
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:    loop.run_until_complete(coro)
        except Exception as e: errors.append(e)
        finally: loop.close(); done.set()
    threading.Thread(target=_run, daemon=True).start()
    done.wait(timeout=5)


# ══════════════════════════════════════════════════════════
#  HELPERS — notificaciones (reutilizables)
# ══════════════════════════════════════════════════════════
def _ws_broadcast(mensaje: dict, usuario_ids: List[str]):
    """Envía mensaje WS a lista de usuarios."""
    for uid in usuario_ids:
        try:
            _run_async(ws_manager.notificar_vendedor(uid, mensaje))
        except Exception as e:
            print(f"❌ [WS] {uid}: {e}")

def _fcm_broadcast(db, usuario_ids: List[str],
                   titulo: str, cuerpo: str, datos: dict):
    """Envía FCM a lista de usuarios."""
    from app.models.fcm_token import FcmToken
    for uid in usuario_ids:
        try:
            enviar_notificacion(db, uid, titulo, cuerpo, datos)
        except Exception as e:
            print(f"❌ [FCM] {uid}: {e}")

def _notificar_repartidores(db, pedido: Pedido):
    """Pedido normal → notifica a todos los repartidores activos."""
    mensaje_ws = {
        "tipo":      "nuevo_pedido",
        "pedido_id": str(pedido.id),
        "cliente":   pedido.cliente.nombre if pedido.cliente else "",
        "total":     float(pedido.total),
        "tipo_pago": pedido.tipo_pago,
        "tipo_pedido": "normal",
        "creado_en": pedido.creado_en.isoformat() if pedido.creado_en else "",
    }
    repartidores = db.query(Repartidor).filter(
        Repartidor.esta_activo == True).all()
    uids = [str(r.usuario_id) for r in repartidores]

    # WS primero
    try:
        _run_async(ws_manager.notificar_todos_vendedores(mensaje_ws))
    except Exception as e:
        print(f"❌ [WS] broadcast repartidores: {e}")

    # FCM backup
    _fcm_broadcast(
        db, uids,
        titulo = "🛒 Nuevo pedido de entrega",
        cuerpo = f"Pedido de {pedido.cliente.nombre if pedido.cliente else 'cliente'}"
                 f" por ${float(pedido.total):.2f}",
        datos  = {"tipo": "nuevo_pedido", "pedido_id": str(pedido.id)},
    )

def _notificar_vendedor_reserva(db, pedido: Pedido):
    """Reserva → notifica al vendedor asignado a la ruta de la empresa."""
    if not pedido.empresa_id:
        return

    # Encontrar vendedor con esa empresa en su ruta activa
    row = db.execute(text("""
        SELECT v.usuario_id
        FROM vendedores v
        JOIN ruta_asignaciones ra ON ra.vendedor_id = v.id
            AND ra.esta_activa = TRUE
        JOIN rutas r ON r.id = ra.ruta_id AND r.esta_activa = TRUE
        JOIN ruta_empresas re ON re.ruta_id = r.id
            AND re.empresa_id = :eid
        WHERE v.esta_activo = TRUE
        LIMIT 1
    """), {"eid": str(pedido.empresa_id)}).mappings().first()

    if not row:
        return

    uid = str(row["usuario_id"])
    mensaje_ws = {
        "tipo":        "nueva_reserva",
        "pedido_id":   str(pedido.id),
        "cliente":     pedido.cliente.nombre if pedido.cliente else "",
        "empresa":     pedido.empresa.nombre if pedido.empresa else "",
        "total":       float(pedido.total),
        "tipo_pedido": "reserva",
    }
    _ws_broadcast(mensaje_ws, [uid])
    _fcm_broadcast(
        db, [uid],
        titulo = "📋 Nueva reserva en tu ruta",
        cuerpo = f"{pedido.cliente.nombre if pedido.cliente else 'Cliente'} "
                 f"reservó en {pedido.empresa.nombre if pedido.empresa else 'empresa'}.",
        datos  = {"tipo": "nueva_reserva", "pedido_id": str(pedido.id)},
    )

def _notificar_aceptado(db, pedido: Pedido, aceptado_por_uid: str,
                        nombre_aceptador: str):
    """Notifica al cliente que su pedido fue aceptado."""
    # WS al aceptador
    _ws_broadcast(
        {"tipo": "pedido_asignado", "pedido_id": str(pedido.id),
         "cliente": pedido.cliente.nombre if pedido.cliente else "",
         "total": float(pedido.total)},
        [aceptado_por_uid],
    )
    # FCM al cliente
    if pedido.cliente and pedido.cliente.usuario_id:
        _fcm_broadcast(
            db, [str(pedido.cliente.usuario_id)],
            titulo = "✅ ¡Pedido aceptado!",
            cuerpo = f"{nombre_aceptador} está preparando tu pedido.",
            datos  = {"tipo": "pedido_aceptado", "pedido_id": str(pedido.id)},
        )

def _notificar_entregado(db, pedido: Pedido):
    if pedido.cliente and pedido.cliente.usuario_id:
        _fcm_broadcast(
            db, [str(pedido.cliente.usuario_id)],
            titulo = "🎉 ¡Pedido entregado!",
            cuerpo = "Tu pedido fue entregado. ¡Gracias!",
            datos  = {"tipo": "pedido_entregado", "pedido_id": str(pedido.id)},
        )


# ══════════════════════════════════════════════════════════
#  HELPER — validar reserva
# ══════════════════════════════════════════════════════════
def _validar_reserva(db, cliente: Cliente, empresa_id: str) -> str:
    """
    Verifica que el cliente pertenece a la empresa y que
    hay un vendedor con esa empresa en su ruta activa.
    Retorna el nombre de la empresa o lanza HTTPException.
    """
    if not cliente.empresa_id:
        raise HTTPException(
            400,
            "Para hacer una reserva debes pertenecer a una empresa.")

    if str(cliente.empresa_id) != empresa_id:
        raise HTTPException(
            400,
            "Solo puedes hacer reservas en tu propia empresa.")

    # Verificar que haya vendedor con esa empresa en ruta
    row = db.execute(text("""
        SELECT r.nombre AS empresa_nombre
        FROM vendedores v
        JOIN ruta_asignaciones ra ON ra.vendedor_id = v.id
            AND ra.esta_activa = TRUE
        JOIN rutas r2 ON r2.id = ra.ruta_id AND r2.esta_activa = TRUE
        JOIN ruta_empresas re ON re.ruta_id = r2.id
            AND re.empresa_id = :eid
        JOIN empresas r ON r.id = :eid
        WHERE v.esta_activo = TRUE
        LIMIT 1
    """), {"eid": empresa_id}).mappings().first()

    if not row:
        raise HTTPException(
            404,
            "No hay vendedores disponibles para esta empresa.")

    return row["empresa_nombre"]


# ══════════════════════════════════════════════════════════
#  HELPER — lock atómico aceptar
# ══════════════════════════════════════════════════════════
def _aceptar_pedido_atomico(
    db, pedido_id: str, aceptador_id: str,
    campo: str  # "vendedor_id" | "repartidor_id"
) -> Pedido:
    resultado = db.execute(text(f"""
        UPDATE pedidos
        SET estado       = 'aceptado',
            {campo}      = :aid,
            aceptado_en  = NOW()
        WHERE id     = :pid
          AND estado = 'pendiente'
        RETURNING id
    """), {"aid": aceptador_id, "pid": pedido_id}).fetchone()

    if not resultado:
        raise HTTPException(
            409, "Este pedido ya fue aceptado por otro.")
    db.commit()
    return db.query(Pedido).filter(Pedido.id == pedido_id).first()


# ══════════════════════════════════════════════════════════
#  ENDPOINTS COMPARTIDOS
# ══════════════════════════════════════════════════════════

@router.get("/configuracion")
def obtener_configuracion(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    claves = ["whatsapp_numero", "cuenta_banco",
              "cuenta_titular", "costo_envio"]
    return {
        c: (db.query(Configuracion)
              .filter(Configuracion.clave == c)
              .first() or type("", (), {"valor": ""})()).valor
        for c in claves
    }


@router.get("/mis-pedidos")
def mis_pedidos(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    if usuario.rol != "cliente":
        raise HTTPException(403, "Solo clientes.")
    cliente = db.query(Cliente).filter(
        Cliente.usuario_id == usuario.id).first()
    if not cliente:
        return []
    pedidos = db.query(Pedido).filter(
        Pedido.cliente_id == cliente.id
    ).order_by(Pedido.creado_en.desc()).all()
    return [_pedido_dict(p) for p in pedidos]


# ══════════════════════════════════════════════════════════
#  ENDPOINTS CLIENTE
# ══════════════════════════════════════════════════════════

@router.post("/")
def crear_pedido(
    datos:   CrearPedido,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    if usuario.rol != "cliente":
        raise HTTPException(403, "Solo clientes.")

    cliente = db.query(Cliente).filter(
        Cliente.usuario_id == usuario.id).first()
    if not cliente:
        raise HTTPException(404, "Cliente no encontrado.")
    if not datos.items:
        raise HTTPException(400, "El pedido debe tener al menos un producto.")
    if datos.tipo_pago not in ("transferencia", "contraentrega"):
        raise HTTPException(400, "Tipo de pago inválido.")
    if datos.tipo not in ("normal", "reserva"):
        raise HTTPException(400, "Tipo de pedido inválido.")

    # Validar reserva
    if datos.tipo == "reserva":
        if not datos.empresa_id:
            raise HTTPException(
                400, "Una reserva requiere empresa_id.")
        _validar_reserva(db, cliente, datos.empresa_id)

    # Calcular total
    total, items_data = Decimal("0"), []
    for item in datos.items:
        if item.cantidad <= 0:
            raise HTTPException(400, "Cantidad debe ser > 0.")
        producto = db.query(Producto).filter(
            Producto.id == item.producto_id,
            Producto.esta_activo == True).first()
        if not producto:
            raise HTTPException(
                404, f"Producto {item.producto_id} no encontrado.")
        sub = Decimal(str(producto.precio)) * item.cantidad
        total += sub
        items_data.append({
            "producto_id": producto.id,
            "cantidad":    item.cantidad,
            "precio_unit": Decimal(str(producto.precio)),
            "subtotal":    sub,
        })

    cfg = db.query(Configuracion).filter(
        Configuracion.clave == "costo_envio").first()
    costo_envio = Decimal(str(cfg.valor or "0")) if cfg else Decimal("0")
    # Reservas sin costo de envío
    if datos.tipo == "reserva":
        costo_envio = Decimal("0")
    total += costo_envio

    pedido = Pedido(
        cliente_id        = cliente.id,
        tipo              = datos.tipo,
        empresa_id        = datos.empresa_id,
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

    for d in items_data:
        db.add(PedidoItem(
            pedido_id   = pedido.id,
            producto_id = d["producto_id"],
            cantidad    = d["cantidad"],
            precio_unit = d["precio_unit"],
            subtotal    = d["subtotal"],
        ))
    db.commit()
    db.refresh(pedido)

    # Notificar según tipo
    if datos.tipo == "reserva":
        _notificar_vendedor_reserva(db, pedido)
    else:
        _notificar_repartidores(db, pedido)

    return _pedido_dict(pedido)


# ══════════════════════════════════════════════════════════
#  ENDPOINTS REPARTIDOR
# ══════════════════════════════════════════════════════════

@router.get("/repartidor/disponibles")
def pedidos_disponibles_repartidor(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_repartidor),
):
    """Pedidos normales pendientes para el repartidor."""
    pedidos = db.query(Pedido).filter(
        Pedido.estado == "pendiente",
        Pedido.tipo   == "normal",
    ).order_by(Pedido.creado_en.desc()).all()
    return [_pedido_dict(p) for p in pedidos]


@router.get("/repartidor/activo")
def pedido_activo_repartidor(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_repartidor),
):
    repartidor = db.query(Repartidor).filter(
        Repartidor.usuario_id == usuario.id).first()
    if not repartidor:
        return None
    pedido = db.query(Pedido).filter(
        Pedido.repartidor_id == repartidor.id,
        Pedido.estado.in_(["aceptado", "en_camino"]),
    ).order_by(Pedido.aceptado_en.desc()).first()
    return _pedido_dict(pedido) if pedido else None


@router.post("/{pedido_id}/aceptar-repartidor")
def aceptar_pedido_repartidor(
    pedido_id: str,
    db:        Session = Depends(get_db),
    usuario:   Usuario = Depends(requiere_repartidor),
):
    repartidor = db.query(Repartidor).filter(
        Repartidor.usuario_id == usuario.id).first()
    if not repartidor:
        raise HTTPException(404, "Repartidor no encontrado.")

    # Verificar sin pedido activo
    activo = db.query(Pedido).filter(
        Pedido.repartidor_id == repartidor.id,
        Pedido.estado.in_(["aceptado", "en_camino"]),
    ).first()
    if activo:
        raise HTTPException(
            400, "Ya tienes un pedido activo.")

    pedido = _aceptar_pedido_atomico(
        db, pedido_id, str(repartidor.id), "repartidor_id")

    _notificar_aceptado(
        db, pedido,
        aceptado_por_uid = str(repartidor.usuario_id),
        nombre_aceptador = repartidor.nombre_completo,
    )
    return _pedido_dict(pedido)


@router.put("/{pedido_id}/estado-repartidor")
def actualizar_estado_repartidor(
    pedido_id: str,
    datos:     ActualizarEstado,
    db:        Session = Depends(get_db),
    usuario:   Usuario = Depends(requiere_repartidor),
):
    if datos.estado not in {"en_camino", "entregado", "cancelado"}:
        raise HTTPException(400, "Estado inválido.")

    repartidor = db.query(Repartidor).filter(
        Repartidor.usuario_id == usuario.id).first()
    pedido = db.query(Pedido).filter(
        Pedido.id == pedido_id).first()
    if not pedido:
        raise HTTPException(404, "Pedido no encontrado.")
    if str(pedido.repartidor_id) != str(repartidor.id):
        raise HTTPException(403, "No puedes modificar este pedido.")

    pedido.estado = datos.estado
    db.commit()

    if datos.estado == "entregado":
        _notificar_entregado(db, pedido)

    return _pedido_dict(pedido)


# ══════════════════════════════════════════════════════════
#  ENDPOINTS VENDEDOR — solo reservas
# ══════════════════════════════════════════════════════════

@router.get("/vendedor/reservas")
def reservas_vendedor(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    """Reservas pendientes asignadas a la ruta del vendedor."""
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id).first()
    if not vendedor:
        return []

    # Empresas en la ruta del vendedor
    empresas_ids = db.execute(text("""
        SELECT re.empresa_id
        FROM ruta_empresas re
        JOIN ruta_asignaciones ra ON ra.ruta_id = re.ruta_id
            AND ra.vendedor_id = :vid
            AND ra.esta_activa = TRUE
        JOIN rutas r ON r.id = re.ruta_id AND r.esta_activa = TRUE
    """), {"vid": str(vendedor.id)}).fetchall()

    if not empresas_ids:
        return []

    ids = [str(r[0]) for r in empresas_ids]
    pedidos = db.query(Pedido).filter(
        Pedido.tipo       == "reserva",
        Pedido.estado     == "pendiente",
        Pedido.empresa_id.in_(ids),
    ).order_by(Pedido.creado_en.desc()).all()

    return [_pedido_dict(p) for p in pedidos]


@router.post("/{pedido_id}/aceptar-reserva")
def aceptar_reserva_vendedor(
    pedido_id: str,
    db:        Session = Depends(get_db),
    usuario:   Usuario = Depends(requiere_vendedor),
):
    """Vendedor acepta una reserva de su ruta."""
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id).first()
    if not vendedor:
        raise HTTPException(404, "Vendedor no encontrado.")

    pedido = db.query(Pedido).filter(
        Pedido.id   == pedido_id,
        Pedido.tipo == "reserva",
    ).first()
    if not pedido:
        raise HTTPException(404, "Reserva no encontrada.")

    pedido = _aceptar_pedido_atomico(
        db, pedido_id, str(vendedor.id), "vendedor_id")

    _notificar_aceptado(
        db, pedido,
        aceptado_por_uid = str(vendedor.usuario_id),
        nombre_aceptador = vendedor.nombre_completo,
    )
    return _pedido_dict(pedido)


@router.put("/{pedido_id}/estado-vendedor")
def actualizar_estado_vendedor(
    pedido_id: str,
    datos:     ActualizarEstado,
    db:        Session = Depends(get_db),
    usuario:   Usuario = Depends(requiere_vendedor),
):
    """Vendedor actualiza estado de una reserva."""
    if datos.estado not in {"entregado", "cancelado"}:
        raise HTTPException(
            400, "Estado inválido para reserva.")

    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id).first()
    pedido = db.query(Pedido).filter(
        Pedido.id == pedido_id).first()
    if not pedido:
        raise HTTPException(404, "Pedido no encontrado.")
    if str(pedido.vendedor_id) != str(vendedor.id):
        raise HTTPException(403, "No puedes modificar este pedido.")

    pedido.estado = datos.estado
    db.commit()
    if datos.estado == "entregado":
        _notificar_entregado(db, pedido)
    return _pedido_dict(pedido)


# ══════════════════════════════════════════════════════════
#  HISTORIAL
# ══════════════════════════════════════════════════════════

@router.get("/historial-repartidor")
def historial_repartidor(
    desde:   str     = Query(...),
    hasta:   str     = Query(...),
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_repartidor),
):
    repartidor = db.query(Repartidor).filter(
        Repartidor.usuario_id == usuario.id).first()
    if not repartidor:
        return []
    pedidos = db.query(Pedido).filter(
        Pedido.repartidor_id == repartidor.id,
        Pedido.estado.in_(["entregado", "en_camino", "aceptado"]),
        func.date(Pedido.creado_en) >= desde,
        func.date(Pedido.creado_en) <= hasta,
    ).order_by(Pedido.creado_en.desc()).all()
    return [_pedido_dict(p) for p in pedidos]


@router.get("/historial-vendedor")
def historial_vendedor(
    desde:   str     = Query(...),
    hasta:   str     = Query(...),
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id).first()
    if not vendedor:
        return []
    pedidos = db.query(Pedido).filter(
        Pedido.vendedor_id == vendedor.id,
        Pedido.tipo        == "reserva",
        Pedido.estado.in_(["entregado", "en_camino", "aceptado"]),
        func.date(Pedido.creado_en) >= desde,
        func.date(Pedido.creado_en) <= hasta,
    ).order_by(Pedido.creado_en.desc()).all()
    return [_pedido_dict(p) for p in pedidos]


# ══════════════════════════════════════════════════════════
#  TIEMPO ESTIMADO DE ENTREGA (OSRM)
# ══════════════════════════════════════════════════════════

@router.get("/{pedido_id}/tiempo-estimado")
def tiempo_estimado(
    pedido_id: str,
    lat_rep:   float = Query(..., description="Lat repartidor/vendedor"),
    lng_rep:   float = Query(..., description="Lng repartidor/vendedor"),
    db:        Session = Depends(get_db),
    usuario:   Usuario = Depends(get_usuario_actual),
):
    """
    Calcula tiempo estimado en minutos desde la posición
    del repartidor/vendedor hasta el punto de entrega,
    usando OSRM peatón.
    """
    import httpx

    pedido = db.query(Pedido).filter(
        Pedido.id == pedido_id).first()
    if not pedido:
        raise HTTPException(404, "Pedido no encontrado.")

    if not pedido.latitud_entrega or not pedido.longitud_entrega:
        return {"minutos": None, "distancia_metros": None}

    url = (
        f"https://routing.openstreetmap.de/routed-foot"
        f"/route/v1/foot/"
        f"{lng_rep},{lat_rep};"
        f"{float(pedido.longitud_entrega)},"
        f"{float(pedido.latitud_entrega)}"
        f"?overview=false"
    )
    try:
        r = httpx.get(url, timeout=10,
                      headers={"User-Agent": "EmpanaTrack/1.0"})
        if r.status_code == 200:
            routes = r.json().get("routes", [])
            if routes:
                duracion  = routes[0]["duration"]   # segundos
                distancia = routes[0]["distance"]   # metros
                return {
                    "minutos":          round(duracion / 60, 1),
                    "distancia_metros": round(distancia),
                }
    except Exception as e:
        print(f"❌ OSRM tiempo: {e}")

    return {"minutos": None, "distancia_metros": None}

# ══════════════════════════════════════════════════════════
#  ENDPOINTS VENDEDOR — RESERVAS
# ══════════════════════════════════════════════════════════

@router.get("/vendedor/reservas")
def reservas_vendedor(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    """Reservas pendientes asignadas a la ruta del vendedor."""
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id).first()
    if not vendedor:
        return []

    # Empresas en la ruta del vendedor
    empresas_ids = db.execute(text("""
        SELECT re.empresa_id
        FROM ruta_empresas re
        JOIN ruta_asignaciones ra ON ra.ruta_id = re.ruta_id
            AND ra.vendedor_id = :vid
            AND ra.esta_activa = TRUE
        JOIN rutas r ON r.id = re.ruta_id AND r.esta_activa = TRUE
    """), {"vid": str(vendedor.id)}).fetchall()

    if not empresas_ids:
        return []

    ids = [str(r[0]) for r in empresas_ids]
    pedidos = db.query(Pedido).filter(
        Pedido.tipo       == "reserva",
        Pedido.estado     == "pendiente",
        Pedido.empresa_id.in_(ids),
    ).order_by(Pedido.creado_en.desc()).all()

    return [_pedido_dict(p) for p in pedidos]


@router.get("/vendedor/reserva-activa")
def reserva_activa_vendedor(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    """
    Obtiene la reserva activa que el vendedor tiene actualmente.
    Una reserva activa es aquella en estado 'aceptado' (no entregada ni cancelada).
    """
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id).first()
    if not vendedor:
        return None
    
    # Buscar reserva activa (aceptada pero no finalizada)
    pedido = db.query(Pedido).filter(
        Pedido.vendedor_id == vendedor.id,
        Pedido.tipo        == "reserva",
        Pedido.estado      == "aceptado",
    ).order_by(Pedido.aceptado_en.desc()).first()
    
    return _pedido_dict(pedido) if pedido else None


@router.post("/{pedido_id}/aceptar-reserva")
def aceptar_reserva_vendedor(
    pedido_id: str,
    db:        Session = Depends(get_db),
    usuario:   Usuario = Depends(requiere_vendedor),
):
    """Vendedor acepta una reserva de su ruta."""
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id).first()
    if not vendedor:
        raise HTTPException(404, "Vendedor no encontrado.")

    # Verificar que el vendedor no tenga ya una reserva activa
    activa = db.query(Pedido).filter(
        Pedido.vendedor_id == vendedor.id,
        Pedido.tipo        == "reserva",
        Pedido.estado      == "aceptado",
    ).first()
    if activa:
        raise HTTPException(400, "Ya tienes una reserva activa. Debes entregarla o cancelarla antes de aceptar otra.")

    pedido = db.query(Pedido).filter(
        Pedido.id   == pedido_id,
        Pedido.tipo == "reserva",
        Pedido.estado == "pendiente",
    ).first()
    if not pedido:
        raise HTTPException(404, "Reserva no encontrada o ya no está disponible.")

    # Aceptar la reserva
    pedido.estado = "aceptado"
    pedido.vendedor_id = vendedor.id
    pedido.aceptado_en = datetime.now(timezone.utc)
    db.commit()
    db.refresh(pedido)

    # Notificar al cliente
    if pedido.cliente and pedido.cliente.usuario_id:
        _fcm_broadcast(
            db, [str(pedido.cliente.usuario_id)],
            titulo = "✅ ¡Reserva aceptada!",
            cuerpo = f"{vendedor.nombre_completo} ha aceptado tu reserva.",
            datos  = {"tipo": "reserva_aceptada", "pedido_id": str(pedido.id)},
        )

    # Notificar a otros vendedores que esta reserva ya fue aceptada
    _ws_broadcast(
        {"tipo": "reserva_aceptada_otro", "pedido_id": str(pedido.id)},
        [],  # broadcast a todos los vendedores
    )

    return _pedido_dict(pedido)


@router.put("/{pedido_id}/estado-vendedor")
def actualizar_estado_vendedor(
    pedido_id: str,
    datos:     ActualizarEstado,
    db:        Session = Depends(get_db),
    usuario:   Usuario = Depends(requiere_vendedor),
):
    """Vendedor actualiza estado de una reserva (entregado/cancelado)."""
    if datos.estado not in {"entregado", "cancelado"}:
        raise HTTPException(400, "Estado inválido para reserva. Use 'entregado' o 'cancelado'.")

    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id).first()
    if not vendedor:
        raise HTTPException(404, "Vendedor no encontrado.")

    pedido = db.query(Pedido).filter(
        Pedido.id == pedido_id,
        Pedido.tipo == "reserva",
    ).first()
    if not pedido:
        raise HTTPException(404, "Reserva no encontrada.")
    
    if str(pedido.vendedor_id) != str(vendedor.id):
        raise HTTPException(403, "No puedes modificar esta reserva porque no la aceptaste tú.")

    if pedido.estado not in ["aceptado", "pendiente"]:
        raise HTTPException(400, f"La reserva ya está {pedido.estado} y no se puede modificar.")

    pedido.estado = datos.estado
    db.commit()

    # Notificar al cliente
    if pedido.cliente and pedido.cliente.usuario_id:
        if datos.estado == "entregado":
            _fcm_broadcast(
                db, [str(pedido.cliente.usuario_id)],
                titulo = "🎉 ¡Reserva entregada!",
                cuerpo = "Tu reserva ha sido entregada. ¡Gracias por confiar en nosotros!",
                datos  = {"tipo": "reserva_entregada", "pedido_id": str(pedido.id)},
            )
        else:
            _fcm_broadcast(
                db, [str(pedido.cliente.usuario_id)],
                titulo = "❌ Reserva cancelada",
                cuerpo = f"Tu reserva en {pedido.empresa.nombre if pedido.empresa else 'la empresa'} ha sido cancelada.",
                datos  = {"tipo": "reserva_cancelada", "pedido_id": str(pedido.id)},
            )

    return _pedido_dict(pedido)


@router.get("/historial-vendedor")
def historial_vendedor(
    desde:   str     = Query(..., description="Fecha desde YYYY-MM-DD"),
    hasta:   str     = Query(..., description="Fecha hasta YYYY-MM-DD"),
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    """Historial de reservas del vendedor (entregadas, canceladas y aceptadas)."""
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id).first()
    if not vendedor:
        return []
    
    pedidos = db.query(Pedido).filter(
        Pedido.vendedor_id == vendedor.id,
        Pedido.tipo        == "reserva",
        Pedido.estado.in_(["entregado", "cancelado", "aceptado"]),
        func.date(Pedido.creado_en) >= desde,
        func.date(Pedido.creado_en) <= hasta,
    ).order_by(Pedido.creado_en.desc()).all()
    
    return [_pedido_dict(p) for p in pedidos]