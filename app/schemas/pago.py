from pydantic import BaseModel
from typing import Optional
from uuid import UUID

class PagoCrear(BaseModel):
    cliente_id: UUID
    venta_id:   Optional[UUID] = None   # None si es adelanto general
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

    model_config = {"from_attributes": True}