# app/routers/vendedores.py
from fastapi      import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic     import BaseModel
from typing       import Optional

from app.database            import get_db
from app.models.vendedor     import Vendedor
from app.models.usuario      import Usuario
from app.core.dependencies   import requiere_vendedor

router = APIRouter(prefix="/vendedores", tags=["Vendedores"])


# ── Schemas ───────────────────────────────────────────────
class PerfilVendedorResponse(BaseModel):
    id:             str
    nombre:         str
    telefono:       Optional[str]
    nombre_usuario: str
    rol:            str


class ActualizarPerfilRequest(BaseModel):
    nombre:   Optional[str] = None
    telefono: Optional[str] = None


class CambiarContrasenaRequest(BaseModel):
    contrasena_actual: str
    contrasena_nueva:  str


# ── GET /vendedores/mi-perfil ─────────────────────────────
@router.get("/mi-perfil", response_model=PerfilVendedorResponse)
def obtener_mi_perfil(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id
    ).first()

    if not vendedor:
        raise HTTPException(
            status_code=404,
            detail="Perfil de vendedor no encontrado"
        )

    return PerfilVendedorResponse(
        id             = str(vendedor.id),
        nombre         = vendedor.nombre_completo,
        telefono       = vendedor.telefono,
        nombre_usuario = usuario.nombre_usuario,
        rol            = usuario.rol,
    )


# ── PUT /vendedores/mi-perfil ─────────────────────────────
@router.put("/mi-perfil")
def actualizar_mi_perfil(
    datos:   ActualizarPerfilRequest,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id
    ).first()

    if not vendedor:
        raise HTTPException(
            status_code=404,
            detail="Perfil de vendedor no encontrado"
        )

    if datos.nombre   is not None:
        vendedor.nombre_completo   = datos.nombre
    if datos.telefono is not None:
        vendedor.telefono = datos.telefono

    db.commit()
    db.refresh(vendedor)
    return {"mensaje": "Perfil actualizado correctamente"}


# ── PUT /vendedores/mi-perfil/contrasena ──────────────────
@router.put("/mi-perfil/contrasena")
def cambiar_contrasena(
    datos:   CambiarContrasenaRequest,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    from app.core.security import verificar_password, hashear_password

    if not verificar_password(datos.contrasena_actual, usuario.contrasena_hash):
        raise HTTPException(
            status_code=400,
            detail="La contraseña actual es incorrecta"
        )

    if len(datos.contrasena_nueva) < 6:
        raise HTTPException(
            status_code=400,
            detail="La contraseña nueva debe tener al menos 6 caracteres"
        )

    usuario.contrasena_hash = hashear_password(datos.contrasena_nueva)
    db.commit()
    return {"mensaje": "Contraseña actualizada correctamente"}