from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.database import get_db
from app.models.vendedor import Vendedor
from app.models.usuario import Usuario
from app.core.dependencies import requiere_vendedor, requiere_admin

router = APIRouter(prefix="/reportes", tags=["Reportes"])

@router.get("/vendedor/hoy")
def reporte_hoy(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor)
):
    vendedor = db.query(Vendedor).filter(Vendedor.usuario_id == usuario.id).first()
    resultado = db.execute(
        text("SELECT * FROM vista_ventas_hoy WHERE vendedor_id = :vid"),
        {"vid": str(vendedor.id)}
    ).mappings().first()
    return dict(resultado) if resultado else {}


@router.get("/admin/deudas")
def reporte_deudas(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin)
):
    resultado = db.execute(
        text("SELECT * FROM vista_deudas_clientes ORDER BY saldo_actual DESC")
    ).mappings().all()
    return [dict(r) for r in resultado]