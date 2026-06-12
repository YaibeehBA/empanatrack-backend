from datetime  import date, datetime, timezone
from math      import radians, sin, cos, sqrt, atan2
from typing    import List, Optional
from uuid      import UUID

import json
import logging

from fastapi        import APIRouter, Depends, HTTPException, logger
from pydantic       import BaseModel
from sqlalchemy     import text
from sqlalchemy.orm import Session

from app.database          import get_db
from app.models.ruta_activa import StockDiario, SesionRuta, VisitaVerificada
from app.services.websocket_manager import ws_manager
from app.models.ruta_activa import RecargaStock
from app.models.producto   import Producto
from app.models.vendedor   import Vendedor
from app.models.usuario    import Usuario
from app.core.dependencies import requiere_admin, requiere_vendedor


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ruta-activa", tags=["Ruta Activa"])

# ── Constantes de verificación ────────────────────────────
DISTANCIA_MAX_METROS = 150   # radio para considerar "en empresa"
MINUTOS_MIN_ESTADIA  = 3     # tiempo mínimo en zona para marcar
MAX_RECARGAS_POR_SESION = 5

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

class ItemRecargaSolicitado(BaseModel):
    producto_id: str
    nombre:      str
    cantidad:    int
    precio:      float

class SolicitarRecargaBody(BaseModel):
    sesion_id: str
    productos: List[ItemRecargaSolicitado]
    notas:     Optional[str] = None

class ResponderRecargaBody(BaseModel):
    recarga_id:        str
    accion:            str   # "aceptar" | "rechazar"
    productos:         Optional[List[ItemRecargaSolicitado]] = None
    lat_recarga:       Optional[float]  = None
    lng_recarga:       Optional[float]  = None
    direccion_recarga: Optional[str]    = None
    notas_admin:       Optional[str]    = None

class CompletarRecargaBody(BaseModel):
    recarga_id: str
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
   
# ── Liberar reservas no entregadas de esta empresa ───────
    from app.models.pedido import Pedido as PedidoModel, PedidoItem

    reservas_pendientes = db.query(PedidoModel).filter(
        PedidoModel.empresa_id == body.empresa_id,
        PedidoModel.tipo       == "reserva",
        PedidoModel.estado     == "aceptado",
        # Solo las del vendedor de esta sesión
        PedidoModel.vendedor_id == visita.vendedor_id,
    ).all()

    for reserva in reservas_pendientes:
        _liberar_y_cancelar(db, reserva, visita.vendedor_id, date.today())

    db.commit()  

    return {
        "mensaje":         "Empresa marcada como visitada ✅",
        "valida":          True,
        "minutos_estadia": minutos,
        "distancia":       dist,
    }

