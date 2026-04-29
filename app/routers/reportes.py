from fastapi        import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy     import text
from app.database          import get_db
from app.models.vendedor   import Vendedor
from app.models.usuario    import Usuario
from app.core.dependencies import requiere_vendedor, requiere_admin

router = APIRouter(prefix="/reportes", tags=["Reportes"])


# ══════════════════════════════════════════════════════════
#  VENDEDOR — reporte hoy (vista)
# ══════════════════════════════════════════════════════════
@router.get("/vendedor/hoy")
def reporte_hoy(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id
    ).first()
    resultado = db.execute(
        text("SELECT * FROM vista_ventas_hoy WHERE vendedor_id = :vid"),
        {"vid": str(vendedor.id)}
    ).mappings().first()
    return dict(resultado) if resultado else {}


# ══════════════════════════════════════════════════════════
#  VENDEDOR — resumen con periodo
# ══════════════════════════════════════════════════════════
@router.get("/vendedor/resumen")
def resumen_vendedor(
    periodo: str     = Query(default="hoy"),
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    from datetime import date, timedelta

    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id
    ).first()

    if not vendedor:
        return {
            "total_ventas":          0,
            "total_vendido":         0.0,
            "total_fiado":           0.0,
            "total_contado":         0.0,
            "total_cobrado":         0.0,
            "pedidos_entregados":    0,
            "total_pedidos_contado": 0.0,
            "dinero_en_mano":        0.0,
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
    else:
        desde = hoy
        hasta = hoy

    # ── Ventas tradicionales ──────────────────────────
    resultado = db.execute(
        text("""
            SELECT
                COUNT(*)                                     AS total_ventas,
                COALESCE(SUM(monto_total), 0)                AS total_vendido,
                COALESCE(SUM(
                    CASE WHEN tipo = 'credito'
                    THEN monto_total ELSE 0 END), 0)         AS total_fiado,
                COALESCE(SUM(
                    CASE WHEN tipo = 'contado'
                    THEN monto_total ELSE 0 END), 0)         AS total_contado,
                COALESCE((
                    SELECT SUM(p.monto)
                    FROM pagos p
                    WHERE p.vendedor_id      = :vid
                      AND DATE(p.fecha_pago) BETWEEN :desde AND :hasta
                ), 0)                                        AS total_cobrado
            FROM ventas
            WHERE vendedor_id       = :vid
              AND DATE(fecha_venta) BETWEEN :desde AND :hasta
        """),
        {"vid": str(vendedor.id),
         "desde": str(desde), "hasta": str(hasta)},
    ).mappings().first()

    # ── Pedidos entregados por este vendedor ──────────
    # Solo contraentrega cuenta como dinero en mano
    # Los de transferencia ya fueron pagados antes
    pedidos_res = db.execute(
            text("""
                SELECT
                    COUNT(*)                                  AS total_pedidos,
                    COALESCE(SUM(total), 0)                   AS total_monto,
                    COALESCE(SUM(
                        CASE WHEN tipo_pago = 'contraentrega'
                        THEN total ELSE 0 END), 0)            AS total_contraentrega,
                    COALESCE(SUM(
                        CASE WHEN tipo_pago = 'transferencia'
                        THEN total ELSE 0 END), 0)            AS total_transferencia
                FROM pedidos
                WHERE vendedor_id       = :vid
                AND tipo              = 'reserva'           
                AND estado            = 'entregado'
                AND DATE(aceptado_en) BETWEEN :desde AND :hasta
            """),
            {"vid": str(vendedor.id),
            "desde": str(desde), "hasta": str(hasta)},
        ).mappings().first()

    total_contado         = float(resultado["total_contado"])
    total_cobrado         = float(resultado["total_cobrado"])
    total_contraentrega   = float(pedidos_res["total_contraentrega"])
    total_transferencia   = float(pedidos_res["total_transferencia"])
    pedidos_entregados    = int(pedidos_res["total_pedidos"])

    # Dinero en mano = ventas contado + cobros + pedidos contraentrega
    dinero_en_mano = total_contado + total_cobrado + total_contraentrega

    return {
        # Ventas tradicionales
        "total_ventas":          int(resultado["total_ventas"]),
        "total_vendido":         float(resultado["total_vendido"]),
        "total_fiado":           float(resultado["total_fiado"]),
        "total_contado":         total_contado,
        "total_cobrado":         total_cobrado,
        # Pedidos
        "pedidos_entregados":    pedidos_entregados,
        "total_pedidos_contado": total_contraentrega,
        "total_pedidos_transf":  total_transferencia,
        # Total
        "dinero_en_mano":        dinero_en_mano,
    }

# ══════════════════════════════════════════════════════════
#  ADMIN — resumen general del negocio
# ══════════════════════════════════════════════════════════
@router.get("/admin/resumen-general")
def resumen_general(
    periodo: str     = Query(default="hoy"),
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin),
):
    from datetime import date, timedelta

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
    else:
        desde = hoy
        hasta = hoy

    # ── Ventas tradicionales ──────────────────────────
    resultado = db.execute(
        text("""
            SELECT
                COUNT(*)                                     AS total_ventas,
                COALESCE(SUM(monto_total), 0)                AS total_vendido,
                COALESCE(SUM(CASE WHEN tipo = 'contado'
                    THEN monto_total ELSE 0 END), 0)         AS total_contado,
                COALESCE(SUM(CASE WHEN tipo = 'credito'
                    THEN monto_total ELSE 0 END), 0)         AS total_fiado
            FROM ventas
            WHERE DATE(fecha_venta) BETWEEN :desde AND :hasta
        """),
        {"desde": str(desde), "hasta": str(hasta)},
    ).mappings().first()

    cobrado = db.execute(
        text("""
            SELECT COALESCE(SUM(monto), 0) AS total_cobrado
            FROM pagos
            WHERE DATE(fecha_pago) BETWEEN :desde AND :hasta
        """),
        {"desde": str(desde), "hasta": str(hasta)},
    ).mappings().first()

    # ── Pedidos entregados ────────────────────────────
    pedidos = db.execute(
        text("""
            SELECT
                COUNT(*)                                      AS total_pedidos,
                COALESCE(SUM(total), 0)                       AS total_pedidos_monto,
                COALESCE(SUM(CASE WHEN tipo_pago='contraentrega'
                    THEN total ELSE 0 END), 0)                AS pedidos_contraentrega,
                COALESCE(SUM(CASE WHEN tipo_pago='transferencia'
                    THEN total ELSE 0 END), 0)                AS pedidos_transferencia
            FROM pedidos
            WHERE estado = 'entregado'
              AND DATE(aceptado_en) BETWEEN :desde AND :hasta
        """),
        {"desde": str(desde), "hasta": str(hasta)},
    ).mappings().first()

    deudas = db.execute(
        text("""
            SELECT
                COALESCE(SUM(saldo_actual), 0) AS total_deudas,
                COUNT(*)                        AS clientes_con_deuda
            FROM vista_deudas_clientes
            WHERE saldo_actual > 0
        """)
    ).mappings().first()

    total_contado       = float(resultado["total_contado"])
    total_cobrado       = float(cobrado["total_cobrado"])
    pedidos_contado     = float(pedidos["pedidos_contraentrega"])

    return {
        "periodo":                periodo,
        "desde":                  str(desde),
        "hasta":                  str(hasta),
        # Ventas
        "total_ventas":           int(resultado["total_ventas"]),
        "total_vendido":          float(resultado["total_vendido"]),
        "total_contado":          total_contado,
        "total_fiado":            float(resultado["total_fiado"]),
        "total_cobrado":          total_cobrado,
        # Pedidos
        "total_pedidos":          int(pedidos["total_pedidos"]),
        "total_pedidos_monto":    float(pedidos["total_pedidos_monto"]),
        "pedidos_contraentrega":  pedidos_contado,
        "pedidos_transferencia":  float(pedidos["pedidos_transferencia"]),
        # Totales
        "total_deudas":           float(deudas["total_deudas"]),
        "clientes_con_deuda":     int(deudas["clientes_con_deuda"]),
        # Dinero en caja = contado + cobros + pedidos contraentrega
        "dinero_en_caja":         total_contado + total_cobrado + pedidos_contado,
    }


