# app/routers/productos.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models.producto import Producto
from app.core.dependencies import requiere_vendedor, get_usuario_actual  
from app.models.usuario import Usuario
from pydantic import BaseModel
from uuid import UUID

class ProductoOutput(BaseModel):
    id:     UUID
    nombre: str
    precio: float
    model_config = {"from_attributes": True}

router = APIRouter(prefix="/productos", tags=["Productos"])

@router.get("/", response_model=List[ProductoOutput])
def listar_productos(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor)
):
    return db.query(Producto).filter(Producto.esta_activo == True).all()

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
            "esta_activo": p.esta_activo,
        }
        for p in productos
    ]