def _liberar_y_cancelar(db, pedido, vendedor_id, hoy):
    """Cancela reserva y devuelve unidades al stock."""
    from app.models.pedido import PedidoItem
    for item in pedido.items:
        stock = db.query(StockDiario).filter(
            StockDiario.vendedor_id == vendedor_id,
            StockDiario.producto_id == item.producto_id,
            StockDiario.fecha       == hoy,
        ).first()
        if stock and stock.cantidad_reservada > 0:
            stock.cantidad_reservada = max(
                0, stock.cantidad_reservada - item.cantidad)
    pedido.estado = "cancelado"
    
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

    # Verificar si hay stock registrado hoy (incluyendo cantidad_reservada)
    stock_rows = db.execute(text("""
        SELECT
            sd.producto_id,
            p.nombre,
            p.precio,
            p.imagen_url,
            sd.cantidad     AS cantidad_inicial,
            sd.cantidad_reservada AS cantidad_reservada,
            sd.creado_en    AS stock_creado_en
        FROM stock_diario sd
        JOIN productos p ON p.id = sd.producto_id
        WHERE sd.vendedor_id = :vid
          AND sd.fecha = :hoy
    """), {"vid": str(vendedor.id), "hoy": str(hoy)}).mappings().all()

    if not stock_rows:
        return {
            "productos":      [],
            "total_restante": 0,
            "sin_stock":      False,
            "stock_cargado":  False,
        }

    # Timestamp más antiguo del stock → solo ventas después de cargar
    stock_creado_en = min(s["stock_creado_en"] for s in stock_rows)

    # Ventas después de cargar el stock
    vendidas = db.execute(text("""
        SELECT
            dv.producto_id,
            COALESCE(SUM(dv.cantidad), 0) AS vendidas
        FROM detalle_ventas dv
        JOIN ventas v ON v.id = dv.venta_id
        WHERE v.vendedor_id  = :vid
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

    # Todos los productos activos para saber cuáles no
    # fueron incluidos en el stock (tratarlos como 0)
    todos_productos = db.execute(text("""
        SELECT id, nombre, precio, imagen_url
        FROM productos
        WHERE esta_activo = TRUE
    """)).mappings().all()

    # Mapa de stock registrado hoy
    stock_map = {
        str(s["producto_id"]): s for s in stock_rows
    }

    resultado      = []
    total_restante = 0

    for p in todos_productos:
        pid = str(p["id"])
        s   = stock_map.get(pid)

        if s is None:
            # Producto activo pero NO incluido en stock del día
            resultado.append({
                "producto_id":        pid,
                "nombre":             p["nombre"],
                "precio":             float(p["precio"]),
                "imagen_url":         p["imagen_url"],
                "cantidad_inicial":   0,
                "cantidad_vendida":   0,
                "cantidad_reservada": 0,  # ← NUEVO
                "cantidad_restante":  0,
                "en_stock_hoy":       False,
            })
        else:
            # ← AQUÍ ES DONDE HACES LA MODIFICACIÓN PRINCIPAL
            inicial   = int(s["cantidad_inicial"])
            reservado = int(s.get("cantidad_reservada", 0))  # ← NUEVO: obtener reservas
            vendido   = vendidas_map.get(pid, 0)
            
            # Disponible = inicial - vendido - reservado
            restante  = max(0, inicial - vendido - reservado)  # ← MODIFICADO: incluir reservado
            total_restante += restante
            
            resultado.append({
                "producto_id":        pid,
                "nombre":             p["nombre"],
                "precio":             float(p["precio"]),
                "imagen_url":         p["imagen_url"],
                "cantidad_inicial":   inicial,
                "cantidad_vendida":   vendido,
                "cantidad_reservada": reservado,   # ← NUEVO
                "cantidad_restante":  restante,    # ← MODIFICADO: usa la nueva variable
                "en_stock_hoy":       True,
            })

    return {
        "productos":      resultado,
        "total_restante": total_restante,
        "sin_stock":      total_restante == 0,
        "stock_cargado":  True,
    }

# ══════════════════════════════════════════════════════════
#  POST /ruta-activa/solicitar-recarga
# ══════════════════════════════════════════════════════════
@router.post("/solicitar-recarga")
async def solicitar_recarga(
    body:    SolicitarRecargaBody,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    """
    Vendedor solicita recarga con lista de productos.
    Guarda productos_solicitados como JSON.
    """
    vendedor = _get_vendedor(db, usuario)
 
    sesion = db.query(SesionRuta).filter(
        SesionRuta.id    == body.sesion_id,
        SesionRuta.estado == "iniciada",
    ).first()
    if not sesion:
        raise HTTPException(404, "Sesión no encontrada o no activa.")
 
    if str(sesion.vendedor_id) != str(vendedor.id):
        raise HTTPException(403, "No tienes acceso a esta sesión.")
 
    # Verificar límite de recargas
    total_recargas = db.query(RecargaStock).filter(
        RecargaStock.sesion_id == body.sesion_id,
    ).count()
    if total_recargas >= MAX_RECARGAS_POR_SESION:
        raise HTTPException(
            400,
            f"Has alcanzado el límite de {MAX_RECARGAS_POR_SESION} recargas.")
 
    # Verificar que no hay recarga pendiente o aceptada
    recarga_activa = db.query(RecargaStock).filter(
        RecargaStock.sesion_id == body.sesion_id,
        RecargaStock.estado.in_(["pendiente", "aceptada"]),
    ).first()
    if recarga_activa:
        raise HTTPException(
            400,
            "Ya tienes una solicitud de recarga activa.")
 
    # Guardar productos solicitados como JSON
    productos_json = json.dumps([
        {
            "producto_id": p.producto_id,
            "nombre":      p.nombre,
            "cantidad":    p.cantidad,
            "precio":      p.precio,
        }
        for p in body.productos
    ])
 
    recarga = RecargaStock(
        sesion_id             = body.sesion_id,
        vendedor_id           = vendedor.id,
        productos_solicitados = productos_json,
        notas_admin           = body.notas,
    )
    db.add(recarga)
    db.commit()
    db.refresh(recarga)
 
    # Notificar a admins por WS
    recargas_usadas = total_recargas + 1
    try:
        productos_data = json.loads(productos_json)
    except:
        productos_data = []
 
    mensaje_admin = {
        "tipo":              "solicitud_recarga",
        "recarga_id":        str(recarga.id),
        "sesion_id":         body.sesion_id,
        "vendedor_nombre":   vendedor.nombre_completo,
        "vendedor_id":       str(vendedor.id),
        "productos":         productos_data,
        "recargas_usadas":   recargas_usadas,
        "recargas_max":      MAX_RECARGAS_POR_SESION,
        "notas":             body.notas,
        "solicitado_en":     recarga.solicitado_en.isoformat(),
    }
    await ws_manager.notificar_todos_admins(mensaje_admin)
 
    logger.info(
        f"Vendedor {vendedor.id} solicitó recarga con "
        f"{len(body.productos)} productos")
 
    return {
        "recarga_id":      str(recarga.id),
        "estado":          recarga.estado,
        "recargas_usadas": recargas_usadas,
        "recargas_max":    MAX_RECARGAS_POR_SESION,
        "mensaje":         "Solicitud enviada con los productos.",
    }
 
 
# ══════════════════════════════════════════════════════════
#  POST /ruta-activa/responder-recarga  (admin)
# ══════════════════════════════════════════════════════════
@router.post("/responder-recarga")
async def responder_recarga(
    body:    ResponderRecargaBody,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin),
):
    """
    Admin aprueba o rechaza recarga.
    Si aprueba, puede modificar cantidades en productos.
    Guarda productos_aprobados como JSON.
    """
    if body.accion not in ("aceptar", "rechazar"):
        raise HTTPException(400, "Acción inválida.")
 
    recarga = db.query(RecargaStock).filter(
        RecargaStock.id == body.recarga_id,
    ).first()
    if not recarga:
        raise HTTPException(404, "Solicitud no encontrada.")
    if recarga.estado != "pendiente":
        raise HTTPException(400, "La solicitud ya fue procesada.")
 
    if body.accion == "aceptar":
        if not body.lat_recarga or not body.lng_recarga:
            raise HTTPException(
                400, "Debes proporcionar lat_recarga y lng_recarga.")
 
        # Guardar productos aprobados (puede modificar cantidades)
        productos_aprobados_json = json.dumps([
            {
                "producto_id": p.producto_id,
                "nombre":      p.nombre,
                "cantidad":    p.cantidad,
                "precio":      p.precio,
            }
            for p in (body.productos or [])
        ])
 
        recarga.estado              = "aceptada"
        recarga.productos_aprobados = productos_aprobados_json
        recarga.lat_recarga         = body.lat_recarga
        recarga.lng_recarga         = body.lng_recarga
        recarga.direccion_recarga   = body.direccion_recarga
        recarga.notas_admin         = body.notas_admin
    else:
        recarga.estado      = "rechazada"
        recarga.notas_admin = body.notas_admin
 
    recarga.respondido_en = datetime.now(timezone.utc)
    db.commit()
 
    # Notificar al vendedor
    vendedor = recarga.vendedor
    if vendedor and vendedor.usuario_id:
        productos_data = []
        if recarga.productos_aprobados:
            try:
                productos_data = json.loads(recarga.productos_aprobados)
            except:
                pass
 
        mensaje_vendedor = {
            "tipo":               "recarga_respondida",
            "recarga_id":         str(recarga.id),
            "estado":             recarga.estado,
            "productos_aprobados": productos_data,
            "lat_recarga":        float(recarga.lat_recarga)
                                  if recarga.lat_recarga else None,
            "lng_recarga":        float(recarga.lng_recarga)
                                  if recarga.lng_recarga else None,
            "direccion_recarga":  recarga.direccion_recarga,
            "notas_admin":        recarga.notas_admin,
        }
        await ws_manager.notificar_vendedor(
            str(vendedor.usuario_id), mensaje_vendedor)
 
    logger.info(f"Admin respondió recarga {recarga.id}: {body.accion}")
 
    return {
        "recarga_id": str(recarga.id),
        "estado":     recarga.estado,
        "mensaje":    "Respuesta enviada al vendedor.",
    }
 
 
# ══════════════════════════════════════════════════════════
#  POST /ruta-activa/completar-recarga
#  Vendedor confirma que recibió → actualiza stock
# ══════════════════════════════════════════════════════════
@router.post("/completar-recarga")
def completar_recarga(
    body:    CompletarRecargaBody,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    vendedor = _get_vendedor(db, usuario)
 
    recarga = db.query(RecargaStock).filter(
        RecargaStock.id          == body.recarga_id,
        RecargaStock.vendedor_id == vendedor.id,
        RecargaStock.estado      == "aceptada",
    ).first()
 
    if not recarga:
        raise HTTPException(404, "Recarga no encontrada o no está aceptada.")
 
    productos_json = recarga.productos_aprobados or recarga.productos_solicitados
 
    if productos_json:
        try:
            productos = json.loads(productos_json)
            hoy = date.today()
            for prod in productos:
                pid = prod.get("producto_id")
                qty = int(prod.get("cantidad", 0))
                if not pid or qty <= 0:
                    continue
 
                stock_item = db.query(StockDiario).filter(
                    StockDiario.vendedor_id == vendedor.id,
                    StockDiario.producto_id == pid,
                    StockDiario.fecha       == hoy,
                ).first()
 
                if stock_item:
                    stock_item.cantidad += qty
                else:
                    db.add(StockDiario(
                        vendedor_id = vendedor.id,
                        producto_id = pid,
                        fecha       = hoy,
                        cantidad    = qty,
                    ))
 
        except Exception as e:
            db.rollback()
            logger.error(f"[completar-recarga] Error: {e}", exc_info=True)
            raise HTTPException(500, f"Error al actualizar stock: {str(e)}")
 
    # Un solo commit que guarda stock + estado juntos
    recarga.estado        = "completada"
    recarga.completado_en = datetime.now(timezone.utc)
    db.commit()
 
    logger.info(f"[completar-recarga] Recarga {recarga.id} completada OK")
 
    return {
        "recarga_id": str(recarga.id),
        "estado":     "completada",
        "mensaje":    "Stock actualizado exitosamente.",
    }
 
# ══════════════════════════════════════════════════════════
#  GET /ruta-activa/recarga-activa/{sesion_id}
# ══════════════════════════════════════════════════════════
@router.get("/recarga-activa/{sesion_id}")
def recarga_activa(
    sesion_id: str,
    db:        Session = Depends(get_db),
    usuario:   Usuario = Depends(requiere_vendedor),
):
    """
    Obtiene la recarga activa (pendiente o aceptada).
    """
    recarga = db.query(RecargaStock).filter(
        RecargaStock.sesion_id == sesion_id,
        RecargaStock.estado.in_(["pendiente", "aceptada"]),
    ).order_by(RecargaStock.solicitado_en.desc()).first()
 
    total = db.query(RecargaStock).filter(
        RecargaStock.sesion_id == sesion_id,
    ).count()
 
    if not recarga:
        return {
            "tiene_recarga_activa": False,
            "recargas_usadas":      total,
            "recargas_max":         MAX_RECARGAS_POR_SESION,
        }
 
    # Parsear productos aprobados si existen
    productos_aprobados = []
    if recarga.productos_aprobados:
        try:
            productos_aprobados = json.loads(recarga.productos_aprobados)
        except:
            pass
 
    return {
        "tiene_recarga_activa": True,
        "recarga_id":           str(recarga.id),
        "estado":               recarga.estado,
        "productos_aprobados":  productos_aprobados,
        "lat_recarga":          float(recarga.lat_recarga)
                                if recarga.lat_recarga else None,
        "lng_recarga":          float(recarga.lng_recarga)
                                if recarga.lng_recarga else None,
        "direccion_recarga":    recarga.direccion_recarga,
        "notas_admin":          recarga.notas_admin,
        "solicitado_en":        recarga.solicitado_en.isoformat(),
        "recargas_usadas":      total,
        "recargas_max":         MAX_RECARGAS_POR_SESION,
    }
 
 
# ══════════════════════════════════════════════════════════
#  GET /ruta-activa/solicitudes-recarga  (admin)
# ══════════════════════════════════════════════════════════
@router.get("/solicitudes-recarga")
def solicitudes_recarga_pendientes(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin),
):
    """
    Admin obtiene todas las solicitudes pendientes.
    """
    recargas = db.query(RecargaStock).filter(
        RecargaStock.estado == "pendiente",
    ).order_by(RecargaStock.solicitado_en.desc()).all()
 
    result = []
    for r in recargas:
        # Parsear productos solicitados
        productos = []
        if r.productos_solicitados:
            try:
                productos = json.loads(r.productos_solicitados)
            except:
                pass
 
        result.append({
            "recarga_id":      str(r.id),
            "sesion_id":       str(r.sesion_id),
            "vendedor_nombre": r.vendedor.nombre_completo
                               if r.vendedor else "",
            "vendedor_id":     str(r.vendedor_id),
            "productos":       productos,
            "estado":          r.estado,
            "notas":           r.notas_admin,
            "solicitado_en":   r.solicitado_en.isoformat(),
        })
 
    return result