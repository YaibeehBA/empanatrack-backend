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
from app.models.ruta_activa    import StockDiario
from app.core.dependencies     import (
    get_usuario_actual, requiere_vendedor, requiere_repartidor)
from app.services.notificaciones import enviar_notificacion
from app.services.websocket_manager import ws_manager

from datetime import date

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
    tipo:              str            = "normal"
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
#  HELPERS — notificaciones
# ══════════════════════════════════════════════════════════
def _ws_broadcast(mensaje: dict, usuario_ids: List[str]):
    for uid in usuario_ids:
        try:
            _run_async(ws_manager.notificar_vendedor(uid, mensaje))
        except Exception as e:
            print(f"❌ [WS] {uid}: {e}")

def _fcm_broadcast(db, usuario_ids: List[str],
                   titulo: str, cuerpo: str, datos: dict):
    for uid in usuario_ids:
        try:
            enviar_notificacion(db, uid, titulo, cuerpo, datos)
        except Exception as e:
            print(f"❌ [FCM] {uid}: {e}")

def _notificar_repartidores(db, pedido: Pedido):
    mensaje_ws = {
        "tipo":        "nuevo_pedido",
        "pedido_id":   str(pedido.id),
        "cliente":     pedido.cliente.nombre if pedido.cliente else "",
        "total":       float(pedido.total),
        "tipo_pago":   pedido.tipo_pago,
        "tipo_pedido": "normal",
        "creado_en":   pedido.creado_en.isoformat() if pedido.creado_en else "",
    }
    repartidores = db.query(Repartidor).filter(
        Repartidor.esta_activo == True).all()
    uids = [str(r.usuario_id) for r in repartidores]
    try:
        _run_async(ws_manager.notificar_todos_vendedores(mensaje_ws))
    except Exception as e:
        print(f"❌ [WS] broadcast repartidores: {e}")
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
        "empresa_id":  str(pedido.empresa_id),
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
    _ws_broadcast(
        {"tipo": "pedido_asignado", "pedido_id": str(pedido.id),
         "cliente": pedido.cliente.nombre if pedido.cliente else "",
         "total": float(pedido.total)},
        [aceptado_por_uid],
    )
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
    if not cliente.empresa_id:
        raise HTTPException(400, "Para hacer una reserva debes pertenecer a una empresa.")
    if str(cliente.empresa_id) != empresa_id:
        raise HTTPException(400, "Solo puedes hacer reservas en tu propia empresa.")
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
        raise HTTPException(404, "No hay vendedores disponibles para esta empresa.")
    return row["empresa_nombre"]


