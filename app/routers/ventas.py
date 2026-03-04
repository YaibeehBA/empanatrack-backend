from uuid import UUID
from fastapi import APIRouter, Depends, Query
from psycopg2 import Date
from sqlalchemy.orm import Session
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