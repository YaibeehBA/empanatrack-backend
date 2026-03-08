from fastapi import APIRouter, Depends, Query
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
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id
    ).first()
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


@router.get("/vendedor/resumen")
def resumen_vendedor(
    periodo: str     = Query(default="hoy"),
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor)
):
    from datetime import date, timedelta

    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id
    ).first()

    if not vendedor:
        return {
            "total_ventas":   0,
            "total_vendido":  0.0,
            "total_fiado":    0.0,
            "total_contado":  0.0,
            "total_cobrado":  0.0,
            "dinero_en_mano": 0.0,
        }

    hoy = date.today()
    if periodo == "ayer":
        desde = hoy - timedelta(days=1)
        hasta = desde
    elif periodo == "semana":
        desde = hoy - timedelta(days=6)
        hasta = hoy
    elif periodo == "mes":
        desde = hoy.replace(day=1)
        hasta = hoy
    else:  # hoy por defecto
        desde = hoy
        hasta = hoy

    resultado = db.execute(
        text("""
            SELECT
                COUNT(*)                                        AS total_ventas,
                COALESCE(SUM(monto_total), 0)                   AS total_vendido,
                COALESCE(SUM(
                    CASE WHEN tipo = 'credito'
                    THEN monto_total ELSE 0 END), 0)            AS total_fiado,
                COALESCE(SUM(
                    CASE WHEN tipo = 'contado'
                    THEN monto_total ELSE 0 END), 0)            AS total_contado,
                COALESCE((
                    SELECT SUM(p.monto)
                    FROM pagos p
                    WHERE p.vendedor_id       = :vid
                      AND DATE(p.fecha_pago)  BETWEEN :desde AND :hasta
                ), 0)                                           AS total_cobrado
            FROM ventas
            WHERE vendedor_id        = :vid
              AND DATE(fecha_venta)  BETWEEN :desde AND :hasta
        """),
        {
            "vid":   str(vendedor.id),
            "desde": str(desde),
            "hasta": str(hasta),
        }
    ).mappings().first()

    total_contado = float(resultado["total_contado"])
    total_cobrado = float(resultado["total_cobrado"])

    return {
        "total_ventas":   int(resultado["total_ventas"]),
        "total_vendido":  float(resultado["total_vendido"]),
        "total_fiado":    float(resultado["total_fiado"]),
        "total_contado":  total_contado,
        "total_cobrado":  total_cobrado,
        "dinero_en_mano": total_contado + total_cobrado,
    }