# ══════════════════════════════════════════════════════════
#  ADMIN — ventas por vendedor
# ══════════════════════════════════════════════════════════
@router.get("/admin/ventas-por-vendedor")
def ventas_por_vendedor(
    periodo: str     = Query(default="hoy"),
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin),
):
    from datetime import date, timedelta

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
    else:
        desde = hoy
        hasta = hoy

    resultado = db.execute(
        text("""
            SELECT
                v.id                                              AS vendedor_id,
                v.nombre_completo                                 AS nombre,
                COUNT(ve.id)                                      AS total_ventas,
                COALESCE(SUM(ve.monto_total), 0)                  AS total_vendido,
                COALESCE(SUM(CASE WHEN ve.tipo='contado'
                    THEN ve.monto_total ELSE 0 END), 0)           AS total_contado,
                COALESCE(SUM(CASE WHEN ve.tipo='credito'
                    THEN ve.monto_total ELSE 0 END), 0)           AS total_fiado,
                COALESCE((
                    SELECT SUM(p.monto) FROM pagos p
                    WHERE p.vendedor_id = v.id
                      AND DATE(p.fecha_pago) BETWEEN :desde AND :hasta
                ), 0)                                             AS total_cobrado,
                -- Pedidos de este vendedor (SOLO RESERVAS)
                COALESCE((
                    SELECT COUNT(*) FROM pedidos pe
                    WHERE pe.vendedor_id = v.id
                      AND pe.tipo        = 'reserva'          -- ← FILTRO CRÍTICO
                      AND pe.estado      = 'entregado'
                      AND DATE(pe.aceptado_en) BETWEEN :desde AND :hasta
                ), 0)                                             AS total_pedidos,
                COALESCE((
                    SELECT SUM(pe.total) FROM pedidos pe
                    WHERE pe.vendedor_id = v.id
                      AND pe.tipo        = 'reserva'          -- ← FILTRO CRÍTICO
                      AND pe.estado      = 'entregado'
                      AND DATE(pe.aceptado_en) BETWEEN :desde AND :hasta
                ), 0)                                             AS total_pedidos_monto,
                COALESCE((
                    SELECT SUM(pe.total) FROM pedidos pe
                    WHERE pe.vendedor_id = v.id
                      AND pe.tipo        = 'reserva'          -- ← FILTRO CRÍTICO
                      AND pe.estado      = 'entregado'
                      AND pe.tipo_pago   = 'contraentrega'
                      AND DATE(pe.aceptado_en) BETWEEN :desde AND :hasta
                ), 0)                                             AS pedidos_contraentrega
            FROM vendedores v
            LEFT JOIN ventas ve
                ON ve.vendedor_id = v.id
               AND DATE(ve.fecha_venta) BETWEEN :desde AND :hasta
            WHERE v.esta_activo = TRUE
            GROUP BY v.id, v.nombre_completo
            ORDER BY total_vendido DESC
        """),
        {"desde": str(desde), "hasta": str(hasta)},
    ).mappings().all()

    return [
        {
            "vendedor_id":          str(r["vendedor_id"]),
            "nombre":               r["nombre"],
            "total_ventas":         int(r["total_ventas"]),
            "total_vendido":        float(r["total_vendido"]),
            "total_contado":        float(r["total_contado"]),
            "total_fiado":          float(r["total_fiado"]),
            "total_cobrado":        float(r["total_cobrado"]),
            "total_pedidos":        int(r["total_pedidos"]),
            "total_pedidos_monto":  float(r["total_pedidos_monto"]),
            "pedidos_contraentrega": float(r["pedidos_contraentrega"]),
            # Dinero en mano de este vendedor
            "dinero_en_mano":       float(r["total_contado"]) +
                                    float(r["total_cobrado"]) +
                                    float(r["pedidos_contraentrega"]),
        }
        for r in resultado
    ]
