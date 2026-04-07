# app/routers/admin.py
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from typing import Optional, List
from pydantic import BaseModel, validator
from uuid import UUID
from pydantic import BaseModel
from app.database import get_db
from app.models.usuario import Usuario
from app.models.vendedor import Vendedor
from app.models.empresa import Empresa
from app.models.producto import Producto
from app.core.dependencies import requiere_admin
from app.core.security import hashear_contrasena
import os
import uuid as uuid_lib

from app.utils.validators import validar_coordenada_latitud, validar_coordenada_longitud, validar_telefono_ecuador

router = APIRouter(prefix="/admin", tags=["Administrador"])

import cloudinary
import cloudinary.uploader
import os

cloudinary.config(
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key    = os.getenv("CLOUDINARY_API_KEY"),
    api_secret = os.getenv("CLOUDINARY_API_SECRET"),
    secure     = True
)

def _subir_imagen_cloudinary(imagen: UploadFile) -> tuple[str, str]:
    """Sube imagen a Cloudinary. Retorna (url, public_id)."""
    resultado = cloudinary.uploader.upload(
        imagen.file,
        folder         = "productos",
        transformation = [{"width": 800, "height": 800,
                           "crop": "limit", "quality": "auto"}]
    )
    return resultado["secure_url"], resultado["public_id"]

def _eliminar_imagen_cloudinary(public_id: str | None) -> None:
    """Elimina imagen de Cloudinary por public_id."""
    if public_id:
        cloudinary.uploader.destroy(public_id)


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
    nombre_completo:  Optional[str]  = None
    telefono:         Optional[str]  = None
    esta_activo:      Optional[bool] = None
    nueva_contrasena: Optional[str]  = None


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
    existe = db.query(Usuario).filter(
        Usuario.nombre_usuario == datos.nombre_usuario
    ).first()
    if existe:
        raise HTTPException(
            status_code=400,
            detail=f"El usuario '{datos.nombre_usuario}' ya existe."
        )

    nuevo_usuario = Usuario(
        nombre_usuario  = datos.nombre_usuario,
        correo          = datos.correo,
        contrasena_hash = hashear_contrasena(datos.contrasena),
        rol             = "vendedor",
    )
    db.add(nuevo_usuario)
    db.flush()

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
    vendedor = db.query(Vendedor).filter(
        Vendedor.id == vendedor_id
    ).first()
    if not vendedor:
        raise HTTPException(
            status_code=404, detail="Vendedor no encontrado."
        )

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

@router.delete("/vendedores/{vendedor_id}")
def eliminar_vendedor(
    vendedor_id: UUID,
    db:          Session = Depends(get_db),
    usuario:     Usuario = Depends(requiere_admin)
):
    vendedor = db.query(Vendedor).filter(
        Vendedor.id == vendedor_id
    ).first()
    if not vendedor:
        raise HTTPException(
            status_code=404, detail="Vendedor no encontrado.")

    # Verificar que no tenga ventas registradas
    from app.models.venta import Venta
    tiene_ventas = db.query(Venta).filter(
        Venta.vendedor_id == vendedor_id
    ).first()
    if tiene_ventas:
        raise HTTPException(
            status_code=400,
            detail="No se puede eliminar: el vendedor tiene ventas registradas. "
                   "Desactívalo en su lugar.")

    # Eliminar usuario asociado también
    usuario_vendedor = vendedor.usuario
    db.delete(vendedor)
    if usuario_vendedor:
        db.delete(usuario_vendedor)
    db.commit()
    return {"mensaje": "Vendedor eliminado correctamente."}


# ═══════════════════════════════════════
#  EMPRESAS
# ═══════════════════════════════════════
class EmpresaCrear(BaseModel):
    nombre:    str
    direccion: Optional[str]   = None
    telefono:  Optional[str]   = None
    latitud:   Optional[float] = None
    longitud:  Optional[float] = None

    @validator('nombre')
    def nombre_valido(cls, v):
        if not v or not v.strip():
            raise ValueError('El nombre es obligatorio')
        if len(v.strip()) < 2:
            raise ValueError('El nombre debe tener al menos 2 caracteres')
        return v.strip()

    @validator('telefono')
    def telefono_valido(cls, v):
        if v and v.strip():
            return validar_telefono_ecuador(v.strip())
        return v

    @validator('latitud')
    def latitud_valida(cls, v):
        if v is not None:
            return validar_coordenada_latitud(v)
        return v

    @validator('longitud')
    def longitud_valida(cls, v):
        if v is not None:
            return validar_coordenada_longitud(v)
        return v


class EmpresaOutput(BaseModel):
    id:          UUID
    nombre:      str
    direccion:   Optional[str]   = None
    telefono:    Optional[str]   = None
    esta_activa: bool
    latitud:     Optional[float] = None
    longitud:    Optional[float] = None
    model_config = {"from_attributes": True}


