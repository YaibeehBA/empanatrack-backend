from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List
from uuid import UUID
from app.database import get_db
from app.models.cliente import Cliente
from app.models.empresa import Empresa
from app.models.usuario import Usuario
from app.schemas.cliente import ClienteCrear, ClienteOutput
from app.services.venta_service import obtener_saldo_cliente
from app.core.dependencies import get_usuario_actual, requiere_admin, requiere_vendedor

router = APIRouter(prefix="/clientes", tags=["Clientes"])

@router.get("/", response_model=List[ClienteOutput])
def listar_clientes(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor)
):
    clientes = db.query(Cliente).filter(Cliente.esta_activo == True).all()
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


@router.post("/", response_model=ClienteOutput)
def crear_cliente(
    datos:   ClienteCrear,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin)
):
    cliente = Cliente(**datos.model_dump())
    db.add(cliente)
    db.commit()
    db.refresh(cliente)
    return ClienteOutput(
        id       = cliente.id,
        cedula   = cliente.cedula,
        nombre   = cliente.nombre,
        correo   = cliente.correo,
        telefono = cliente.telefono,
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