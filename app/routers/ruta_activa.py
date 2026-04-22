from datetime  import date, datetime, timezone
from math      import radians, sin, cos, sqrt, atan2
from typing    import List, Optional
from uuid      import UUID

from fastapi        import APIRouter, Depends, HTTPException
from pydantic       import BaseModel
from sqlalchemy     import text
from sqlalchemy.orm import Session

from app.database          import get_db
from app.models.ruta_activa import StockDiario, SesionRuta, VisitaVerificada
from app.models.producto   import Producto
from app.models.vendedor   import Vendedor
from app.models.usuario    import Usuario
from app.core.dependencies import requiere_vendedor

router = APIRouter(prefix="/ruta-activa", tags=["Ruta Activa"])

# ── Constantes de verificación ────────────────────────────
DISTANCIA_MAX_METROS = 150   # radio para considerar "en empresa"
MINUTOS_MIN_ESTADIA  = 3     # tiempo mínimo en zona para marcar


# ══════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════
def _haversine(lat1: float, lon1: float,
               lat2: float, lon2: float) -> float:
    """Distancia en metros entre dos coordenadas."""
    R = 6371000
    φ1, φ2 = radians(lat1), radians(lat2)
    dφ = radians(lat2 - lat1)
    dλ = radians(lon2 - lon1)
    a = sin(dφ/2)**2 + cos(φ1)*cos(φ2)*sin(dλ/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))


def _get_vendedor(db: Session, usuario: Usuario) -> Vendedor:
    v = db.query(Vendedor).filter(
        Vendedor.usuario_id == usuario.id
    ).first()
    if not v:
        print(f"[DEBUG] No se encontró vendedor para usuario_id: {usuario.id}")
        raise HTTPException(404, "Vendedor no encontrado.")
    return v

# ══════════════════════════════════════════════════════════
#  SCHEMAS
# ══════════════════════════════════════════════════════════
class ItemStock(BaseModel):
    producto_id: str
    cantidad:    int

class GuardarStockBody(BaseModel):
    items: List[ItemStock]

class IniciarRutaBody(BaseModel):
    asignacion_id: str
    lat:           float
    lng:           float

class RegistrarLlegadaBody(BaseModel):
    sesion_id:  str
    empresa_id: str
    lat:        float
    lng:        float

class MarcarVisitadaBody(BaseModel):
    sesion_id:  str
    empresa_id: str
    lat:        float
    lng:        float

class CompletarRutaBody(BaseModel):
    sesion_id: str