class EmpresaEditar(BaseModel):
    nombre:      Optional[str]   = None
    direccion:   Optional[str]   = None
    telefono:    Optional[str]   = None
    esta_activa: Optional[bool]  = None
    latitud:     Optional[float] = None
    longitud:    Optional[float] = None

    @validator('nombre')
    def nombre_valido(cls, v):
        if v is not None:
            if not v.strip():
                raise ValueError('El nombre no puede estar vacío')
            if len(v.strip()) < 2:
                raise ValueError('El nombre debe tener al menos 2 caracteres')
            return v.strip()
        return v

    @validator('telefono')
    def telefono_valido(cls, v):
        if v and v.strip():
            return validar_telefono_ecuador(v.strip())
        return v

    @validator('latitud')
    def latitud_valida(cls, v):
        if v is not None:
            return validar_coordenada_latitud(v)
        return v

    @validator('longitud')
    def longitud_valida(cls, v):
        if v is not None:
            return validar_coordenada_longitud(v)
        return v

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

# ── Schema ───────────────────────────────────────────────
class ParseMapsUrl(BaseModel):
    url: str


# ── Endpoint ─────────────────────────────────────────────
@router.post("/empresas/parsear-url-maps")
def parsear_url_maps(
    datos:   ParseMapsUrl,
    usuario: Usuario = Depends(requiere_admin),
):
    from app.utils.maps_parser import parse_google_maps_url
    try:
        resultado = parse_google_maps_url(datos.url)
        return {"ok": True, "data": resultado}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Error interno procesando la URL."
        )

@router.put("/empresas/{empresa_id}", response_model=EmpresaOutput)
def editar_empresa(
    empresa_id: UUID,
    datos:      EmpresaEditar,
    db:         Session = Depends(get_db),
    usuario:    Usuario = Depends(requiere_admin)
):
    empresa = db.query(Empresa).filter(
        Empresa.id == empresa_id
    ).first()
    if not empresa:
        raise HTTPException(
            status_code=404, detail="Empresa no encontrada."
        )

    if datos.nombre      is not None: empresa.nombre      = datos.nombre
    if datos.direccion   is not None: empresa.direccion   = datos.direccion
    if datos.telefono    is not None: empresa.telefono    = datos.telefono
    if datos.esta_activa is not None: empresa.esta_activa = datos.esta_activa
    if datos.latitud     is not None: empresa.latitud     = datos.latitud  
    if datos.longitud    is not None: empresa.longitud    = datos.longitud 

    db.commit()
    db.refresh(empresa)
    return empresa


@router.delete("/empresas/{empresa_id}")
def eliminar_empresa(
    empresa_id: UUID,
    db:         Session = Depends(get_db),
    usuario:    Usuario = Depends(requiere_admin)
):
    empresa = db.query(Empresa).filter(
        Empresa.id == empresa_id
    ).first()
    if not empresa:
        raise HTTPException(
            status_code=404, detail="Empresa no encontrada.")

    # Verificar que no tenga clientes asociados
    from app.models.cliente import Cliente
    tiene_clientes = db.query(Cliente).filter(
        Cliente.empresa_id == empresa_id
    ).first()
    if tiene_clientes:
        raise HTTPException(
            status_code=400,
            detail="No se puede eliminar: la empresa tiene clientes asociados. "
                   "Desactívala en su lugar.")

    db.delete(empresa)
    db.commit()
    return {"mensaje": "Empresa eliminada correctamente."}


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
    imagen_url:  Optional[str] = None
    imagen_public_id: Optional[str] = None
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
    producto = db.query(Producto).filter(
        Producto.id == producto_id
    ).first()
    if not producto:
        raise HTTPException(
            status_code=404, detail="Producto no encontrado."
        )

    if datos.nombre      is not None: producto.nombre      = datos.nombre
    if datos.precio      is not None: producto.precio      = datos.precio
    if datos.esta_activo is not None: producto.esta_activo = datos.esta_activo

    db.commit()
    db.refresh(producto)
    return producto


@router.post("/productos/{producto_id}/imagen",
             response_model=ProductoOutput)
def subir_imagen_producto(
    producto_id: UUID,
    imagen:      UploadFile = File(...),
    db:          Session    = Depends(get_db),
    usuario:     Usuario    = Depends(requiere_admin)
):
    producto = db.query(Producto).filter(
        Producto.id == producto_id
    ).first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")

    # Eliminar imagen anterior si existe
    _eliminar_imagen_cloudinary(producto.imagen_public_id)

    # Subir nueva imagen
    url, public_id         = _subir_imagen_cloudinary(imagen)
    producto.imagen_url    = url
    producto.imagen_public_id = public_id

    db.commit()
    db.refresh(producto)
    return producto


