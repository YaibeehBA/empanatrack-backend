from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional
from uuid import UUID
from app.utils.validators import validar_cedula_ecuador, validar_telefono_ecuador


class ClienteCrear(BaseModel):
    cedula:     str
    nombre:     str
    correo:     Optional[EmailStr] = None
    telefono:   Optional[str]      = None
    empresa_id: Optional[UUID]     = None

    @field_validator('cedula')
    @classmethod
    def cedula_valida(cls, v):
        return validar_cedula_ecuador(v)

    @field_validator('telefono')
    @classmethod
    def telefono_valido(cls, v):
        if v is None:
            return v
        return validar_telefono_ecuador(v)


class ClienteOutput(BaseModel):
    id:           UUID
    cedula:       str
    nombre:       str
    correo:       Optional[str] = None
    telefono:     Optional[str] = None
    empresa:      Optional[str] = None
    saldo_actual: float         = 0.0

    model_config = {"from_attributes": True}


class ClienteCrearCompleto(BaseModel):
    cedula:         str
    nombre:         str
    correo:         Optional[str]  = None
    telefono:       Optional[str]  = None
    empresa_id:     Optional[UUID] = None
    nombre_usuario: Optional[str]  = None
    contrasena:     Optional[str]  = None

    @field_validator('cedula')
    @classmethod
    def cedula_valida(cls, v):
        return validar_cedula_ecuador(v)

    @field_validator('telefono')
    @classmethod
    def telefono_valido(cls, v):
        if v is None:
            return v
        return validar_telefono_ecuador(v)


class ClienteCrearOutput(BaseModel):
    id:           UUID
    cedula:       str
    nombre:       str
    correo:       Optional[str] = None
    telefono:     Optional[str] = None
    empresa:      Optional[str] = None
    tiene_acceso: bool          = False

    model_config = {"from_attributes": True}