# ══════════════════════════════════════════════════════════
#  ADMIN — productos más vendidos
# ══════════════════════════════════════════════════════════
@router.get("/admin/productos-mas-vendidos")
def productos_mas_vendidos(
    periodo: str     = Query(default="hoy"),
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin),
):
    from datetime import date, timedelta

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
    else:
        desde = hoy
        hasta = hoy

    resultado = db.execute(
        text("""
            SELECT
                p.id                                AS producto_id,
                p.nombre                            AS nombre,
                p.precio                            AS precio_unitario,
                COALESCE(SUM(dv.cantidad), 0)        AS total_cantidad,
                COALESCE(SUM(dv.subtotal), 0)        AS total_ingresos
            FROM productos p
            LEFT JOIN detalle_ventas dv ON dv.producto_id = p.id
            LEFT JOIN ventas v
                ON v.id                  = dv.venta_id
               AND DATE(v.fecha_venta)   BETWEEN :desde AND :hasta
            WHERE p.esta_activo = TRUE
            GROUP BY p.id, p.nombre, p.precio
            ORDER BY total_cantidad DESC
            LIMIT 10
        """),
        {"desde": str(desde), "hasta": str(hasta)},
    ).mappings().all()

    return [
        {
            "producto_id":    str(r["producto_id"]),
            "nombre":         r["nombre"],
            "precio_unitario": float(r["precio_unitario"]),
            "total_cantidad": int(r["total_cantidad"]),
            "total_ingresos": float(r["total_ingresos"]),
        }
        for r in resultado
    ]


