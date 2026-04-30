from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID

class DetalleVentaInput(BaseModel):
    producto_id: UUID
    cantidad:    int
    precio_unitario: float

class VentaCrear(BaseModel):
    cliente_id:  Optional[UUID] = None   # None si es contado
    reserva_id:  Optional[str] = None 
    tipo:        str                      # "contado" o "credito"
    detalle:     List[DetalleVentaInput]
    notas:       Optional[str] = None

class VentaOutput(BaseModel):
    id:              UUID
    tipo:            str
    monto_total:     float
    monto_pagado:    float
    monto_pendiente: float
    estado:          str
    fecha_venta:     str
    cliente:         Optional[str] = None  # nombre del cliente
    vendedor:        str

    model_config = {"from_attributes": True}