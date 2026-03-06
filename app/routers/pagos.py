from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text as sql_text
from typing import Optional, List
from uuid import UUID
from pydantic import BaseModel
from decimal import Decimal

from app.database import get_db
from app.models.usuario import Usuario
from app.models.cliente import Cliente
from app.models.vendedor import Vendedor
from app.models.pago import Pago
from app.models.venta import Venta
from app.core.dependencies import get_usuario_actual

router = APIRouter(prefix="/pagos", tags=["Pagos"])


# ═══════════════════════════════════════
#  SCHEMAS
# ═══════════════════════════════════════

class PagoCrear(BaseModel):
    cliente_id: UUID
    venta_id:   Optional[UUID] = None
    monto:      float
    tipo:       str
    notas:      Optional[str]  = None


class PagoOutput(BaseModel):
    id:         UUID
    monto:      float
    tipo:       str
    fecha_pago: str
    cliente:    str
    vendedor:   str
    venta_id:   Optional[UUID] = None


# ═══════════════════════════════════════
#  REGISTRAR PAGO
# ═══════════════════════════════════════

@router.post("/", response_model=PagoOutput)
def registrar_pago(
    datos:   PagoCrear,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual)
):
    # ── Verificar cliente ─────────────────────────────
    cliente = db.query(Cliente).filter(
        Cliente.id          == datos.cliente_id,
        Cliente.esta_activo == True
    ).first()
    if not cliente:
        raise HTTPException(
            status_code=404,
            detail="Cliente no encontrado."
        )

    # ── Obtener vendedor según rol ────────────────────
    if usuario.rol == "administrador":
        venta_reciente = db.query(Venta).filter(
            Venta.cliente_id == datos.cliente_id
        ).order_by(Venta.fecha_venta.desc()).first()

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
        raise HTTPException(
            status_code=404,
            detail="Vendedor no encontrado."
        )

    # ── Validar monto ─────────────────────────────────
    if datos.monto <= 0:
        raise HTTPException(
            status_code=400,
            detail="El monto debe ser mayor a 0."
        )

    # ── Verificar venta específica si se indicó ───────
    if datos.venta_id:
        venta = db.query(Venta).filter(
            Venta.id         == datos.venta_id,
            Venta.cliente_id == datos.cliente_id,
            Venta.estado     != 'pagado'
        ).first()
        if not venta:
            raise HTTPException(
                status_code=404,
                detail="Venta no encontrada o ya está pagada."
            )

        # Validar contra el pendiente de ESA venta
        if datos.monto > float(venta.monto_pendiente):
            raise HTTPException(
                status_code=400,
                detail=f"El monto ${datos.monto:.2f} supera el pendiente "
                       f"de esta venta ${float(venta.monto_pendiente):.2f}."
            )
    else:
        # Abono general — validar contra saldo con ESTE vendedor
        saldo_vendedor = db.execute(
            sql_text(
                "SELECT calcular_saldo_cliente_vendedor(:cid, :vid)"
            ),
            {
                "cid": str(datos.cliente_id),
                "vid": str(vendedor.id),
            }
        ).scalar() or 0

        if datos.monto > float(saldo_vendedor):
            raise HTTPException(
                status_code=400,
                detail=f"El monto ${datos.monto:.2f} supera el saldo "
                       f"con este vendedor ${float(saldo_vendedor):.2f}."
            )

    # ── Registrar el pago ─────────────────────────────
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


    # ── Abono general: distribuir entre ventas ────────
    if not datos.venta_id:
        from decimal import Decimal, ROUND_HALF_UP

        ventas_pendientes = db.query(Venta).filter(
            Venta.cliente_id  == datos.cliente_id,
            Venta.vendedor_id == vendedor.id,
            Venta.estado      != 'pagado',
            Venta.tipo        == 'credito',
        ).order_by(Venta.fecha_venta.asc()).all()

        # Usar Decimal para evitar errores de precisión
        monto_restante = Decimal(str(datos.monto)).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

        for v in ventas_pendientes:
            if monto_restante <= 0:
                break

            pendiente = Decimal(str(v.monto_pendiente)).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
            pagado_actual = Decimal(str(v.monto_pagado)).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
            monto_total = Decimal(str(v.monto_total)).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )

            if monto_restante >= pendiente:
                # Venta completamente pagada
                db.execute(
                    sql_text("""
                        UPDATE ventas
                        SET monto_pagado    = monto_total,
                            monto_pendiente = 0,
                            estado          = 'pagado'::estado_venta
                        WHERE id = :id
                    """),
                    {"id": str(v.id)}
                )
                monto_restante -= pendiente
            else:
                # Pago parcial — calcular con precisión
                nuevo_pagado    = (pagado_actual + monto_restante).quantize(
                    Decimal('0.01'), rounding=ROUND_HALF_UP
                )
                nuevo_pendiente = (monto_total - nuevo_pagado).quantize(
                    Decimal('0.01'), rounding=ROUND_HALF_UP
                )
                db.execute(
                    sql_text("""
                        UPDATE ventas
                        SET monto_pagado    = :pagado,
                            monto_pendiente = :pendiente,
                            estado          = 'parcial'::estado_venta
                        WHERE id = :id
                    """),
                    {
                        "id":        str(v.id),
                        "pagado":    float(nuevo_pagado),
                        "pendiente": float(nuevo_pendiente),
                    }
                )
                monto_restante = Decimal('0')

        db.expire_all()
        db.commit()

    return PagoOutput(
        id         = pago.id,
        monto      = float(pago.monto),
        tipo       = pago.tipo,
        fecha_pago = str(pago.fecha_pago),
        cliente    = cliente.nombre,
        vendedor   = vendedor.nombre_completo,
        venta_id   = pago.venta_id,
    )


# ═══════════════════════════════════════
#  LISTAR PAGOS DE UN CLIENTE
# ═══════════════════════════════════════

@router.get("/cliente/{cliente_id}")
def listar_pagos_cliente(
    cliente_id: UUID,
    db:         Session = Depends(get_db),
    usuario:    Usuario = Depends(get_usuario_actual)
):
    pagos = db.query(Pago).filter(
        Pago.cliente_id == cliente_id
    ).order_by(Pago.fecha_pago.desc()).all()

    return [
        {
            "id":         str(p.id),
            "monto":      float(p.monto),
            "tipo":       p.tipo,
            "fecha_pago": str(p.fecha_pago),
            "venta_id":   str(p.venta_id) if p.venta_id else None,
            "notas":      p.notas,
        }
        for p in pagos
    ]