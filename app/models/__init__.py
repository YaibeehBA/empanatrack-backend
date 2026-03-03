# app/models/__init__.py
from app.models.usuario import Usuario
from app.models.vendedor import Vendedor
from app.models.cliente import Cliente
from app.models.empresa import Empresa
from app.models.pago import Pago
from app.models.producto import Producto
from app.models.ruta import Ruta
# Lista de todos los modelos para facilitar importaciones
__all__ = [
     "Usuario",
    "Vendedor",
    "Cliente",
    "Empresa",
    "Pago",
    "Producto",
    "Ruta",
    # "Empresa",
]