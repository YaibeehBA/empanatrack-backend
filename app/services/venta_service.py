from sqlalchemy.orm import Session
from sqlalchemy import text
from fastapi import HTTPException
from app.models.venta import Venta, DetalleVenta
from app.schemas.venta import VentaCrear
from decimal import Decimal
import uuid

def registrar_venta(db: Session, datos: VentaCrear, vendedor_id: uuid.UUID) -> Venta:
    # Validar que ventas a crédito tengan cliente
    if datos.tipo == "credito" and not datos.cliente_id:
        raise HTTPException(status_code=400,
                            detail="Las ventas a crédito requieren un cliente.")

    # Calcular montos desde el detalle
    monto_total = sum(
        Decimal(str(item.precio_unitario)) * item.cantidad
        for item in datos.detalle
    )

    # En ventas de contado se cobra todo en el momento
    monto_pagado    = monto_total if datos.tipo == "contado" else Decimal("0.00")
    monto_pendiente = monto_total - monto_pagado
    estado          = "pagado"   if datos.tipo == "contado" else "pendiente"

    # Crear la venta
    venta = Venta(
        vendedor_id     = vendedor_id,
        cliente_id      = datos.cliente_id,
        tipo            = datos.tipo,
        monto_total     = monto_total,
        monto_pagado    = monto_pagado,
        monto_pendiente = monto_pendiente,
        estado          = estado,
        notas           = datos.notas,
    )
    db.add(venta)
    db.flush()  # Obtener el id de la venta antes del commit

    # Crear el detalle de productos
    for item in datos.detalle:
        subtotal = Decimal(str(item.precio_unitario)) * item.cantidad
        detalle  = DetalleVenta(
            venta_id        = venta.id,
            producto_id     = item.producto_id,
            cantidad        = item.cantidad,
            precio_unitario = Decimal(str(item.precio_unitario)),
            subtotal        = subtotal,
        )
        db.add(detalle)

    db.commit()
    db.refresh(venta)
    return venta


def obtener_saldo_cliente(db: Session, cliente_id: uuid.UUID) -> Decimal:
    """Llama a la función PostgreSQL que calcula el saldo real."""
    resultado = db.execute(
        text("SELECT calcular_saldo_cliente(:cliente_id)"),
        {"cliente_id": str(cliente_id)}
    ).scalar()
    return resultado or Decimal("0.00")