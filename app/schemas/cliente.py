from pydantic import BaseModel, EmailStr
from typing import Optional
from uuid import UUID

class ClienteCrear(BaseModel):
    cedula:     str
    nombre:     str
    correo:     Optional[EmailStr] = None
    telefono:   Optional[str]      = None
    empresa_id: Optional[UUID]     = None

class ClienteOutput(BaseModel):
    id:         UUID
    cedula:     str
    nombre:     str
    correo:     Optional[str]  = None
    telefono:   Optional[str]  = None
    empresa:    Optional[str]  = None   # nombre de la empresa
    saldo_actual: float        = 0.0

    model_config = {"from_attributes": True}