# ══════════════════════════════════════════════════════════
#  GET /ruta-activa/estado-hoy
#  Estado completo del vendedor para hoy
# ══════════════════════════════════════════════════════════
@router.get("/estado-hoy")
def estado_hoy(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    vendedor = _get_vendedor(db, usuario)
    hoy      = date.today()

    # ── Limpiar stock de días anteriores (más de 1 día) ──────
    db.execute(text("""
        DELETE FROM stock_diario
        WHERE vendedor_id = :vid
          AND fecha < :hoy
    """), {"vid": str(vendedor.id), "hoy": str(hoy)})
    db.commit()

    # ── Ruta asignada hoy ─────────────────────────────────
    ruta_row = db.execute(text("""
        SELECT
            ra.id            AS asignacion_id,
            ra.turno,
            r.id             AS ruta_id,
            r.nombre         AS ruta_nombre,
            r.descripcion    AS ruta_descripcion
        FROM ruta_asignaciones ra
        JOIN rutas r ON r.id = ra.ruta_id
        WHERE ra.vendedor_id = :vid
        AND ra.esta_activa = TRUE
        AND r.esta_activa  = TRUE
        LIMIT 1
    """), {"vid": str(vendedor.id)}).mappings().first()

    if not ruta_row:
        return {"tiene_ruta": False}

    # ── Stock llenado hoy ─────────────────────────────────
    stock = db.query(StockDiario).filter(
        StockDiario.vendedor_id == vendedor.id,
        StockDiario.fecha       == hoy,
    ).all()
    stock_lleno = len(stock) > 0

    # ── Sesión de ruta hoy ────────────────────────────────
    sesion = db.query(SesionRuta).filter(
        SesionRuta.asignacion_id == ruta_row["asignacion_id"],
        SesionRuta.fecha         == hoy,
    ).first()

    # ── Empresas de la ruta ───────────────────────────────
    empresas_rows = db.execute(text("""
        SELECT
            e.id, e.nombre, e.direccion,
            e.latitud, e.longitud,
            re.orden
        FROM ruta_empresas re
        JOIN empresas e ON e.id = re.empresa_id
        WHERE re.ruta_id = :rid
        ORDER BY re.orden
    """), {"rid": str(ruta_row["ruta_id"])}).mappings().all()

# ── Visitas de hoy ────────────────────────────────────────
    visitas_map = {}  # empresa_id → {visitada, llegada_en}
    if sesion:
            visitas = db.query(VisitaVerificada).filter(
                VisitaVerificada.sesion_id == sesion.id,
            ).all()
            for v in visitas:
                visitas_map[str(v.empresa_id)] = {
                    "es_valida":  v.es_valida,
                    "llegada_en": v.llegada_en.isoformat()
                                if v.llegada_en else None,
                }

    visitas_ids = {
            eid for eid, v in visitas_map.items()
            if v["es_valida"]
        }

    empresas = [
            {
                "id":        str(e["id"]),
                "nombre":    e["nombre"],
                "direccion": e["direccion"],
                "latitud":   float(e["latitud"])  if e["latitud"]  else None,
                "longitud":  float(e["longitud"]) if e["longitud"] else None,
                "orden":     e["orden"],
                "visitada":  str(e["id"]) in visitas_ids,
                # NUEVO: si tiene llegada registrada pero no validada aún
                "llegada_en": visitas_map.get(str(e["id"]), {}).get("llegada_en"),
            }
            for e in empresas_rows
        ]

    total     = len(empresas)
    visitadas = len(visitas_ids)

    # ── CORRECCIÓN: ruta_completada separado de completada ──
    # ruta_completada = sesión cerrada por el vendedor
    # completada      = todas las empresas visitadas
    sesion_completada = sesion is not None and sesion.estado == "completada"

    return {
        "tiene_ruta":       True,
        "stock_lleno":      stock_lleno,
        "asignacion_id":    str(ruta_row["asignacion_id"]),
        "ruta_id":          str(ruta_row["ruta_id"]),
        "ruta_nombre":      ruta_row["ruta_nombre"],
        "turno":            ruta_row["turno"],
        "sesion": {
            "id":        str(sesion.id),
            "estado":    sesion.estado,
            "iniciada_en": sesion.iniciada_en.isoformat(),
        } if sesion else None,
        "empresas":         empresas,
        "total":            total,
        "visitadas":        visitadas,
        "completada":       visitadas >= total and total > 0,
        "sesion_completada": sesion_completada,  # ← NUEVO
    }


# ══════════════════════════════════════════════════════════
#  GET /ruta-activa/stock-hoy
# ══════════════════════════════════════════════════════════
@router.get("/stock-hoy")
def stock_hoy(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    vendedor = _get_vendedor(db, usuario)
    hoy      = date.today()

    # Todos los productos activos
    productos = db.query(Producto).filter(
        Producto.esta_activo == True
    ).order_by(Producto.nombre).all()

    # Stock ya registrado hoy
    stock_map = {}
    stock_rows = db.query(StockDiario).filter(
        StockDiario.vendedor_id == vendedor.id,
        StockDiario.fecha       == hoy,
    ).all()
    for s in stock_rows:
        stock_map[str(s.producto_id)] = s.cantidad

    return [
        {
            "producto_id": str(p.id),
            "nombre":      p.nombre,
            "precio":      float(p.precio),
            "imagen_url":  p.imagen_url,
            "cantidad":    stock_map.get(str(p.id), 0),
        }
        for p in productos
    ]


# ══════════════════════════════════════════════════════════
#  POST /ruta-activa/guardar-stock
# ══════════════════════════════════════════════════════════
@router.post("/guardar-stock")
def guardar_stock(
    body:    GuardarStockBody,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    vendedor = _get_vendedor(db, usuario)
    hoy      = date.today()

    if not body.items:
        raise HTTPException(400, "Debes agregar al menos un producto.")

    total = sum(i.cantidad for i in body.items)
    if total <= 0:
        raise HTTPException(400, "Debes ingresar al menos una unidad.")

    sesion_activa = db.query(SesionRuta).filter(
        SesionRuta.vendedor_id == vendedor.id,
        SesionRuta.fecha       == hoy,
        SesionRuta.estado      == 'iniciada',
    ).first()
    if sesion_activa:
        raise HTTPException(
            400,
            "No puedes modificar el stock mientras la ruta está activa.")

    # Limpiar stock del día
    db.query(StockDiario).filter(
        StockDiario.vendedor_id == vendedor.id,
        StockDiario.fecha       == hoy,
    ).delete()
    db.flush()

    for item in body.items:
        if item.cantidad < 0:
            raise HTTPException(
                400, "Las cantidades no pueden ser negativas.")
        if item.cantidad > 0:
            db.add(StockDiario(
                vendedor_id = vendedor.id,
                fecha       = hoy,
                producto_id = item.producto_id,
                cantidad    = item.cantidad,
            ))

    db.commit()
    return {"mensaje": "Stock guardado correctamente."}
# ══════════════════════════════════════════════════════════
#  POST /ruta-activa/iniciar
# ══════════════════════════════════════════════════════════
@router.post("/iniciar")
def iniciar_ruta(
    body:    IniciarRutaBody,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    vendedor = _get_vendedor(db, usuario)
    hoy      = date.today()

    # Verificar stock
    stock = db.query(StockDiario).filter(
        StockDiario.vendedor_id == vendedor.id,
        StockDiario.fecha       == hoy,
    ).first()
    if not stock:
        raise HTTPException(
            400, "Debes llenar tu stock antes de iniciar la ruta.")

    # Verificar que no existe sesión hoy
    existente = db.query(SesionRuta).filter(
        SesionRuta.asignacion_id == body.asignacion_id,
        SesionRuta.fecha         == hoy,
    ).first()
    if existente:
        return {
            "sesion_id": str(existente.id),
            "estado":    existente.estado,
            "mensaje":   "Ruta ya iniciada.",
        }

    sesion = SesionRuta(
        asignacion_id = body.asignacion_id,
        vendedor_id   = vendedor.id,
        lat_inicio    = body.lat,
        lng_inicio    = body.lng,
    )
    db.add(sesion)
    db.commit()
    db.refresh(sesion)

    return {
        "sesion_id": str(sesion.id),
        "estado":    sesion.estado,
        "mensaje":   "Ruta iniciada correctamente.",
    }


# ══════════════════════════════════════════════════════════
#  POST /ruta-activa/registrar-llegada
#  Vendedor llega a empresa (GPS ≤150m) → guarda timestamp
# ══════════════════════════════════════════════════════════
@router.post("/registrar-llegada")
def registrar_llegada(
    body:    RegistrarLlegadaBody,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    vendedor = _get_vendedor(db, usuario)

    sesion = db.query(SesionRuta).filter(
        SesionRuta.id == body.sesion_id
    ).first()
    if not sesion:
        raise HTTPException(404, "Sesión no encontrada.")

    # Verificar distancia
    empresa = db.execute(text(
        "SELECT latitud, longitud FROM empresas WHERE id = :eid"
    ), {"eid": body.empresa_id}).mappings().first()

    if not empresa or not empresa["latitud"]:
        raise HTTPException(
            400, "Empresa sin coordenadas GPS.")

    dist = int(_haversine(
        body.lat, body.lng,
        float(empresa["latitud"]),
        float(empresa["longitud"]),
    ))

    if dist > DISTANCIA_MAX_METROS:
        raise HTTPException(
            400,
            f"Estás a {dist}m de la empresa. "
            f"Debes estar a menos de {DISTANCIA_MAX_METROS}m."
        )

    # Upsert visita — solo si no existe
    existente = db.query(VisitaVerificada).filter(
        VisitaVerificada.sesion_id  == body.sesion_id,
        VisitaVerificada.empresa_id == body.empresa_id,
    ).first()

    if not existente:
        db.add(VisitaVerificada(
            sesion_id        = body.sesion_id,
            empresa_id       = body.empresa_id,
            vendedor_id      = vendedor.id,
            llegada_en       = datetime.now(timezone.utc),
            lat_verificada   = body.lat,
            lng_verificada   = body.lng,
            distancia_metros = dist,
        ))
        db.commit()

    return {
        "mensaje":   "Llegada registrada.",
        "distancia": dist,
        "llegada_en": datetime.now(timezone.utc).isoformat(),
    }


# ══════════════════════════════════════════════════════════
#  POST /ruta-activa/marcar-visitada
#  Verifica GPS + tiempo mínimo antes de marcar
# ══════════════════════════════════════════════════════════
@router.post("/marcar-visitada")
def marcar_visitada(
    body:    MarcarVisitadaBody,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    visita = db.query(VisitaVerificada).filter(
        VisitaVerificada.sesion_id  == body.sesion_id,
        VisitaVerificada.empresa_id == body.empresa_id,
    ).first()

    if not visita:
        raise HTTPException(
            400,
            "Primero debes registrar tu llegada a esta empresa.")

    if visita.es_valida:
        return {"mensaje": "Ya marcada como visitada.", "valida": True}

    # Verificar distancia actual
    empresa = db.execute(text(
        "SELECT latitud, longitud FROM empresas WHERE id = :eid"
    ), {"eid": body.empresa_id}).mappings().first()

    dist = int(_haversine(
        body.lat, body.lng,
        float(empresa["latitud"]),
        float(empresa["longitud"]),
    ))

    if dist > DISTANCIA_MAX_METROS:
        raise HTTPException(
            400,
            f"Estás a {dist}m. Debes estar a ≤{DISTANCIA_MAX_METROS}m "
            f"para marcar como visitada."
        )

    # Verificar tiempo mínimo
    ahora    = datetime.now(timezone.utc)
    llegada  = visita.llegada_en
    if llegada.tzinfo is None:
        from datetime import timezone as tz
        llegada = llegada.replace(tzinfo=tz.utc)

    minutos = int((ahora - llegada).total_seconds() / 60)

    if minutos < MINUTOS_MIN_ESTADIA:
        faltan = MINUTOS_MIN_ESTADIA - minutos
        raise HTTPException(
            400,
            f"Debes permanecer al menos {MINUTOS_MIN_ESTADIA} minutos "
            f"en la empresa. Faltan {faltan} minuto(s)."
        )

    # Marcar como válida
    visita.marcada_en       = ahora
    visita.minutos_estadia  = minutos
    visita.distancia_metros = dist
    visita.es_valida        = True
    db.commit()

    return {
        "mensaje":         "Empresa marcada como visitada ✅",
        "valida":          True,
        "minutos_estadia": minutos,
        "distancia":       dist,
    }


# ══════════════════════════════════════════════════════════
#  POST /ruta-activa/completar
# ══════════════════════════════════════════════════════════
@router.post("/completar")
def completar_ruta(
    body:    CompletarRutaBody,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    sesion = db.query(SesionRuta).filter(
        SesionRuta.id == body.sesion_id
    ).first()
    if not sesion:
        raise HTTPException(404, "Sesión no encontrada.")

    sesion.estado        = "completada"
    sesion.completada_en = datetime.now(timezone.utc)
    db.commit()

    return {"mensaje": "Ruta completada.", "estado": "completada"}


# ══════════════════════════════════════════════════════════
#  GET /ruta-activa/resumen/{sesion_id}
#  Resumen final de la ruta
# ══════════════════════════════════════════════════════════
@router.get("/resumen/{sesion_id}")
def resumen_ruta(
    sesion_id: str,
    db:        Session = Depends(get_db),
    usuario:   Usuario = Depends(requiere_vendedor),
):
    vendedor = _get_vendedor(db, usuario)
    hoy      = date.today()

    sesion = db.query(SesionRuta).filter(
        SesionRuta.id == sesion_id
    ).first()
    if not sesion:
        raise HTTPException(404, "Sesión no encontrada.")

    # Ventas del día
    ventas = db.execute(text("""
        SELECT
            COUNT(*)                                    AS total_ventas,
            COALESCE(SUM(monto_total), 0)               AS total_vendido,
            COALESCE(SUM(CASE WHEN tipo='contado'
                THEN monto_total ELSE 0 END), 0)        AS total_contado,
            COALESCE(SUM(CASE WHEN tipo='credito'
                THEN monto_total ELSE 0 END), 0)        AS total_fiado
        FROM ventas
        WHERE vendedor_id     = :vid
          AND DATE(fecha_venta) = :hoy
    """), {"vid": str(vendedor.id), "hoy": str(hoy)}).mappings().first()

    # Cobros del día
    cobros = db.execute(text("""
        SELECT COALESCE(SUM(monto), 0) AS total_cobrado
        FROM pagos
        WHERE vendedor_id      = :vid
          AND DATE(fecha_pago) = :hoy
    """), {"vid": str(vendedor.id), "hoy": str(hoy)}).mappings().first()

    # Stock inicial
    stock = db.query(StockDiario).filter(
        StockDiario.vendedor_id == vendedor.id,
        StockDiario.fecha       == hoy,
    ).all()
    stock_items = [
        {
            "producto":  s.producto.nombre if s.producto else "",
            "cantidad":  s.cantidad,
            "precio":    float(s.producto.precio) if s.producto else 0,
            "total":     s.cantidad * float(s.producto.precio)
                         if s.producto else 0,
        }
        for s in stock
    ]
    stock_total = sum(i["total"] for i in stock_items)

    # Visitas
    visitas = db.query(VisitaVerificada).filter(
        VisitaVerificada.sesion_id == sesion_id,
        VisitaVerificada.es_valida == True,
    ).all()

    total_contado = float(ventas["total_contado"])
    total_cobrado = float(cobros["total_cobrado"])

    return {
        "sesion_id":       sesion_id,
        "fecha":           str(hoy),
        "empresas_visitadas": len(visitas),
        "total_ventas":    int(ventas["total_ventas"]),
        "total_vendido":   float(ventas["total_vendido"]),
        "total_contado":   total_contado,
        "total_fiado":     float(ventas["total_fiado"]),
        "total_cobrado":   total_cobrado,
        "dinero_en_mano":  total_contado + total_cobrado,
        "stock_inicial":   stock_items,
        "stock_total_valor": stock_total,
        "duracion_minutos": int(
            (sesion.completada_en - sesion.iniciada_en).total_seconds() / 60
        ) if sesion.completada_en else None,
    }

# ══════════════════════════════════════════════════════════
#  GET /ruta-activa/stock-restante
#  Stock inicial del día MENOS lo ya vendido hoy
# ══════════════════════════════════════════════════════════
@router.get("/stock-restante")
def stock_restante(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    vendedor = _get_vendedor(db, usuario)
    hoy      = date.today()

    stock_inicial = db.execute(text("""
        SELECT
            sd.producto_id,
            p.nombre,
            p.precio,
            p.imagen_url,
            sd.cantidad     AS cantidad_inicial,
            sd.creado_en    AS stock_creado_en
        FROM stock_diario sd
        JOIN productos p ON p.id = sd.producto_id
        WHERE sd.vendedor_id = :vid
          AND sd.fecha = :hoy
    """), {"vid": str(vendedor.id), "hoy": str(hoy)}).mappings().all()

    if not stock_inicial:
        return {
            "productos":      [],
            "total_restante": 0,
            "sin_stock":      False,
            "stock_cargado":  False,
        }

    # Timestamp más antiguo del stock de hoy
    # Solo contar ventas DESPUÉS de que se cargó el stock
    stock_creado_en = min(
        s["stock_creado_en"] for s in stock_inicial
    )

    # ── Ventas solo DESPUÉS de que se guardó el stock ────
    vendidas = db.execute(text("""
        SELECT
            dv.producto_id,
            COALESCE(SUM(dv.cantidad), 0) AS vendidas
        FROM detalle_ventas dv
        JOIN ventas v ON v.id = dv.venta_id
        WHERE v.vendedor_id = :vid
          AND v.fecha_venta >= :desde
          AND v.fecha_venta::date = :hoy
        GROUP BY dv.producto_id
    """), {
        "vid":   str(vendedor.id),
        "hoy":   str(hoy),
        "desde": stock_creado_en,
    }).mappings().all()

    vendidas_map = {
        str(r["producto_id"]): int(r["vendidas"])
        for r in vendidas
    }

    resultado      = []
    total_restante = 0

    for s in stock_inicial:
        pid      = str(s["producto_id"])
        inicial  = int(s["cantidad_inicial"])
        vendido  = vendidas_map.get(pid, 0)
        restante = max(0, inicial - vendido)
        total_restante += restante
        resultado.append({
            "producto_id":       pid,
            "nombre":            s["nombre"],
            "precio":            float(s["precio"]),
            "imagen_url":        s["imagen_url"],
            "cantidad_inicial":  inicial,
            "cantidad_vendida":  vendido,
            "cantidad_restante": restante,
        })

    return {
        "productos":      resultado,
        "total_restante": total_restante,
        "sin_stock":      total_restante == 0,
        "stock_cargado":  True,
    }