from decimal import Decimal
from typing  import List, Optional
from datetime import date  

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


@router.get("/mapa-ruta")
def mapa_ruta_cliente(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    """
    Devuelve el estado actual de la ruta del vendedor
    asignado a la empresa del cliente.
    """
    if usuario.rol != "cliente":
        raise HTTPException(403, "Solo clientes.")

    cliente = db.query(Cliente).filter(
        Cliente.usuario_id == usuario.id).first()
    if not cliente:
        raise HTTPException(404, "Cliente no encontrado.")

    if not cliente.empresa_id:
        return {
            "tiene_empresa":   False,
            "ruta_activa":     False,
            "mensaje":         "No estás asociado a una empresa.",
        }

    hoy = date.today()

    # Buscar vendedor con sesión activa que tenga
    # la empresa del cliente en su ruta
    row = db.execute(text("""
        SELECT
            v.id            AS vendedor_id,
            v.nombre_completo AS vendedor_nombre,
            sr.id           AS sesion_id,
            sr.iniciada_en,
            ra.id           AS asignacion_id,
            r.id            AS ruta_id,
            r.nombre        AS ruta_nombre,
            re_cliente.orden AS orden_empresa_cliente
        FROM vendedores v
        JOIN ruta_asignaciones ra ON ra.vendedor_id = v.id
            AND ra.esta_activa = TRUE
        JOIN rutas r ON r.id = ra.ruta_id
            AND r.esta_activa = TRUE
        JOIN ruta_empresas re_cliente
            ON re_cliente.ruta_id = r.id
            AND re_cliente.empresa_id = :eid
        JOIN sesiones_ruta sr
            ON sr.asignacion_id = ra.id
            AND sr.fecha = :hoy
            AND sr.estado = 'iniciada'
        WHERE v.esta_activo = TRUE
        LIMIT 1
    """), {
        "eid": str(cliente.empresa_id),
        "hoy": str(hoy),
    }).mappings().first()

    if not row:
        # Verificar si hay ruta asignada pero no iniciada aún
        ruta_asignada = db.execute(text("""
            SELECT v.nombre_completo, r.nombre AS ruta_nombre
            FROM vendedores v
            JOIN ruta_asignaciones ra ON ra.vendedor_id = v.id
                AND ra.esta_activa = TRUE
            JOIN rutas r ON r.id = ra.ruta_id
                AND r.esta_activa = TRUE
            JOIN ruta_empresas re ON re.ruta_id = r.id
                AND re.empresa_id = :eid
            WHERE v.esta_activo = TRUE
            LIMIT 1
        """), {"eid": str(cliente.empresa_id)}).mappings().first()

        return {
            "tiene_empresa":   True,
            "empresa_id":      str(cliente.empresa_id),
            "ruta_activa":     False,
            "vendedor_nombre": ruta_asignada["nombre_completo"]
                               if ruta_asignada else None,
            "mensaje": "El vendedor aún no ha iniciado su ruta hoy."
                       if ruta_asignada
                       else "No hay vendedor asignado a tu empresa.",
        }

    sesion_id  = str(row["sesion_id"])
    vendedor_id = str(row["vendedor_id"])
    orden_cliente = int(row["orden_empresa_cliente"])

    # Todas las empresas de la ruta con estado de visita
    empresas_rows = db.execute(text("""
        SELECT
            e.id,
            e.nombre,
            e.direccion,
            e.latitud,
            e.longitud,
            re.orden,
            CASE WHEN vv.es_valida = TRUE THEN TRUE
                 ELSE FALSE END AS visitada,
            vv.marcada_en
        FROM ruta_empresas re
        JOIN empresas e ON e.id = re.empresa_id
        LEFT JOIN visitas_verificadas vv
            ON vv.empresa_id = e.id
            AND vv.sesion_id = :sid
            AND vv.es_valida = TRUE
        WHERE re.ruta_id = :rid
        ORDER BY re.orden
    """), {
        "sid": sesion_id,
        "rid": str(row["ruta_id"]),
    }).mappings().all()

    empresas = []
    visitadas_count = 0
    empresas_antes_cliente = 0

    for emp in empresas_rows:
        visitada = bool(emp["visitada"])
        if visitada:
            visitadas_count += 1

        es_empresa_cliente = str(emp["id"]) == str(cliente.empresa_id)

        # Cuántas empresas faltan antes de llegar al cliente
        if not visitada and not es_empresa_cliente:
            orden = int(emp["orden"])
            if orden < orden_cliente:
                empresas_antes_cliente += 1

        empresas.append({
            "id":                 str(emp["id"]),
            "nombre":             emp["nombre"],
            "direccion":          emp["direccion"],
            "latitud":            float(emp["latitud"])
                                  if emp["latitud"] else None,
            "longitud":           float(emp["longitud"])
                                  if emp["longitud"] else None,
            "orden":              emp["orden"],
            "visitada":           visitada,
            "es_mi_empresa":      es_empresa_cliente,
        })

    empresa_cliente = next(
        (e for e in empresas
         if e["es_mi_empresa"]), None)

    ya_visitada = empresa_cliente["visitada"] \
        if empresa_cliente else False

    return {
        "tiene_empresa":           True,
        "empresa_id":              str(cliente.empresa_id),
        "ruta_activa":             True,
        "vendedor_id":             vendedor_id,
        "vendedor_nombre":         row["vendedor_nombre"],
        "sesion_id":               sesion_id,
        "ruta_nombre":             row["ruta_nombre"],
        "empresas":                empresas,
        "total_empresas":          len(empresas),
        "empresas_visitadas":      visitadas_count,
        "empresas_antes_cliente":  empresas_antes_cliente,
        "mi_empresa_visitada":     ya_visitada,
        "orden_mi_empresa":        orden_cliente,
    }