# ══════════════════════════════════════════════════════════
#  HELPER — liberar stock de reserva
# ══════════════════════════════════════════════════════════
def _liberar_stock_reserva(db, pedido: Pedido, vendedor_id, hoy) -> None:
    """Libera las unidades reservadas de vuelta al stock disponible."""
    if pedido.estado not in ("aceptado", "pendiente"):
        return
    for item in pedido.items:
        stock = db.query(StockDiario).filter(
            StockDiario.vendedor_id == vendedor_id,
            StockDiario.producto_id == item.producto_id,
            StockDiario.fecha       == hoy,
        ).first()
        if stock and stock.cantidad_reservada > 0:
            stock.cantidad_reservada = max(
                0, stock.cantidad_reservada - item.cantidad)


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

    if datos.tipo == "reserva":
        if not datos.empresa_id:
            raise HTTPException(400, "Una reserva requiere empresa_id.")
        _validar_reserva(db, cliente, datos.empresa_id)

    total, items_data = Decimal("0"), []
    for item in datos.items:
        if item.cantidad <= 0:
            raise HTTPException(400, "Cantidad debe ser > 0.")
        producto = db.query(Producto).filter(
            Producto.id == item.producto_id,
            Producto.esta_activo == True).first()
        if not producto:
            raise HTTPException(404, f"Producto {item.producto_id} no encontrado.")
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

    activo = db.query(Pedido).filter(
        Pedido.repartidor_id == repartidor.id,
        Pedido.estado.in_(["aceptado", "en_camino"]),
    ).first()
    if activo:
        raise HTTPException(400, "Ya tienes un pedido activo.")

    # Lock atómico
    resultado = db.execute(text("""
        UPDATE pedidos
        SET estado      = 'aceptado',
            repartidor_id = :aid,
            aceptado_en = NOW()
        WHERE id     = :pid
          AND estado = 'pendiente'
        RETURNING id
    """), {"aid": str(repartidor.id), "pid": pedido_id}).fetchone()
    if not resultado:
        raise HTTPException(409, "Este pedido ya fue aceptado por otro.")
    db.commit()

    pedido = db.query(Pedido).filter(Pedido.id == pedido_id).first()
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
    pedido = db.query(Pedido).filter(Pedido.id == pedido_id).first()
    if not pedido:
        raise HTTPException(404, "Pedido no encontrado.")
    if str(pedido.repartidor_id) != str(repartidor.id):
        raise HTTPException(403, "No puedes modificar este pedido.")
    pedido.estado = datos.estado
    db.commit()
    if datos.estado == "entregado":
        _notificar_entregado(db, pedido)
    elif datos.estado == "cancelado":
        if pedido.cliente and pedido.cliente.usuario_id:
            _fcm_broadcast(
                db, [str(pedido.cliente.usuario_id)],
                titulo = "❌ Pedido cancelado",
                cuerpo = "Tu pedido fue cancelado por el repartidor. "
                         "Puedes hacer un nuevo pedido cuando quieras.",
                datos  = {"tipo": "pedido_cancelado",
                          "pedido_id": str(pedido.id)},
            )
    return _pedido_dict(pedido)


# ══════════════════════════════════════════════════════════
#  ENDPOINTS VENDEDOR — RESERVAS
#  (cada ruta definida UNA SOLA VEZ)
# ══════════════════════════════════════════════════════════

@router.get("/vendedor/reservas")
def reservas_vendedor(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    """
    Reservas PENDIENTES de las empresas asignadas al vendedor.
    No filtra por vendedor_id porque las pendientes aún no tienen uno.
    """
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id).first()
    if not vendedor:
        return []

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
    """Reservas aceptadas por este vendedor (no finalizadas)."""
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id).first()
    if not vendedor:
        return []

    pedidos = db.query(Pedido).filter(
        Pedido.vendedor_id == vendedor.id,
        Pedido.tipo        == "reserva",
        Pedido.estado      == "aceptado",
    ).order_by(Pedido.aceptado_en.desc()).all()

    # Devuelve lista para que el panel de empresa pueda mostrar varias
    return [_pedido_dict(p) for p in pedidos]


