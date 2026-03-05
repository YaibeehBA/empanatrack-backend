from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel
from app.database import get_db
from app.models.pago import Pago
from app.models.cliente import Cliente
from app.models.vendedor import Vendedor
from app.models.venta import Venta
from app.core.dependencies import get_usuario_actual, requiere_vendedor
from app.models.usuario import Usuario


class PagoCrear(BaseModel):
    cliente_id: UUID
    venta_id:   Optional[UUID] = None  # None = adelanto general
    monto:      float
    tipo:       str = "efectivo"
    notas:      Optional[str] = None


class PagoOutput(BaseModel):
    id:         UUID
    monto:      float
    tipo:       str
    fecha_pago: str
    cliente:    str
    vendedor:   str
    venta_id:   Optional[UUID] = None

    model_config = {"from_attributes": True}


router = APIRouter(prefix="/pagos", tags=["Pagos"])


@router.post("/", response_model=PagoOutput)
def registrar_pago(
    datos:   PagoCrear,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual)   
):
    # Verificar que el cliente existe
    cliente = db.query(Cliente).filter(
        Cliente.id          == datos.cliente_id,
        Cliente.esta_activo == True
    ).first()
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")

    # Obtener el vendedor según el rol
    if usuario.rol == "administrador":
        # El admin usa el vendedor que más ventas tiene con ese cliente
        # o el primero disponible
        from app.models.venta import Venta as VentaModel
        venta_reciente = db.query(VentaModel).filter(
            VentaModel.cliente_id == datos.cliente_id
        ).order_by(VentaModel.fecha_venta.desc()).first()

        if venta_reciente:
            vendedor = db.query(Vendedor).filter(
                Vendedor.id == venta_reciente.vendedor_id
            ).first()
        else:
            vendedor = db.query(Vendedor).filter(
                Vendedor.esta_activo == True
            ).first()
    else:
        vendedor = db.query(Vendedor).filter(
            Vendedor.usuario_id == usuario.id
        ).first()

    if not vendedor:
        raise HTTPException(status_code=404, detail="Vendedor no encontrado.")

    # Verificar venta si se especificó
    if datos.venta_id:
        venta = db.query(Venta).filter(
            Venta.id         == datos.venta_id,
            Venta.cliente_id == datos.cliente_id,
            Venta.estado     != "pagado"
        ).first()
        if not venta:
            raise HTTPException(
                status_code=404,
                detail="Venta no encontrada o ya está pagada."
            )

    # Verificar que el monto no supere el saldo
    saldo_actual = db.execute(
        text("SELECT calcular_saldo_cliente(:cid)"),
        {"cid": str(datos.cliente_id)}
    ).scalar() or 0

    if datos.monto > float(saldo_actual):
        raise HTTPException(
            status_code=400,
            detail=f"El monto ${datos.monto:.2f} supera el saldo del cliente ${float(saldo_actual):.2f}."
        )

    # Registrar el pago
    pago = Pago(
        cliente_id  = datos.cliente_id,
        vendedor_id = vendedor.id,
        venta_id    = datos.venta_id,
        monto       = datos.monto,
        tipo        = datos.tipo,
        notas       = datos.notas,
    )
    db.add(pago)
    
    
    # Si es abono general (sin venta_id), distribuir entre ventas pendientes
    if not datos.venta_id:
        from sqlalchemy import text as sql_text

        ventas_pendientes = db.query(Venta).filter(
            Venta.cliente_id  == datos.cliente_id,
            Venta.vendedor_id == vendedor.id,
            Venta.estado      != 'pagado',
            Venta.tipo        == 'credito',
        ).order_by(Venta.fecha_venta.asc()).all()

        monto_restante = float(datos.monto)
        for v in ventas_pendientes:
            if monto_restante <= 0:
                break
            pendiente = float(v.monto_pendiente)
            if monto_restante >= pendiente:
                v.monto_pagado    = v.monto_total
                v.monto_pendiente = 0
                # Cast explícito al tipo ENUM via SQL raw
                db.execute(
                    sql_text("UPDATE ventas SET estado = 'pagado'::estado_venta WHERE id = :id"),
                    {"id": str(v.id)}
                )
                monto_restante -= pendiente
            else:
                v.monto_pagado    = float(v.monto_pagado) + monto_restante
                v.monto_pendiente = pendiente - monto_restante
                db.execute(
                    sql_text("UPDATE ventas SET estado = 'parcial'::estado_venta WHERE id = :id"),
                    {"id": str(v.id)}
                )
                monto_restante = 0

        db.commit()  # Commit para guardar los cambios en las ventas
   

    db.commit()  # Commit para guardar el pago
    db.refresh(pago)

    return PagoOutput(
        id         = pago.id,
        monto      = float(pago.monto),
        tipo       = pago.tipo,
        fecha_pago = str(pago.fecha_pago),
        cliente    = cliente.nombre,
        vendedor   = vendedor.nombre_completo,
        venta_id   = pago.venta_id,
    )


@router.get("/cliente/{cliente_id}", response_model=List[PagoOutput])
def pagos_de_cliente(
    cliente_id: UUID,
    db:         Session = Depends(get_db),
    usuario:    Usuario = Depends(get_usuario_actual)
):
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id
    ).first()

    pagos = db.query(Pago).filter(
        Pago.cliente_id == cliente_id
    ).order_by(Pago.fecha_pago.desc()).all()

    return [
        PagoOutput(
            id         = p.id,
            monto      = float(p.monto),
            tipo       = p.tipo,
            fecha_pago = str(p.fecha_pago),
            cliente    = p.cliente.nombre,
            vendedor   = p.vendedor.nombre_completo,
            venta_id   = p.venta_id,
        ) for p in pagos
    ]