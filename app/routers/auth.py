# app/routers/auth.py
import random
import string
from datetime        import datetime, timedelta, timezone
from typing          import Optional

from fastapi         import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic        import BaseModel
from sqlalchemy.orm  import Session

from app.database        import get_db
from app.models.usuario  import Usuario
from app.models.vendedor import Vendedor
from app.models.cliente  import Cliente
from app.models.empresa  import Empresa
from app.core.security   import (
    verificar_contrasena,
    crear_token,
    hashear_contrasena,
)
from app.schemas.auth import LoginInput, TokenOutput

router = APIRouter(prefix="/auth", tags=["Autenticación"])


# ══════════════════════════════════════════════════════════
#  LOGIN
# ══════════════════════════════════════════════════════════
@router.post("/login", response_model=TokenOutput)
def login(datos: LoginInput, db: Session = Depends(get_db)):
    usuario = db.query(Usuario).filter(
        Usuario.nombre_usuario == datos.nombre_usuario,
        Usuario.esta_activo    == True,
    ).first()

    if not usuario or not verificar_contrasena(
            datos.contrasena, usuario.contrasena_hash):
        raise HTTPException(
            status_code=401,
            detail="Credenciales incorrectas.",
        )

    nombre = usuario.nombre_usuario
    if usuario.rol == "vendedor" and usuario.vendedor:
        nombre = usuario.vendedor.nombre_completo
    elif usuario.rol == "cliente" and usuario.cliente:
        nombre = usuario.cliente.nombre

    token = crear_token({"sub": str(usuario.id), "rol": usuario.rol})
    return TokenOutput(access_token=token, rol=usuario.rol, nombre=nombre)


# ══════════════════════════════════════════════════════════
#  REGISTRO PÚBLICO DE CLIENTE
# ══════════════════════════════════════════════════════════
class RegistroClientePublico(BaseModel):
    cedula:            str
    nombre:            str
    correo:            Optional[str] = None
    telefono:          Optional[str] = None
    nombre_usuario:    str
    contrasena:        str
    empresa_id:        Optional[str] = None
    empresa_nombre:    Optional[str] = None
    empresa_direccion: Optional[str] = None
    empresa_telefono:  Optional[str] = None


@router.post("/registro")
def registro_cliente(
    datos: RegistroClientePublico,
    db:    Session = Depends(get_db),
):
    # Cédula única
    if db.query(Cliente).filter(
        Cliente.cedula == datos.cedula
    ).first():
        raise HTTPException(
            status_code=400,
            detail="Ya existe un cliente con esa cédula.",
        )

    # Usuario único
    if db.query(Usuario).filter(
        Usuario.nombre_usuario == datos.nombre_usuario
    ).first():
        raise HTTPException(
            status_code=400,
            detail=f"El usuario '{datos.nombre_usuario}' ya está en uso.",
        )

    # Correo único
    if datos.correo:
        if db.query(Usuario).filter(
            Usuario.correo == datos.correo
        ).first():
            raise HTTPException(
                status_code=400,
                detail="El correo ya está registrado.",
            )

    if len(datos.contrasena) < 6:
        raise HTTPException(
            status_code=400,
            detail="La contraseña debe tener al menos 6 caracteres.",
        )

    # ── Resolver empresa ──────────────────────────────────
    empresa_id_final = None

    if datos.empresa_id:
        empresa = db.query(Empresa).filter(
            Empresa.id == datos.empresa_id
        ).first()
        if not empresa:
            raise HTTPException(
                status_code=404,
                detail="La empresa seleccionada no existe.",
            )
        empresa_id_final = empresa.id

    elif datos.empresa_nombre and datos.empresa_nombre.strip():
        empresa_existe = db.query(Empresa).filter(
            Empresa.nombre.ilike(datos.empresa_nombre.strip())
        ).first()
        if empresa_existe:
            empresa_id_final = empresa_existe.id
        else:
            nueva_empresa = Empresa(
                nombre    = datos.empresa_nombre.strip(),
                direccion = datos.empresa_direccion.strip()
                    if datos.empresa_direccion
                    and datos.empresa_direccion.strip() else None,
                telefono  = datos.empresa_telefono.strip()
                    if datos.empresa_telefono
                    and datos.empresa_telefono.strip() else None,
            )
            db.add(nueva_empresa)
            db.flush()
            empresa_id_final = nueva_empresa.id

    # ── Crear usuario ─────────────────────────────────────
    nuevo_usuario = Usuario(
        nombre_usuario  = datos.nombre_usuario,
        correo          = datos.correo,
        contrasena_hash = hashear_contrasena(datos.contrasena),
        rol             = "cliente",
    )
    db.add(nuevo_usuario)
    db.flush()

    # ── Crear cliente ─────────────────────────────────────
    cliente = Cliente(
        usuario_id = nuevo_usuario.id,
        cedula     = datos.cedula,
        nombre     = datos.nombre,
        correo     = datos.correo,
        telefono   = datos.telefono,
        empresa_id = empresa_id_final,
    )
    db.add(cliente)
    db.commit()

    return {
        "mensaje": "Registro exitoso. Ya puedes iniciar sesión.",
        "usuario": datos.nombre_usuario,
        "nombre":  datos.nombre,
    }


