# app/routers/productos.py
import os
import uuid
from fastapi             import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses   import JSONResponse
from sqlalchemy.orm      import Session
from typing              import List, Optional
from app.database        import get_db
from app.models.producto import Producto
from app.models.usuario  import Usuario
from app.core.dependencies import requiere_vendedor, requiere_admin, get_usuario_actual
from pydantic            import BaseModel
from uuid                import UUID

# ── Schemas ───────────────────────────────────────────────
class ProductoOutput(BaseModel):
    id:         UUID
    nombre:     str
    precio:     float
    imagen_url: Optional[str] = None
    esta_activo: bool
    model_config = {"from_attributes": True}

router = APIRouter(prefix="/productos", tags=["Productos"])

# ── Constantes ────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "productos")
MAX_SIZE_BYTES  = 2 * 1024 * 1024          # 2 MB
TIPOS_PERMITIDOS = {"image/jpeg", "image/png", "image/webp"}


# ── Helper: guardar imagen ────────────────────────────────
def _guardar_imagen(archivo: UploadFile) -> str:
    # Validar tipo
    if archivo.content_type not in TIPOS_PERMITIDOS:
        raise HTTPException(
            status_code=400,
            detail="Solo se permiten imágenes JPG, PNG o WEBP"
        )

    # Leer contenido
    contenido = archivo.file.read()

    # Validar tamaño
    if len(contenido) > MAX_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail="La imagen no puede superar 2MB"
        )

    # Generar nombre único
    extension  = archivo.filename.rsplit(".", 1)[-1].lower()
    nombre_archivo = f"{uuid.uuid4()}.{extension}"
    ruta_completa  = os.path.join(UPLOAD_DIR, nombre_archivo)

    # Guardar en disco
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with open(ruta_completa, "wb") as f:
        f.write(contenido)

    return f"/static/productos/{nombre_archivo}"


# ── Helper: eliminar imagen anterior ─────────────────────
def _eliminar_imagen(imagen_url: Optional[str]) -> None:
    if not imagen_url:
        return
    ruta = imagen_url.lstrip("/")               # quita el / inicial
    if os.path.exists(ruta):
        os.remove(ruta)


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
    nombre:      str          = Form(...),
    precio:      float        = Form(...),
    imagen:      Optional[UploadFile] = File(None),
    db:          Session      = Depends(get_db),
    usuario:     Usuario      = Depends(requiere_admin),
):
    imagen_url = None
    if imagen and imagen.filename:
        imagen_url = _guardar_imagen(imagen)

    producto = Producto(
        nombre      = nombre,
        precio      = precio,
        imagen_url  = imagen_url,
        esta_activo = True,
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
    nombre:      Optional[str]        = Form(None),
    precio:      Optional[float]      = Form(None),
    esta_activo: Optional[bool]       = Form(None),
    imagen:      Optional[UploadFile] = File(None),
    db:          Session              = Depends(get_db),
    usuario:     Usuario              = Depends(requiere_admin),
):
    producto = db.query(Producto).filter(
        Producto.id == producto_id
    ).first()

    if not producto:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    if nombre      is not None: producto.nombre      = nombre
    if precio      is not None: producto.precio      = precio
    if esta_activo is not None: producto.esta_activo = esta_activo

    # Si llega nueva imagen, eliminar la anterior y guardar la nueva
    if imagen and imagen.filename:
        _eliminar_imagen(producto.imagen_url)
        producto.imagen_url = _guardar_imagen(imagen)

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

    _eliminar_imagen(producto.imagen_url)
    producto.imagen_url = None
    db.commit()

    return {"mensaje": "Imagen eliminada correctamente"}