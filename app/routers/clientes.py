from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List
from uuid import UUID
from app.core.security import hashear_contrasena
from app.database import get_db
from app.models.cliente import Cliente
from app.models.empresa import Empresa
from app.models.usuario import Usuario
from app.models.vendedor import Vendedor
from app.schemas.cliente import ClienteCrear, ClienteCrearCompleto, ClienteCrearOutput, ClienteOutput
from app.services.venta_service import obtener_saldo_cliente
from app.core.dependencies import get_usuario_actual, requiere_admin, requiere_vendedor

router = APIRouter(prefix="/clientes", tags=["Clientes"])

@router.get("/", response_model=List[ClienteOutput])
def listar_clientes(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual)
):
    # Admin ve todos
    if usuario.rol == "administrador":
        clientes = db.query(Cliente).filter(
            Cliente.esta_activo == True
        ).order_by(Cliente.nombre).all()
        return [
            ClienteOutput(
                id           = c.id,
                cedula       = c.cedula,
                nombre       = c.nombre,
                correo       = c.correo,
                telefono     = c.telefono,
                empresa      = c.empresa.nombre if c.empresa else None,
                saldo_actual = float(obtener_saldo_cliente(db, c.id)),
            ) for c in clientes
        ]

    # Vendedor ve TODOS los clientes activos
    # (para poder venderles), pero la deuda es solo la suya
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id
    ).first()
    if not vendedor:
        return []

    clientes = db.query(Cliente).filter(
        Cliente.esta_activo == True
    ).order_by(Cliente.nombre).all()

    return [
        ClienteOutput(
            id           = c.id,
            cedula       = c.cedula,
            nombre       = c.nombre,
            correo       = c.correo,
            telefono     = c.telefono,
            empresa      = c.empresa.nombre if c.empresa else None,
            # Deuda solo con este vendedor
            saldo_actual = float(obtener_saldo_vendedor_cliente(
                db, c.id, vendedor.id
            )),
        ) for c in clientes
    ]
def obtener_saldo_vendedor_cliente(
    db:          Session,
    cliente_id:  UUID,
    vendedor_id: UUID
) -> Decimal:
    """Usa la función PostgreSQL para calcular saldo por vendedor."""
    from sqlalchemy import text
    resultado = db.execute(
        text("SELECT calcular_saldo_cliente_vendedor(:cid, :vid)"),
        {
            "cid": str(cliente_id),
            "vid": str(vendedor_id),
        }
    ).scalar()
    return Decimal(str(resultado or 0))

@router.get("/mi-perfil")
def mi_perfil(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual)
):
    if usuario.rol != "cliente":
        raise HTTPException(status_code=403,
                            detail="Solo clientes pueden usar este endpoint.")
    cliente = db.query(Cliente).filter(
        Cliente.usuario_id == usuario.id,
        Cliente.esta_activo == True
    ).first()
    if not cliente:
        raise HTTPException(status_code=404, detail="Perfil no encontrado.")

    return {
        "id":     str(cliente.id),
        "nombre": cliente.nombre,
        "cedula": cliente.cedula,
    }

@router.get("/{cliente_id}/saldo")
def obtener_saldo(
    cliente_id: UUID,
    db:         Session = Depends(get_db),
    usuario:    Usuario = Depends(get_usuario_actual)
):
    # Si es cliente, solo puede ver su propio saldo
    if usuario.rol == "cliente":
        if not usuario.cliente or usuario.cliente.id != cliente_id:
            raise HTTPException(status_code=403, detail="Solo puedes ver tu propio saldo.")

    saldo = obtener_saldo_cliente(db, cliente_id)
    return {"cliente_id": cliente_id, "saldo_actual": float(saldo)}


@router.post("/", response_model=ClienteCrearOutput)
def crear_cliente(
    datos:   ClienteCrearCompleto,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor)   # Vendedor o admin pueden crear
):
    # Verificar que la cédula no exista
    existe = db.query(Cliente).filter(
        Cliente.cedula == datos.cedula
    ).first()
    if existe:
        raise HTTPException(
            status_code=400,
            detail=f"Ya existe un cliente con cédula {datos.cedula}."
        )

    # Verificar correo único si se proporcionó
    if datos.correo:
        correo_existe = db.query(Cliente).filter(
            Cliente.correo == datos.correo
        ).first()
        if correo_existe:
            raise HTTPException(
                status_code=400,
                detail="El correo ya está registrado."
            )

    # Crear usuario de app si se proporcionaron credenciales
    usuario_app = None
    if datos.nombre_usuario and datos.contrasena:
        # Verificar que el nombre de usuario no exista
        user_existe = db.query(Usuario).filter(
            Usuario.nombre_usuario == datos.nombre_usuario
        ).first()
        if user_existe:
            raise HTTPException(
                status_code=400,
                detail=f"El usuario '{datos.nombre_usuario}' ya existe."
            )

        usuario_app = Usuario(
            nombre_usuario  = datos.nombre_usuario,
            correo          = datos.correo,
            contrasena_hash = hashear_contrasena(datos.contrasena),
            rol             = "cliente",
        )
        db.add(usuario_app)
        db.flush()  # Para obtener el id antes del commit

    # Crear el cliente
    cliente = Cliente(
        usuario_id = usuario_app.id if usuario_app else None,
        empresa_id = datos.empresa_id,
        cedula     = datos.cedula,
        nombre     = datos.nombre,
        correo     = datos.correo,
        telefono   = datos.telefono,
    )
    db.add(cliente)
    db.commit()
    db.refresh(cliente)

    # Obtener nombre empresa si existe
    empresa_nombre = None
    if cliente.empresa_id:
        empresa = db.query(Empresa).filter(
            Empresa.id == cliente.empresa_id
        ).first()
        empresa_nombre = empresa.nombre if empresa else None

    return ClienteCrearOutput(
        id           = cliente.id,
        cedula       = cliente.cedula,
        nombre       = cliente.nombre,
        correo       = cliente.correo,
        telefono     = cliente.telefono,
        empresa      = empresa_nombre,
        tiene_acceso = usuario_app is not None,
    )


@router.get("/{cliente_id}/historial")
def historial_cliente(
    cliente_id: UUID,
    db:         Session = Depends(get_db),
    usuario:    Usuario = Depends(get_usuario_actual)
):
    # El cliente solo puede ver su propio historial
    if usuario.rol == "cliente":
        if not usuario.cliente or str(usuario.cliente.id) != str(cliente_id):
            raise HTTPException(status_code=403,
                                detail="Solo puedes ver tu propio historial.")

    resultado = db.execute(
        text("""
            SELECT * FROM vista_historial_cliente
            WHERE cliente_id = :cid
            ORDER BY fecha DESC
        """),
        {"cid": str(cliente_id)}
    ).mappings().all()

    return [dict(r) for r in resultado]


@router.get("/empresas/lista")
def listar_empresas(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor)
):
    empresas = db.query(Empresa).filter(
        Empresa.esta_activa == True
    ).order_by(Empresa.nombre).all()
    return [{"id": str(e.id), "nombre": e.nombre, "direccion": e.direccion}
            for e in empresas]