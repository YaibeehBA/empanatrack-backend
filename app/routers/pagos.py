# app/routers/pagos.py
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
    usuario: Usuario = Depends(requiere_vendedor)
):
    # Verificar que el cliente existe
    cliente = db.query(Cliente).filter(
        Cliente.id          == datos.cliente_id,
        Cliente.esta_activo == True
    ).first()
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")

    # Verificar que el vendedor existe
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id
    ).first()
    if not vendedor:
        raise HTTPException(status_code=404, detail="Vendedor no encontrado.")

    # Si se especificó una venta, verificar que pertenece al cliente
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

    # Verificar que el monto no supere lo que debe
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
    db.commit()
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