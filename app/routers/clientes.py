from decimal import Decimal
from typing  import List, Optional

from fastapi             import APIRouter, Depends, HTTPException, Query
from sqlalchemy          import or_
from sqlalchemy.orm      import Session
from sqlalchemy          import text
from uuid                import UUID
from app.core.security   import hashear_contrasena
from app.database        import get_db
from app.models.cliente  import Cliente
from app.models.empresa  import Empresa
from app.models.usuario  import Usuario
from app.models.vendedor import Vendedor
from app.schemas.cliente import (
    ClienteCrear, ClienteCrearCompleto,
    ClienteCrearOutput, ClienteOutput,
)
from app.services.venta_service import obtener_saldo_cliente
from app.core.dependencies      import (
    get_usuario_actual, requiere_admin, requiere_vendedor,
)

router = APIRouter(prefix="/clientes", tags=["Clientes"])


# ══════════════════════════════════════════════════════════
#  HELPER
# ══════════════════════════════════════════════════════════
def obtener_saldo_vendedor_cliente(
    db:          Session,
    cliente_id:  UUID,
    vendedor_id: UUID,
) -> Decimal:
    resultado = db.execute(
        text("SELECT calcular_saldo_cliente_vendedor(:cid, :vid)"),
        {"cid": str(cliente_id), "vid": str(vendedor_id)},
    ).scalar()
    return Decimal(str(resultado or 0))


# ══════════════════════════════════════════════════════════
#  GET /clientes/
# ══════════════════════════════════════════════════════════
@router.get("/")
def listar_clientes(
    pagina:     int           = Query(default=1,  ge=1),
    por_pagina: int           = Query(default=20, ge=1, le=100),
    buscar:     Optional[str] = Query(default=None),
    db:         Session       = Depends(get_db),
    usuario:    Usuario       = Depends(get_usuario_actual),
):
    query = db.query(Cliente).filter(Cliente.esta_activo == True)

    if buscar and buscar.strip():
        termino = f"%{buscar.strip()}%"
        query = query.filter(
            or_(
                Cliente.nombre.ilike(termino),
                Cliente.cedula.ilike(termino),
            )
        )

    clientes = (
        query.order_by(Cliente.nombre)
        .offset((pagina - 1) * por_pagina)
        .limit(por_pagina)
        .all()
    )

    if usuario.rol == "administrador":
        resultado = [
            ClienteOutput(
                id           = c.id,
                cedula       = c.cedula,
                nombre       = c.nombre,
                correo       = c.correo,
                telefono     = c.telefono,
                empresa      = c.empresa.nombre if c.empresa else None,
                saldo_actual = float(obtener_saldo_cliente(db, c.id)),
            )
            for c in clientes
        ]
    else:
        vendedor = db.query(Vendedor).filter(
            Vendedor.usuario_id == usuario.id
        ).first()
        if not vendedor:
            return {"clientes": [], "pagina": pagina,
                    "por_pagina": por_pagina}

        resultado = [
            ClienteOutput(
                id           = c.id,
                cedula       = c.cedula,
                nombre       = c.nombre,
                correo       = c.correo,
                telefono     = c.telefono,
                empresa      = c.empresa.nombre if c.empresa else None,
                saldo_actual = float(obtener_saldo_vendedor_cliente(
                    db, c.id, vendedor.id
                )),
            )
            for c in clientes
        ]

    return {
        "clientes":   resultado,
        "pagina":     pagina,
        "por_pagina": por_pagina,
    }


