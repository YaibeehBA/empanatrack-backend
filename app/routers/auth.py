from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.usuario import Usuario
from app.models.vendedor import Vendedor
from app.core.security import verificar_contrasena, crear_token
from app.schemas.auth import LoginInput, TokenOutput

router = APIRouter(prefix="/auth", tags=["Autenticación"])

@router.post("/login", response_model=TokenOutput)
def login(datos: LoginInput, db: Session = Depends(get_db)):
    # Buscar usuario por nombre
    usuario = db.query(Usuario).filter(
        Usuario.nombre_usuario == datos.nombre_usuario,
        Usuario.esta_activo    == True
    ).first()

    if not usuario or not verificar_contrasena(datos.contrasena, usuario.contrasena_hash):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas.")

    # Obtener el nombre a mostrar según el rol
    nombre = usuario.nombre_usuario
    if usuario.rol == "vendedor" and usuario.vendedor:
        nombre = usuario.vendedor.nombre_completo
    elif usuario.rol == "cliente" and usuario.cliente:
        nombre = usuario.cliente.nombre

    token = crear_token({"sub": str(usuario.id), "rol": usuario.rol})
    return TokenOutput(access_token=token, rol=usuario.rol, nombre=nombre)