# ══════════════════════════════════════════════════════════
#  EMPRESAS PÚBLICAS (para buscador en registro)
# ══════════════════════════════════════════════════════════
@router.get("/empresas-publico")
def listar_empresas_publico(
    buscar: Optional[str] = None,
    db:     Session       = Depends(get_db),
):
    query = db.query(Empresa).filter(Empresa.esta_activa == True)
    if buscar:
        query = query.filter(Empresa.nombre.ilike(f"%{buscar}%"))
    empresas = query.order_by(Empresa.nombre).all()
    return [{"id": str(e.id), "nombre": e.nombre} for e in empresas]


# ══════════════════════════════════════════════════════════
#  VERIFICAR CÉDULA DISPONIBLE
# ══════════════════════════════════════════════════════════
@router.get("/verificar-cedula/{cedula}")
def verificar_cedula(cedula: str, db: Session = Depends(get_db)):
    existe = db.query(Cliente).filter(
        Cliente.cedula == cedula
    ).first()
    return {"disponible": existe is None}


# ══════════════════════════════════════════════════════════
#  RECUPERACIÓN DE CONTRASEÑA — SCHEMAS
# ══════════════════════════════════════════════════════════
class SolicitarRecuperacion(BaseModel):
    correo: str


class VerificarCodigo(BaseModel):
    correo:           str
    codigo:           str
    contrasena_nueva: str


# ══════════════════════════════════════════════════════════
#  POST /auth/recuperar-contrasena
#  Genera código de 6 dígitos y lo envía por correo
# ══════════════════════════════════════════════════════════
@router.post("/recuperar-contrasena")
async def solicitar_recuperacion(
    datos:            SolicitarRecuperacion,
    background_tasks: BackgroundTasks,
    db:               Session = Depends(get_db),
):
    from app.models.recuperacion_contrasena import RecuperacionContrasena
    from app.services.email_service         import enviar_codigo_recuperacion

    correo = datos.correo.strip().lower()

    usuario = db.query(Usuario).filter(
        Usuario.correo      == correo,
        Usuario.esta_activo == True,
    ).first()

    # Respuesta genérica — no revelar si el correo existe o no
    RESPUESTA_GENERICA = {
        "mensaje": "Si el correo está registrado, "
                   "recibirás un código en breve."
    }

    if not usuario:
        return RESPUESTA_GENERICA

    # Invalidar códigos anteriores no usados
    db.query(RecuperacionContrasena).filter(
        RecuperacionContrasena.usuario_id == usuario.id,
        RecuperacionContrasena.usado      == False,
    ).update({"usado": True})

    # Generar código de 6 dígitos
    codigo = ''.join(random.choices(string.digits, k=6))

    recuperacion = RecuperacionContrasena(
        usuario_id = usuario.id,
        codigo     = codigo,
        expira_en  = datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    db.add(recuperacion)
    db.commit()

    # Nombre amigable
    nombre = usuario.nombre_usuario
    if usuario.rol == "cliente" and usuario.cliente:
        nombre = usuario.cliente.nombre.split()[0]
    elif usuario.rol == "vendedor" and usuario.vendedor:
        nombre = usuario.vendedor.nombre_completo.split()[0]

    # Enviar correo en background (no bloquea la respuesta)
    background_tasks.add_task(
        enviar_codigo_recuperacion,
        correo_destino = correo,
        nombre         = nombre,
        codigo         = codigo,
    )

    return RESPUESTA_GENERICA


# ══════════════════════════════════════════════════════════
#  POST /auth/verificar-codigo
#  Valida código y actualiza la contraseña
# ══════════════════════════════════════════════════════════
@router.post("/verificar-codigo")
def verificar_codigo_recuperacion(
    datos: VerificarCodigo,
    db:    Session = Depends(get_db),
):
    from app.models.recuperacion_contrasena import RecuperacionContrasena

    if len(datos.contrasena_nueva) < 6:
        raise HTTPException(
            status_code=400,
            detail="La contraseña debe tener al menos 6 caracteres.",
        )

    correo = datos.correo.strip().lower()

    usuario = db.query(Usuario).filter(
        Usuario.correo      == correo,
        Usuario.esta_activo == True,
    ).first()

    if not usuario:
        raise HTTPException(
            status_code=400,
            detail="Código inválido o expirado.",
        )

    ahora        = datetime.now(timezone.utc)
    recuperacion = db.query(RecuperacionContrasena).filter(
        RecuperacionContrasena.usuario_id == usuario.id,
        RecuperacionContrasena.codigo     == datos.codigo.strip(),
        RecuperacionContrasena.usado      == False,
        RecuperacionContrasena.expira_en  >  ahora,
    ).first()

    if not recuperacion:
        raise HTTPException(
            status_code=400,
            detail="Código inválido o expirado.",
        )

    # Actualizar contraseña
    usuario.contrasena_hash = hashear_contrasena(datos.contrasena_nueva)

    # Marcar código como usado
    recuperacion.usado = True

    db.commit()

    return {"mensaje": "Contraseña actualizada correctamente."}