# ══════════════════════════════════════════════════════════
#  ADMIN — clientes con más deuda
# ══════════════════════════════════════════════════════════
@router.get("/admin/deudas")
def reporte_deudas(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin),
):
    resultado = db.execute(
        text("""
            SELECT * FROM vista_deudas_clientes
            WHERE saldo_actual > 0
            ORDER BY saldo_actual DESC
        """)
    ).mappings().all()
    return [dict(r) for r in resultado]

@router.get("/vendedor/resumen-fechas")
def resumen_vendedor_fechas(
    desde:   str     = Query(...),
    hasta:   str     = Query(...),
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    vendedor = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id
    ).first()
    if not vendedor:
        return {
            "total_ventas": 0, "total_vendido": 0.0,
            "total_fiado": 0.0, "total_contado": 0.0,
            "total_cobrado": 0.0, "pedidos_entregados": 0,
            "total_pedidos_contado": 0.0,
            "total_pedidos_transf": 0.0, "dinero_en_mano": 0.0,
        }

    resultado = db.execute(
        text("""
            SELECT
                COUNT(*)                                     AS total_ventas,
                COALESCE(SUM(monto_total), 0)                AS total_vendido,
                COALESCE(SUM(CASE WHEN tipo='credito'
                    THEN monto_total ELSE 0 END), 0)         AS total_fiado,
                COALESCE(SUM(CASE WHEN tipo='contado'
                    THEN monto_total ELSE 0 END), 0)         AS total_contado,
                COALESCE((
                    SELECT SUM(p.monto) FROM pagos p
                    WHERE p.vendedor_id = :vid
                      AND DATE(p.fecha_pago) BETWEEN :desde AND :hasta
                ), 0)                                        AS total_cobrado
            FROM ventas
            WHERE vendedor_id = :vid
              AND DATE(fecha_venta) BETWEEN :desde AND :hasta
        """),
        {"vid": str(vendedor.id), "desde": desde, "hasta": hasta},
    ).mappings().first()

    pedidos_res = db.execute(
            text("""
                SELECT
                    COUNT(*)                                  AS total_pedidos,
                    COALESCE(SUM(CASE WHEN tipo_pago='contraentrega'
                        THEN total ELSE 0 END), 0)            AS total_contraentrega,
                    COALESCE(SUM(CASE WHEN tipo_pago='transferencia'
                        THEN total ELSE 0 END), 0)            AS total_transferencia
                FROM pedidos
                WHERE vendedor_id = :vid
                AND tipo        = 'reserva'                 
                AND estado      = 'entregado'
                AND DATE(aceptado_en) BETWEEN :desde AND :hasta
            """),
            {"vid": str(vendedor.id), "desde": desde, "hasta": hasta},
        ).mappings().first()

    total_contado       = float(resultado["total_contado"])
    total_cobrado       = float(resultado["total_cobrado"])
    total_contraentrega = float(pedidos_res["total_contraentrega"])

    return {
        "total_ventas":          int(resultado["total_ventas"]),
        "total_vendido":         float(resultado["total_vendido"]),
        "total_fiado":           float(resultado["total_fiado"]),
        "total_contado":         total_contado,
        "total_cobrado":         total_cobrado,
        "pedidos_entregados":    int(pedidos_res["total_pedidos"]),
        "total_pedidos_contado": total_contraentrega,
        "total_pedidos_transf":  float(pedidos_res["total_transferencia"]),
        "dinero_en_mano":        total_contado + total_cobrado + total_contraentrega,
    }