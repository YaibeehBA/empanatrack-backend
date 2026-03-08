from uuid import UUID
from fastapi import APIRouter, Depends, Query
from psycopg2 import Date
from sqlalchemy.orm import Session
from datetime import date, timedelta
from typing import List, Optional
from datetime import date
from app.database import get_db
from app.models.venta import Venta
from app.models.vendedor import Vendedor
from app.schemas.venta import VentaCrear, VentaOutput
from app.services.venta_service import registrar_venta
from app.core.dependencies import get_usuario_actual, requiere_vendedor
from app.models.usuario import Usuario

router = APIRouter(prefix="/ventas", tags=["Ventas"])

@router.post("/", response_model=VentaOutput)
def crear_venta(
    datos:   VentaCrear,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor)
):
    vendedor = db.query(Vendedor).filter(Vendedor.usuario_id == usuario.id).first()
    venta    = registrar_venta(db, datos, vendedor.id)
    
    # ======================================================
    # NUEVO: Enviar notificación push al cliente si es crédito
    # ======================================================
    if venta.tipo == "credito" and venta.cliente_id:
        from app.services.notificaciones import enviar_notificacion
        from app.models.cliente import Cliente as ClienteModel
        from decimal import Decimal
        from sqlalchemy import func

        cliente_obj = db.query(ClienteModel).filter(
            ClienteModel.id == venta.cliente_id
        ).first()

        if cliente_obj and cliente_obj.usuario_id:
            # Calcular deuda TOTAL del cliente (no solo esta venta)
            deuda_total = db.query(
                func.sum(Venta.monto_pendiente)
            ).filter(
                Venta.cliente_id == venta.cliente_id,
                Venta.estado.in_(["pendiente", "parcial"])
            ).scalar() or Decimal("0.00")

            print(f"\n{'='*50}")
            print(f"🔔 [VENTAS] Intentando enviar notificacion...")
            print(f"   venta_id:    {venta.id}")
            print(f"   cliente_id:  {venta.cliente_id}")
            print(f"   usuario_id:  {cliente_obj.usuario_id}")
            print(f"   monto_total: ${venta.monto_total:.2f}")
            print(f"   deuda_total: ${deuda_total:.2f}")
            
            # Construir detalle de productos (asumiendo que venta tiene relación con detalle)
            detalle_texto = ", ".join([
                f"{item.cantidad}x {item.producto.nombre}"
                for item in venta.detalle  # Asegúrate que venta.detalle exista
            ])

            resultado = enviar_notificacion(
                db         = db,
                usuario_id = cliente_obj.usuario_id,
                titulo     = "🫓 Nueva compra registrada",
                cuerpo     = (
                    f"Se registró una compra de "
                    f"${venta.monto_total:.2f}. "
                    f"Tu deuda total es ${deuda_total:.2f}. "
                    f"Detalle: {detalle_texto}"
                ),
                datos = {
                    "tipo":        "venta_fiado",
                    "venta_id":    str(venta.id),
                    "monto":       str(venta.monto_total),
                    "deuda_total": str(deuda_total),
                },
            )
            print(f"   Resultado FCM: {resultado}")
            print(f"{'='*50}\n")
        else:
            print(f"\n{'='*50}")
            print(f"❌ [VENTAS] No se envio notificacion:")
            print(f"   venta_id:    {venta.id}")
            print(f"   cliente_id:  {venta.cliente_id}")
            print(f"   cliente_obj: {cliente_obj}")
            print(f"   usuario_id:  {cliente_obj.usuario_id if cliente_obj else 'N/A'}")
            print(f"   motivo:      {'Cliente no encontrado' if not cliente_obj else 'Cliente sin usuario_id'}")
            print(f"{'='*50}\n")
    # ======================================================

    return VentaOutput(
        id              = venta.id,
        tipo            = venta.tipo,
        monto_total     = float(venta.monto_total),
        monto_pagado    = float(venta.monto_pagado),
        monto_pendiente = float(venta.monto_pendiente),
        estado          = venta.estado,
        fecha_venta     = str(venta.fecha_venta),
        cliente         = venta.cliente.nombre if venta.cliente else None,
        vendedor        = vendedor.nombre_completo,
    )


@router.get("/", response_model=List[VentaOutput])
def listar_ventas(
    fecha:      Optional[date] = Query(default=None),
    cliente_id: Optional[UUID] = Query(default=None),
    estado:     Optional[str]  = Query(default=None),
    db:         Session        = Depends(get_db),
    usuario:    Usuario        = Depends(requiere_vendedor)
):
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id
    ).first()

    # Si es admin sin vendedor asociado, devolver lista vacía
    # (el admin usa sus propios endpoints en /admin)
    if not vendedor:
        return []

    # SIEMPRE filtrar por el vendedor autenticado
    query = db.query(Venta).filter(Venta.vendedor_id == vendedor.id)

    if fecha:
        query = query.filter(
            Venta.fecha_venta.cast(Date) == fecha
        )
    if cliente_id:
        query = query.filter(Venta.cliente_id == cliente_id)
    if estado:
        query = query.filter(Venta.estado == estado)

    ventas = query.order_by(Venta.fecha_venta.desc()).all()

    return [
        VentaOutput(
            id              = v.id,
            tipo            = v.tipo,
            monto_total     = float(v.monto_total),
            monto_pagado    = float(v.monto_pagado),
            monto_pendiente = float(v.monto_pendiente),
            estado          = v.estado,
            fecha_venta     = str(v.fecha_venta),
            cliente         = v.cliente.nombre if v.cliente else None,
            vendedor        = vendedor.nombre_completo,
        ) for v in ventas
    ]


@router.get("/historial", response_model=List[VentaOutput])
def historial_ventas(
    periodo: str     = Query(default="hoy"),
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor)
):
    from datetime import date, timedelta
    from sqlalchemy import text, cast, Date as SADate

    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id
    ).first()
    if not vendedor:
        return []

    hoy = date.today()
    if periodo == "hoy":
        desde, hasta = hoy, hoy
    elif periodo == "ayer":
        desde = hoy - timedelta(days=1)
        hasta = desde
    elif periodo == "semana":
        desde = hoy - timedelta(days=6)
        hasta = hoy
    elif periodo == "mes":
        desde = hoy.replace(day=1)
        hasta = hoy
    else:
        desde, hasta = hoy, hoy

    ventas = db.query(Venta).filter(
        Venta.vendedor_id == vendedor.id,
        cast(Venta.fecha_venta, SADate) >= desde,
        cast(Venta.fecha_venta, SADate) <= hasta,
    ).order_by(Venta.fecha_venta.desc()).all()

    return [
        VentaOutput(
            id              = v.id,
            tipo            = v.tipo,
            monto_total     = float(v.monto_total),
            monto_pagado    = float(v.monto_pagado),
            monto_pendiente = float(v.monto_pendiente),
            estado          = v.estado,
            fecha_venta     = str(v.fecha_venta),
            cliente         = v.cliente.nombre if v.cliente else None,
            vendedor        = vendedor.nombre_completo,
        ) for v in ventas
    ]