# app/routers/productos.py
import os
import cloudinary
import cloudinary.uploader
from fastapi              import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm       import Session
from typing               import List, Optional
from app.database         import get_db
from app.models.producto  import Producto
from app.models.usuario   import Usuario
from app.core.dependencies import requiere_vendedor, requiere_admin, get_usuario_actual
from pydantic             import BaseModel
from uuid                 import UUID

# ── Cloudinary config ─────────────────────────────────────
cloudinary.config(
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key    = os.getenv("CLOUDINARY_API_KEY"),
    api_secret = os.getenv("CLOUDINARY_API_SECRET"),
    secure     = True
)

# ── Schemas ───────────────────────────────────────────────
class ProductoOutput(BaseModel):
    id:          UUID
    nombre:      str
    precio:      float
    imagen_url:  Optional[str] = None
    imagen_public_id: Optional[str] = None
    esta_activo: bool
    model_config = {"from_attributes": True}

router = APIRouter(prefix="/productos", tags=["Productos"])

# ── Helpers Cloudinary ────────────────────────────────────
def _subir_imagen(archivo: UploadFile) -> tuple[str, str]:
    """Sube a Cloudinary. Retorna (url, public_id)."""
    resultado = cloudinary.uploader.upload(
        archivo.file,
        folder         = "productos",
        transformation = [{"width": 800, "height": 800,
                           "crop": "limit", "quality": "auto"}]
    )
    return resultado["secure_url"], resultado["public_id"]

def _eliminar_imagen(public_id: Optional[str]) -> None:
    """Elimina de Cloudinary por public_id."""
    if public_id:
        cloudinary.uploader.destroy(public_id)


# ══════════════════════════════════════════════════════════
#  ENDPOINTS
# ══════════════════════════════════════════════════════════

# ── GET / — listar para vendedores ───────────────────────
@router.get("/", response_model=List[ProductoOutput])
def listar_productos(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    return db.query(Producto).filter(
        Producto.esta_activo == True
    ).all()


# ── GET /disponibles — para clientes ─────────────────────
@router.get("/disponibles")
def productos_disponibles(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual),
):
    productos = db.query(Producto).filter(
        Producto.esta_activo == True
    ).order_by(Producto.nombre).all()

    return [
        {
            "id":          str(p.id),
            "nombre":      p.nombre,
            "precio":      float(p.precio),
            "imagen_url":  p.imagen_url,
            "esta_activo": p.esta_activo,
        }
        for p in productos
    ]


# ── POST / — crear producto con imagen opcional ───────────
@router.post("/")
def crear_producto(
    nombre:  str                    = Form(...),
    precio:  float                  = Form(...),
    imagen:  Optional[UploadFile]   = File(None),
    db:      Session                = Depends(get_db),
    usuario: Usuario                = Depends(requiere_admin),
):
    imagen_url   = None
    imagen_public_id = None

    if imagen and imagen.filename:
        imagen_url, imagen_public_id = _subir_imagen(imagen)

    producto = Producto(
        nombre           = nombre,
        precio           = precio,
        imagen_url       = imagen_url,
        imagen_public_id = imagen_public_id,
        esta_activo      = True,
    )
    db.add(producto)
    db.commit()
    db.refresh(producto)

    return {
        "id":          str(producto.id),
        "nombre":      producto.nombre,
        "precio":      float(producto.precio),
        "imagen_url":  producto.imagen_url,
        "esta_activo": producto.esta_activo,
    }


# ── PUT /{id} — editar producto con imagen opcional ───────
@router.put("/{producto_id}")
def actualizar_producto(
    producto_id: UUID,
    nombre:      Optional[str]      = Form(None),
    precio:      Optional[float]    = Form(None),
    esta_activo: Optional[bool]     = Form(None),
    imagen:      Optional[UploadFile] = File(None),
    db:          Session            = Depends(get_db),
    usuario:     Usuario            = Depends(requiere_admin),
):
    producto = db.query(Producto).filter(
        Producto.id == producto_id
    ).first()

    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    if nombre      is not None: producto.nombre      = nombre
    if precio      is not None: producto.precio      = precio
    if esta_activo is not None: producto.esta_activo = esta_activo

    if imagen and imagen.filename:
        # Eliminar imagen anterior de Cloudinary
        _eliminar_imagen(producto.imagen_public_id)
        # Subir nueva
        producto.imagen_url, producto.imagen_public_id = _subir_imagen(imagen)

    db.commit()
    db.refresh(producto)

    return {
        "id":          str(producto.id),
        "nombre":      producto.nombre,
        "precio":      float(producto.precio),
        "imagen_url":  producto.imagen_url,
        "esta_activo": producto.esta_activo,
    }


# ── DELETE /{id}/imagen — eliminar solo la imagen ─────────
@router.delete("/{producto_id}/imagen")
def eliminar_imagen_producto(
    producto_id: UUID,
    db:          Session = Depends(get_db),
    usuario:     Usuario = Depends(requiere_admin),
):
    producto = db.query(Producto).filter(
        Producto.id == producto_id
    ).first()

    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    _eliminar_imagen(producto.imagen_public_id)
    producto.imagen_url       = None
    producto.imagen_public_id = None
    db.commit()

    return {"mensaje": "Imagen eliminada correctamente"}