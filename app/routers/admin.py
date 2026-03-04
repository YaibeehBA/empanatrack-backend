from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional, List
from uuid import UUID
from pydantic import BaseModel
from app.database import get_db
from app.models.usuario import Usuario
from app.models.vendedor import Vendedor
from app.models.empresa import Empresa
from app.models.producto import Producto
from app.core.dependencies import requiere_admin
from app.core.security import hashear_contrasena

router = APIRouter(prefix="/admin", tags=["Administrador"])


# ═══════════════════════════════════════
#  VENDEDORES
# ═══════════════════════════════════════

class VendedorCrear(BaseModel):
    nombre_completo: str
    telefono:        Optional[str] = None
    nombre_usuario:  str
    contrasena:      str
    correo:          Optional[str] = None

class VendedorOutput(BaseModel):
    id:              UUID
    nombre_completo: str
    telefono:        Optional[str] = None
    nombre_usuario:  str
    correo:          Optional[str] = None
    esta_activo:     bool
    model_config = {"from_attributes": True}

class VendedorEditar(BaseModel):
    nombre_completo: Optional[str] = None
    telefono:        Optional[str] = None
    esta_activo:     Optional[bool] = None
    nueva_contrasena: Optional[str] = None


@router.get("/vendedores", response_model=List[VendedorOutput])
def listar_vendedores(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin)
):
    vendedores = db.query(Vendedor).all()
    return [
        VendedorOutput(
            id              = v.id,
            nombre_completo = v.nombre_completo,
            telefono        = v.telefono,
            nombre_usuario  = v.usuario.nombre_usuario,
            correo          = v.usuario.correo,
            esta_activo     = v.esta_activo,
        ) for v in vendedores
    ]


@router.post("/vendedores", response_model=VendedorOutput)
def crear_vendedor(
    datos:   VendedorCrear,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin)
):
    # Verificar usuario único
    existe = db.query(Usuario).filter(
        Usuario.nombre_usuario == datos.nombre_usuario
    ).first()
    if existe:
        raise HTTPException(
            status_code=400,
            detail=f"El usuario '{datos.nombre_usuario}' ya existe."
        )

    # Crear usuario
    nuevo_usuario = Usuario(
        nombre_usuario  = datos.nombre_usuario,
        correo          = datos.correo,
        contrasena_hash = hashear_contrasena(datos.contrasena),
        rol             = "vendedor",
    )
    db.add(nuevo_usuario)
    db.flush()

    # Crear vendedor
    vendedor = Vendedor(
        usuario_id      = nuevo_usuario.id,
        nombre_completo = datos.nombre_completo,
        telefono        = datos.telefono,
    )
    db.add(vendedor)
    db.commit()
    db.refresh(vendedor)

    return VendedorOutput(
        id              = vendedor.id,
        nombre_completo = vendedor.nombre_completo,
        telefono        = vendedor.telefono,
        nombre_usuario  = nuevo_usuario.nombre_usuario,
        correo          = nuevo_usuario.correo,
        esta_activo     = vendedor.esta_activo,
    )


@router.put("/vendedores/{vendedor_id}", response_model=VendedorOutput)
def editar_vendedor(
    vendedor_id: UUID,
    datos:       VendedorEditar,
    db:          Session = Depends(get_db),
    usuario:     Usuario = Depends(requiere_admin)
):
    vendedor = db.query(Vendedor).filter(Vendedor.id == vendedor_id).first()
    if not vendedor:
        raise HTTPException(status_code=404, detail="Vendedor no encontrado.")

    if datos.nombre_completo is not None:
        vendedor.nombre_completo = datos.nombre_completo
    if datos.telefono is not None:
        vendedor.telefono = datos.telefono
    if datos.esta_activo is not None:
        vendedor.esta_activo         = datos.esta_activo
        vendedor.usuario.esta_activo = datos.esta_activo

    if datos.nueva_contrasena:
        vendedor.usuario.contrasena_hash = hashear_contrasena(
            datos.nueva_contrasena
        )

    db.commit()
    db.refresh(vendedor)

    return VendedorOutput(
        id              = vendedor.id,
        nombre_completo = vendedor.nombre_completo,
        telefono        = vendedor.telefono,
        nombre_usuario  = vendedor.usuario.nombre_usuario,
        correo          = vendedor.usuario.correo,
        esta_activo     = vendedor.esta_activo,
    )