@router.post("/{pedido_id}/aceptar-reserva")
def aceptar_reserva_vendedor(
    pedido_id: str,
    db:        Session = Depends(get_db),
    usuario:   Usuario = Depends(requiere_vendedor),
):
    """
    Vendedor acepta una reserva de su ruta.
    Valida stock disponible (cantidad - cantidad_reservada) por producto.
    """
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id).first()
    if not vendedor:
        raise HTTPException(404, "Vendedor no encontrado.")

    # Cargar reserva — solo pendientes
    pedido = db.query(Pedido).filter(
        Pedido.id     == pedido_id,
        Pedido.tipo   == "reserva",
        Pedido.estado == "pendiente",
    ).first()
    if not pedido:
        raise HTTPException(404, "Reserva no encontrada o ya fue procesada.")

    # Verificar que la empresa esté en la ruta del vendedor
    en_ruta = db.execute(text("""
        SELECT 1
        FROM ruta_empresas re
        JOIN ruta_asignaciones ra ON ra.ruta_id = re.ruta_id
            AND ra.vendedor_id = :vid
            AND ra.esta_activa = TRUE
        JOIN rutas r ON r.id = re.ruta_id AND r.esta_activa = TRUE
        WHERE re.empresa_id = :eid
        LIMIT 1
    """), {"vid": str(vendedor.id), "eid": str(pedido.empresa_id)}).first()
    if not en_ruta:
        raise HTTPException(403, "Esta empresa no pertenece a tu ruta.")

    # Verificar que la empresa no haya sido visitada ya hoy
    empresa_visitada = db.execute(text("""
        SELECT 1
        FROM visitas_verificadas vv
        JOIN sesiones_ruta sr ON sr.id = vv.sesion_id
        WHERE vv.empresa_id  = :eid
          AND vv.vendedor_id = :vid
          AND sr.fecha       = :hoy
          AND vv.es_valida   = TRUE
        LIMIT 1
    """), {
        "eid": str(pedido.empresa_id),
        "vid": str(vendedor.id),
        "hoy": str(date.today()),
    }).first()
    if empresa_visitada:
        raise HTTPException(
            400,
            "Ya visitaste esta empresa hoy. No puedes aceptar más reservas de ella.")

    hoy = date.today()

    # ── Validar stock disponible por producto ─────────────────────────────
    errores_stock = []
    for item in pedido.items:
        stock = db.query(StockDiario).filter(
            StockDiario.vendedor_id == vendedor.id,
            StockDiario.producto_id == item.producto_id,
            StockDiario.fecha       == hoy,
        ).first()

        nombre_producto = item.producto.nombre if item.producto else str(item.producto_id)

        if not stock or stock.cantidad == 0:
            errores_stock.append(
                f"Sin stock de '{nombre_producto}'")
            continue

        # Disponible real = total - ya reservado en otras reservas
        disponible = stock.cantidad - stock.cantidad_reservada
        if disponible < item.cantidad:
            errores_stock.append(
                f"'{nombre_producto}': disponible {disponible}, "
                f"solicitado {item.cantidad}")

    if errores_stock:
        raise HTTPException(
            400,
            "Stock insuficiente:\n" + "\n".join(errores_stock))

    # ── Reservar unidades en stock (atómico con el UPDATE del pedido) ─────
    for item in pedido.items:
        stock = db.query(StockDiario).filter(
            StockDiario.vendedor_id == vendedor.id,
            StockDiario.producto_id == item.producto_id,
            StockDiario.fecha       == hoy,
        ).first()
        stock.cantidad_reservada += item.cantidad
    db.flush()  # aplicar cambios de stock antes del UPDATE del pedido

    # ── Lock atómico: solo si sigue pendiente ────────────────────────────
    resultado = db.execute(text("""
        UPDATE pedidos
        SET estado      = 'aceptado',
            vendedor_id = :vid,
            aceptado_en = NOW()
        WHERE id     = :pid
          AND estado = 'pendiente'
        RETURNING id
    """), {"vid": str(vendedor.id), "pid": pedido_id}).fetchone()

    if not resultado:
        # Otro vendedor lo tomó justo antes — revertir stock
        db.rollback()
        raise HTTPException(409, "Esta reserva acaba de ser aceptada por otro vendedor.")

    db.commit()

    # Recargar con relaciones
    pedido = db.query(Pedido).filter(Pedido.id == pedido_id).first()

    # ── Notificaciones ────────────────────────────────────────────────────
    # Al cliente
    if pedido.cliente and pedido.cliente.usuario_id:
        _fcm_broadcast(
            db, [str(pedido.cliente.usuario_id)],
            titulo = "✅ ¡Reserva aceptada!",
            cuerpo = f"{vendedor.nombre_completo} ha aceptado tu reserva.",
            datos  = {"tipo": "reserva_aceptada", "pedido_id": str(pedido.id)},
        )

    # Al propio vendedor vía WS — incluye empresa_id para que el mapa
    # invalide reservasEmpresaProvider(empresaId) en tiempo real
    try:
        import asyncio, threading
        mensaje_ws = {
            "tipo":       "reserva_aceptada_propia",
            "pedido_id":  str(pedido.id),
            "empresa_id": str(pedido.empresa_id) if pedido.empresa_id else None,
        }
        def _enviar_ws():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    ws_manager.notificar_vendedor(
                        str(vendedor.usuario_id), mensaje_ws))
            finally:
                loop.close()
        threading.Thread(target=_enviar_ws, daemon=True).start()
    except Exception as e:
        print(f"❌ [WS] Error notificando reserva aceptada: {e}")

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
        raise HTTPException(400, "Estado inválido. Use 'entregado' o 'cancelado'.")

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
    if str(pedido.vendedor_id) != str(vendedor.id):
        raise HTTPException(403, "No puedes modificar esta reserva.")
    if pedido.estado not in ["aceptado", "pendiente"]:
        raise HTTPException(400, f"La reserva ya está '{pedido.estado}'.")

    pedido.estado = datos.estado
    db.commit()

    if pedido.cliente and pedido.cliente.usuario_id:
        if datos.estado == "entregado":
            _fcm_broadcast(
                db, [str(pedido.cliente.usuario_id)],
                titulo = "🎉 ¡Reserva entregada!",
                cuerpo = "Tu reserva fue entregada. ¡Gracias!",
                datos  = {"tipo": "reserva_entregada", "pedido_id": str(pedido.id)},
            )
        else:
            _fcm_broadcast(
                db, [str(pedido.cliente.usuario_id)],
                titulo = "❌ Reserva cancelada",
                cuerpo = f"Tu reserva en {pedido.empresa.nombre if pedido.empresa else 'la empresa'} fue cancelada.",
                datos  = {"tipo": "reserva_cancelada", "pedido_id": str(pedido.id)},
            )
    return _pedido_dict(pedido)