@router.delete("/productos/{producto_id}/imagen")
def eliminar_imagen_producto(
    producto_id: UUID,
    db:          Session = Depends(get_db),
    usuario:     Usuario = Depends(requiere_admin)
):
    producto = db.query(Producto).filter(
        Producto.id == producto_id
    ).first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")

    _eliminar_imagen_cloudinary(producto.imagen_public_id)
    producto.imagen_url       = None
    producto.imagen_public_id = None
    db.commit()

    return {"mensaje": "Imagen eliminada correctamente"}
@router.delete("/productos/{producto_id}")
def eliminar_producto(
    producto_id: UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin)
):
    # Buscar el producto
    producto = db.query(Producto).filter(Producto.id == producto_id).first()
    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")
    
    # Opcional: Eliminar la imagen de Cloudinary si existe
    if producto.imagen_public_id:
        _eliminar_imagen_cloudinary(producto.imagen_public_id)
    
    # Eliminar el producto de la base de datos
    db.delete(producto)
    db.commit()
    
    return {"mensaje": "Producto eliminado correctamente"}
# ═══════════════════════════════════════
#  REPORTE GENERAL
# ═══════════════════════════════════════

@router.get("/resumen")
def resumen_general(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin),
):
    from sqlalchemy import text
    from datetime   import date

    hoy = date.today()

    vendedores_activos = db.query(Vendedor).filter(
        Vendedor.esta_activo == True
    ).count()

    deudas = db.execute(
        text("""
            SELECT
                COALESCE(SUM(saldo_actual), 0) AS total_deudas,
                COUNT(*)                        AS clientes_con_deuda
            FROM vista_deudas_clientes
            WHERE saldo_actual > 0
        """)
    ).mappings().first()

    ventas_hoy = db.execute(
        text("""
            SELECT
                COALESCE(SUM(monto_total), 0)  AS total_vendido,
                COUNT(*)                        AS total_ventas
            FROM ventas
            WHERE DATE(fecha_venta) = :hoy
        """),
        {"hoy": str(hoy)},
    ).mappings().first()

    pedidos_hoy = db.execute(
        text("""
            SELECT
                COUNT(*)                                          AS total_pedidos,
                COALESCE(SUM(total), 0)                          AS total_monto,
                COALESCE(SUM(CASE WHEN estado='pendiente'
                    THEN 1 ELSE 0 END), 0)                       AS pendientes,
                COALESCE(SUM(CASE WHEN estado='entregado'
                    THEN 1 ELSE 0 END), 0)                       AS entregados
            FROM pedidos
            WHERE DATE(creado_en) = :hoy
        """),
        {"hoy": str(hoy)},
    ).mappings().first()

    return {
        "total_deudas":       float(deudas["total_deudas"]),
        "clientes_con_deuda": int(deudas["clientes_con_deuda"]),
        "vendedores_activos": vendedores_activos,
        "vendido_hoy":        float(ventas_hoy["total_vendido"]),
        "ventas_hoy":         int(ventas_hoy["total_ventas"]),
        "pedidos_hoy":        int(pedidos_hoy["total_pedidos"]),
        "pedidos_pendientes": int(pedidos_hoy["pendientes"]),
        "pedidos_entregados": int(pedidos_hoy["entregados"]),
        "monto_pedidos_hoy":  float(pedidos_hoy["total_monto"]),
    }
# ═══════════════════════════════════════
#  CONFIGURACIÓN DEL NEGOCIO
# ═══════════════════════════════════════

class ConfiguracionItem(BaseModel):
    clave:       str
    valor:       str
    descripcion: Optional[str] = None

class ConfiguracionOutput(BaseModel):
    clave:       str
    valor:       str
    descripcion: Optional[str] = None


@router.get("/configuracion", response_model=List[ConfiguracionOutput])
def listar_configuracion(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin),
):
    from app.models.pedido import Configuracion
    items = db.query(Configuracion).order_by(
        Configuracion.clave
    ).all()
    return [
        ConfiguracionOutput(
            clave       = c.clave,
            valor       = c.valor,
            descripcion = c.descripcion,
        )
        for c in items
    ]


@router.put("/configuracion/{clave}")
def actualizar_configuracion(
    clave:   str,
    datos:   ConfiguracionItem,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin),
):
    from app.models.pedido import Configuracion
    cfg = db.query(Configuracion).filter(
        Configuracion.clave == clave
    ).first()
    if not cfg:
        raise HTTPException(
            status_code=404,
            detail=f"Configuración '{clave}' no encontrada.")

    cfg.valor = datos.valor.strip()
    db.commit()
    return {"clave": cfg.clave, "valor": cfg.valor,
            "mensaje": "Actualizado correctamente."}