# ══════════════════════════════════════════════════════════
#  GET /clientes/mi-perfil
# ══════════════════════════════════════════════════════════
@router.get("/mi-perfil")
def mi_perfil(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    if usuario.rol != "cliente":
        raise HTTPException(
            status_code=403,
            detail="Solo clientes pueden usar este endpoint.",
        )
    cliente = db.query(Cliente).filter(
        Cliente.usuario_id  == usuario.id,
        Cliente.esta_activo == True,
    ).first()
    if not cliente:
        raise HTTPException(status_code=404,
                            detail="Perfil no encontrado.")
    return {
        "id":     str(cliente.id),
        "nombre": cliente.nombre,
        "cedula": cliente.cedula,
    }


# ══════════════════════════════════════════════════════════
#  GET /clientes/empresas/lista
# ══════════════════════════════════════════════════════════
@router.get("/empresas/lista")
def listar_empresas(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    empresas = db.query(Empresa).filter(
        Empresa.esta_activa == True
    ).order_by(Empresa.nombre).all()
    return [
        {"id": str(e.id), "nombre": e.nombre,
         "direccion": e.direccion}
        for e in empresas
    ]

@router.get("/mi-empresa")
def mi_empresa(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    """Devuelve la empresa del cliente si tiene una."""
    if usuario.rol != "cliente":
        raise HTTPException(403, "Solo clientes.")

    cliente = db.query(Cliente).filter(
        Cliente.usuario_id == usuario.id).first()
    if not cliente or not cliente.empresa_id:
        return None

    empresa = db.execute(text("""
        SELECT id, nombre, direccion
        FROM empresas
        WHERE id = :eid
    """), {"eid": str(cliente.empresa_id)}).mappings().first()

    return dict(empresa) if empresa else None

# ══════════════════════════════════════════════════════════
#  GET /clientes/verificar-cedula/{cedula}
# ══════════════════════════════════════════════════════════
@router.get("/verificar-cedula/{cedula}")
def verificar_cedula_autenticado(
    cedula:  str,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    existe = db.query(Cliente).filter(
        Cliente.cedula == cedula
    ).first()
    return {"disponible": existe is None}


# ══════════════════════════════════════════════════════════
#  POST /clientes/
# ══════════════════════════════════════════════════════════
@router.post("/", response_model=ClienteCrearOutput)
def crear_cliente(
    datos:   ClienteCrearCompleto,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    # Cédula única
    if db.query(Cliente).filter(
        Cliente.cedula == datos.cedula
    ).first():
        raise HTTPException(
            status_code=400,
            detail=f"Ya existe un cliente con cédula {datos.cedula}.",
        )

    # Correo único
    if datos.correo:
        if db.query(Cliente).filter(
            Cliente.correo == datos.correo
        ).first():
            raise HTTPException(
                status_code=400,
                detail="El correo ya está registrado.",
            )

    # Usuario app
    usuario_app = None
    if datos.nombre_usuario and datos.contrasena:
        if db.query(Usuario).filter(
            Usuario.nombre_usuario == datos.nombre_usuario
        ).first():
            raise HTTPException(
                status_code=400,
                detail=f"El usuario '{datos.nombre_usuario}' ya existe.",
            )
        usuario_app = Usuario(
            nombre_usuario  = datos.nombre_usuario,
            correo          = datos.correo,
            contrasena_hash = hashear_contrasena(datos.contrasena),
            rol             = "cliente",
        )
        db.add(usuario_app)
        db.flush()

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


# ══════════════════════════════════════════════════════════
#  GET /clientes/{cliente_id}/saldo
# ══════════════════════════════════════════════════════════
@router.get("/{cliente_id}/saldo")
def obtener_saldo(
    cliente_id: UUID,
    db:         Session = Depends(get_db),
    usuario:    Usuario = Depends(get_usuario_actual),
):
    if usuario.rol == "cliente":
        if not usuario.cliente or \
                usuario.cliente.id != cliente_id:
            raise HTTPException(
                status_code=403,
                detail="Solo puedes ver tu propio saldo.",
            )
    saldo = obtener_saldo_cliente(db, cliente_id)
    return {"cliente_id": cliente_id,
            "saldo_actual": float(saldo)}


# ══════════════════════════════════════════════════════════
#  GET /clientes/{cliente_id}/historial
# ══════════════════════════════════════════════════════════
@router.get("/{cliente_id}/historial")
def historial_cliente(
    cliente_id: UUID,
    db:         Session = Depends(get_db),
    usuario:    Usuario = Depends(get_usuario_actual),
):
    if usuario.rol == "cliente":
        if not usuario.cliente or \
                str(usuario.cliente.id) != str(cliente_id):
            raise HTTPException(
                status_code=403,
                detail="Solo puedes ver tu propio historial.",
            )

    resultado = db.execute(
        text("""
            SELECT * FROM vista_historial_cliente
            WHERE cliente_id = :cid
            ORDER BY fecha DESC
        """),
        {"cid": str(cliente_id)},
    ).mappings().all()

    return [dict(r) for r in resultado]

@router.delete("/{cliente_id}")
def eliminar_cliente(
    cliente_id: UUID,
    db:         Session = Depends(get_db),
    usuario:    Usuario = Depends(requiere_admin),
):
    cliente = db.query(Cliente).filter(
        Cliente.id == cliente_id
    ).first()
    if not cliente:
        raise HTTPException(
            status_code=404, detail="Cliente no encontrado.")

    # Verificar que no tenga ventas
    from sqlalchemy import text
    tiene_ventas = db.execute(
        text("SELECT 1 FROM ventas WHERE cliente_id = :cid LIMIT 1"),
        {"cid": str(cliente_id)}
    ).first()
    if tiene_ventas:
        raise HTTPException(
            status_code=400,
            detail="No se puede eliminar: el cliente tiene ventas registradas.")

    # Eliminar usuario app si tiene
    if cliente.usuario_id:
        usr = db.query(Usuario).filter(
            Usuario.id == cliente.usuario_id
        ).first()
        if usr:
            db.delete(usr)

    db.delete(cliente)
    db.commit()
    return {"mensaje": "Cliente eliminado correctamente."}