# ══════════════════════════════════════════════════════════
#  GET /pedidos/reservas-empresa/{empresa_id}
#  Reservas activas (aceptadas por ESTE vendedor) de una empresa
#  + reservas pendientes de esa empresa (para el panel del mapa)
# ══════════════════════════════════════════════════════════
@router.get("/reservas-empresa/{empresa_id}")
def reservas_por_empresa(
    empresa_id: str,
    db:         Session = Depends(get_db),
    usuario:    Usuario = Depends(requiere_vendedor),
):
    """
    Devuelve las reservas que el vendedor debe gestionar al llegar a la empresa:
    - Las que él mismo aceptó (estado=aceptado, vendedor_id=él)
    - NO incluye pendientes de otros (esas se aceptan desde la pestaña Reservas)
    """
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id).first()
    if not vendedor:
        raise HTTPException(404, "Vendedor no encontrado.")

    pedidos = db.query(Pedido).filter(
        Pedido.empresa_id  == empresa_id,
        Pedido.vendedor_id == vendedor.id,      # solo las suyas
        Pedido.tipo        == "reserva",
        Pedido.estado      == "aceptado",        # solo aceptadas (listas para entregar)
    ).order_by(Pedido.creado_en.desc()).all()

    return [_pedido_dict(p) for p in pedidos]


# ══════════════════════════════════════════════════════════
#  POST /pedidos/{id}/liberar-reserva
# ══════════════════════════════════════════════════════════
@router.post("/{pedido_id}/liberar-reserva")
def liberar_reserva(
    pedido_id: str,
    db:        Session = Depends(get_db),
    usuario:   Usuario = Depends(requiere_vendedor),
):
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id).first()
    if not vendedor:
        raise HTTPException(404, "Vendedor no encontrado.")

    pedido = db.query(Pedido).filter(
        Pedido.id          == pedido_id,
        Pedido.tipo        == "reserva",
        Pedido.vendedor_id == vendedor.id,
    ).first()
    if not pedido:
        raise HTTPException(404, "Reserva no encontrada.")
    if pedido.estado not in ("aceptado", "pendiente"):
        raise HTTPException(400, "No se puede liberar esta reserva.")

    hoy = date.today()
    _liberar_stock_reserva(db, pedido, vendedor.id, hoy)
    pedido.estado = "cancelado"
    db.commit()

    if pedido.cliente and pedido.cliente.usuario_id:
        _fcm_broadcast(
            db, [str(pedido.cliente.usuario_id)],
            titulo = "❌ Reserva cancelada",
            cuerpo = "Tu reserva fue cancelada por el vendedor.",
            datos  = {"tipo": "reserva_cancelada", "pedido_id": str(pedido.id)},
        )

    # Notificar al vendedor para que el mapa actualice el badge
    try:
        import asyncio, threading
        mensaje_ws = {
            "tipo":       "reserva_liberada",
            "pedido_id":  str(pedido.id),
            "empresa_id": str(pedido.empresa_id) if pedido.empresa_id else None,
        }
        def _enviar_ws():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    ws_manager.notificar_vendedor(
                        str(vendedor.usuario_id), mensaje_ws))
            finally:
                loop.close()
        threading.Thread(target=_enviar_ws, daemon=True).start()
    except Exception as e:
        print(f"❌ [WS] Error notificando liberación: {e}")

    return _pedido_dict(pedido)