# ═══════════════════════════════════════
#  EMPRESAS
# ═══════════════════════════════════════

class EmpresaCrear(BaseModel):
    nombre:    str
    direccion: Optional[str] = None
    telefono:  Optional[str] = None

class EmpresaOutput(BaseModel):
    id:         UUID
    nombre:     str
    direccion:  Optional[str] = None
    telefono:   Optional[str] = None
    esta_activa: bool
    model_config = {"from_attributes": True}

class EmpresaEditar(BaseModel):
    nombre:     Optional[str] = None
    direccion:  Optional[str] = None
    telefono:   Optional[str] = None
    esta_activa: Optional[bool] = None


@router.get("/empresas", response_model=List[EmpresaOutput])
def listar_empresas(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin)
):
    return db.query(Empresa).order_by(Empresa.nombre).all()


@router.post("/empresas", response_model=EmpresaOutput)
def crear_empresa(
    datos:   EmpresaCrear,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin)
):
    empresa = Empresa(**datos.model_dump())
    db.add(empresa)
    db.commit()
    db.refresh(empresa)
    return empresa


@router.put("/empresas/{empresa_id}", response_model=EmpresaOutput)
def editar_empresa(
    empresa_id: UUID,
    datos:      EmpresaEditar,
    db:         Session = Depends(get_db),
    usuario:    Usuario = Depends(requiere_admin)
):
    empresa = db.query(Empresa).filter(Empresa.id == empresa_id).first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa no encontrada.")

    if datos.nombre      is not None: empresa.nombre      = datos.nombre
    if datos.direccion   is not None: empresa.direccion   = datos.direccion
    if datos.telefono    is not None: empresa.telefono    = datos.telefono
    if datos.esta_activa is not None: empresa.esta_activa = datos.esta_activa

    db.commit()
    db.refresh(empresa)
    return empresa


# ═══════════════════════════════════════
#  PRODUCTOS
# ═══════════════════════════════════════

class ProductoCrear(BaseModel):
    nombre: str
    precio: float

class ProductoOutput(BaseModel):
    id:          UUID
    nombre:      str
    precio:      float
    esta_activo: bool
    model_config = {"from_attributes": True}

class ProductoEditar(BaseModel):
    nombre:      Optional[str]   = None
    precio:      Optional[float] = None
    esta_activo: Optional[bool]  = None


@router.get("/productos", response_model=List[ProductoOutput])
def listar_productos_admin(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin)
):
    return db.query(Producto).order_by(Producto.nombre).all()


@router.post("/productos", response_model=ProductoOutput)
def crear_producto(
    datos:   ProductoCrear,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin)
):
    if datos.precio <= 0:
        raise HTTPException(
            status_code=400, detail="El precio debe ser mayor a 0."
        )
    producto = Producto(**datos.model_dump())
    db.add(producto)
    db.commit()
    db.refresh(producto)
    return producto


@router.put("/productos/{producto_id}", response_model=ProductoOutput)
def editar_producto(
    producto_id: UUID,
    datos:       ProductoEditar,
    db:          Session = Depends(get_db),
    usuario:     Usuario = Depends(requiere_admin)
):
    producto = db.query(Producto).filter(Producto.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")

    if datos.nombre      is not None: producto.nombre      = datos.nombre
    if datos.precio      is not None: producto.precio      = datos.precio
    if datos.esta_activo is not None: producto.esta_activo = datos.esta_activo

    db.commit()
    db.refresh(producto)
    return producto


# ═══════════════════════════════════════
#  REPORTE GENERAL
# ═══════════════════════════════════════

@router.get("/resumen")
def resumen_general(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin)
):
    from sqlalchemy import text
    deudas    = db.execute(
        text("SELECT COUNT(*) as clientes, COALESCE(SUM(saldo_actual),0) as total FROM vista_deudas_clientes")
    ).mappings().first()
    vendedores_activos = db.query(Vendedor).filter(
        Vendedor.esta_activo == True
    ).count()
    ventas_hoy = db.execute(
        text("SELECT COALESCE(SUM(total_vendido),0) as hoy FROM vista_ventas_hoy")
    ).mappings().first()

    return {
        "total_deudas":        float(deudas["total"]),
        "clientes_con_deuda":  int(deudas["clientes"]),
        "vendedores_activos":  vendedores_activos,
        "vendido_hoy":         float(ventas_hoy["hoy"]),
    }