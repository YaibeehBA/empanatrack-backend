from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.usuario import Usuario
from app.models.vendedor import Vendedor
from app.core.security import verificar_contrasena, crear_token
from app.schemas.auth import LoginInput, TokenOutput

from app.models.cliente import Cliente
from app.models.empresa import Empresa
from app.core.security  import hashear_contrasena

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



class RegistroClientePublico(BaseModel):
    cedula:         str
    nombre:         str
    correo:         Optional[str] = None
    telefono:       Optional[str] = None
    nombre_usuario: str
    contrasena:     str

@router.post("/registro")
def registro_cliente(
    datos: RegistroClientePublico,
    db:    Session = Depends(get_db)
):
    # Verificar cédula única
    if db.query(Cliente).filter(
        Cliente.cedula == datos.cedula
    ).first():
        raise HTTPException(
            status_code=400,
            detail="Ya existe un cliente con esa cédula."
        )

    # Verificar usuario único
    if db.query(Usuario).filter(
        Usuario.nombre_usuario == datos.nombre_usuario
    ).first():
        raise HTTPException(
            status_code=400,
            detail=f"El usuario '{datos.nombre_usuario}' ya está en uso."
        )

    # Verificar correo único si se proporcionó
    if datos.correo:
        if db.query(Usuario).filter(
            Usuario.correo == datos.correo
        ).first():
            raise HTTPException(
                status_code=400,
                detail="El correo ya está registrado."
            )

    if len(datos.contrasena) < 6:
        raise HTTPException(
            status_code=400,
            detail="La contraseña debe tener al menos 6 caracteres."
        )

    # Crear usuario
    nuevo_usuario = Usuario(
        nombre_usuario  = datos.nombre_usuario,
        correo          = datos.correo,
        contrasena_hash = hashear_contrasena(datos.contrasena),
        rol             = "cliente",
    )
    db.add(nuevo_usuario)
    db.flush()

    # Crear cliente vinculado
    cliente = Cliente(
        usuario_id = nuevo_usuario.id,
        cedula     = datos.cedula,
        nombre     = datos.nombre,
        correo     = datos.correo,
        telefono   = datos.telefono,
    )
    db.add(cliente)
    db.commit()

    return {
        "mensaje":  "Registro exitoso. Ya puedes iniciar sesión.",
        "usuario":  datos.nombre_usuario,
        "nombre":   datos.nombre,
    }