# ══════════════════════════════════════════════════════════
#  POST /pedidos/{id}/entregar-reserva
#  Marca entregada y descuenta del stock real
# ══════════════════════════════════════════════════════════
@router.post("/{pedido_id}/entregar-reserva")
def entregar_reserva(
    pedido_id: str,
    db:        Session = Depends(get_db),
    usuario:   Usuario = Depends(requiere_vendedor),
):
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id).first()
    if not vendedor:
        raise HTTPException(404, "Vendedor no encontrado.")

    pedido = db.query(Pedido).filter(
        Pedido.id          == pedido_id,
        Pedido.tipo        == "reserva",
        Pedido.vendedor_id == vendedor.id,
    ).first()
    if not pedido:
        raise HTTPException(404, "Reserva no encontrada.")
    if pedido.estado != "aceptado":
        raise HTTPException(400, "La reserva debe estar aceptada para entregarla.")

    hoy = date.today()
    for item in pedido.items:
        stock = db.query(StockDiario).filter(
            StockDiario.vendedor_id == vendedor.id,
            StockDiario.producto_id == item.producto_id,
            StockDiario.fecha       == hoy,
        ).first()
        if stock:
            stock.cantidad_reservada = max(
                0, stock.cantidad_reservada - item.cantidad)
            stock.cantidad = max(0, stock.cantidad - item.cantidad)

    pedido.estado = "entregado"
    db.commit()

    _notificar_entregado(db, pedido)

    # Notificar al vendedor para actualizar badge del mapa
    try:
        import asyncio, threading
        mensaje_ws = {
            "tipo":       "reserva_entregada",
            "pedido_id":  str(pedido.id),
            "empresa_id": str(pedido.empresa_id) if pedido.empresa_id else None,
        }
        def _enviar_ws():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    ws_manager.notificar_vendedor(
                        str(vendedor.usuario_id), mensaje_ws))
            finally:
                loop.close()
        threading.Thread(target=_enviar_ws, daemon=True).start()
    except Exception as e:
        print(f"❌ [WS] Error notificando entrega: {e}")

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
        Pedido.estado.in_(["entregado", "cancelado", "aceptado"]),
        func.date(Pedido.creado_en) >= desde,
        func.date(Pedido.creado_en) <= hasta,
    ).order_by(Pedido.creado_en.desc()).all()
    return [_pedido_dict(p) for p in pedidos]


# ══════════════════════════════════════════════════════════
#  TIEMPO ESTIMADO (OSRM)
# ══════════════════════════════════════════════════════════

@router.get("/{pedido_id}/tiempo-estimado")
def tiempo_estimado(
    pedido_id: str,
    lat_rep:   float = Query(...),
    lng_rep:   float = Query(...),
    db:        Session = Depends(get_db),
    usuario:   Usuario = Depends(get_usuario_actual),
):
    import httpx
    pedido = db.query(Pedido).filter(Pedido.id == pedido_id).first()
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
                return {
                    "minutos":          round(routes[0]["duration"] / 60, 1),
                    "distancia_metros": round(routes[0]["distance"]),
                }
    except Exception as e:
        print(f"❌ OSRM tiempo: {e}")
    return {"minutos": None, "distancia_metros": None}