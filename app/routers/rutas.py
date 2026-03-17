import uuid
import httpx
from typing          import List as TypingList
from typing          import List, Optional
from fastapi         import APIRouter, Depends, HTTPException
from pydantic        import BaseModel
from sqlalchemy.orm  import Session

from app.database          import get_db
from app.models.ruta       import Ruta, RutaEmpresa, RutaAsignacion
from app.models.empresa    import Empresa
from app.models.vendedor   import Vendedor
from app.models.usuario    import Usuario
from app.core.dependencies import requiere_admin, requiere_vendedor

router = APIRouter(prefix="/rutas", tags=["Rutas"])


# ══════════════════════════════════════════════════════════
#  SCHEMAS
# ══════════════════════════════════════════════════════════
class RutaCrear(BaseModel):
    nombre:      str
    descripcion: Optional[str] = None

class RutaEditar(BaseModel):
    nombre:      Optional[str]  = None
    descripcion: Optional[str]  = None
    esta_activa: Optional[bool] = None

class AsignacionCrear(BaseModel):
    vendedor_id: str
    turno:       str = "unica"   # mañana / tarde / unica

class EmpresasRuta(BaseModel):
    empresa_ids: List[str]

class CoordenadaEmpresa(BaseModel):
    latitud:  float
    longitud: float


# ══════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════
def _empresa_dict(e: Empresa) -> dict:
    return {
        "id":        str(e.id),
        "nombre":    e.nombre,
        "direccion": e.direccion,
        "telefono":  e.telefono,
        "latitud":   float(e.latitud)  if e.latitud  else None,
        "longitud":  float(e.longitud) if e.longitud else None,
    }

def _ruta_dict(r: Ruta) -> dict:
    return {
        "id":          str(r.id),
        "nombre":      r.nombre,
        "descripcion": r.descripcion,
        "esta_activa": r.esta_activa,
        "creado_en":   r.creado_en.isoformat() if r.creado_en else None,
        "empresas": [
            _empresa_dict(re.empresa)
            for re in r.empresas
            if re.empresa and re.empresa.esta_activa
        ],
        "asignaciones": [
            {
                "id":           str(a.id),
                "vendedor_id":  str(a.vendedor_id),
                "nombre":       a.vendedor.nombre_completo
                                if a.vendedor else "",
                "turno":        a.turno,
                "esta_activa":  a.esta_activa,
            }
            for a in r.asignaciones
        ],
    }


# ══════════════════════════════════════════════════════════
#  ADMIN — CRUD RUTAS
# ══════════════════════════════════════════════════════════

# Listar todas las rutas
@router.get("/admin")
def listar_rutas(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin),
):
    rutas = db.query(Ruta).order_by(Ruta.nombre).all()
    return [_ruta_dict(r) for r in rutas]


# Detalle de una ruta
@router.get("/admin/{ruta_id}")
def detalle_ruta(
    ruta_id: str,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin),
):
    ruta = db.query(Ruta).filter(Ruta.id == ruta_id).first()
    if not ruta:
        raise HTTPException(status_code=404, detail="Ruta no encontrada.")
    return _ruta_dict(ruta)


# Crear ruta
@router.post("/admin")
def crear_ruta(
    datos:   RutaCrear,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin),
):
    ruta = Ruta(
        nombre      = datos.nombre.strip(),
        descripcion = datos.descripcion,
    )
    db.add(ruta)
    db.commit()
    db.refresh(ruta)
    return _ruta_dict(ruta)


# Editar ruta
@router.put("/admin/{ruta_id}")
def editar_ruta(
    ruta_id: str,
    datos:   RutaEditar,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin),
):
    ruta = db.query(Ruta).filter(Ruta.id == ruta_id).first()
    if not ruta:
        raise HTTPException(status_code=404, detail="Ruta no encontrada.")
    if datos.nombre      is not None: ruta.nombre      = datos.nombre.strip()
    if datos.descripcion is not None: ruta.descripcion = datos.descripcion
    if datos.esta_activa is not None: ruta.esta_activa = datos.esta_activa
    db.commit()
    db.refresh(ruta)
    return _ruta_dict(ruta)


# Eliminar ruta
@router.delete("/admin/{ruta_id}")
def eliminar_ruta(
    ruta_id: str,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin),
):
    ruta = db.query(Ruta).filter(Ruta.id == ruta_id).first()
    if not ruta:
        raise HTTPException(status_code=404, detail="Ruta no encontrada.")
    db.delete(ruta)
    db.commit()
    return {"mensaje": "Ruta eliminada."}


# ══════════════════════════════════════════════════════════
#  ADMIN — EMPRESAS DE UNA RUTA
# ══════════════════════════════════════════════════════════

# Reemplazar empresas de una ruta
@router.put("/admin/{ruta_id}/empresas")
def actualizar_empresas_ruta(
    ruta_id: str,
    datos:   EmpresasRuta,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin),
):
    ruta = db.query(Ruta).filter(Ruta.id == ruta_id).first()
    if not ruta:
        raise HTTPException(status_code=404, detail="Ruta no encontrada.")

    # Eliminar relaciones actuales
    db.query(RutaEmpresa).filter(
        RutaEmpresa.ruta_id == ruta.id
    ).delete()

    # Crear nuevas
    for eid in datos.empresa_ids:
        empresa = db.query(Empresa).filter(Empresa.id == eid).first()
        if empresa:
            db.add(RutaEmpresa(ruta_id=ruta.id, empresa_id=empresa.id))

    db.commit()
    db.refresh(ruta)
    return _ruta_dict(ruta)


# ══════════════════════════════════════════════════════════
#  ADMIN — ASIGNACIONES
# ══════════════════════════════════════════════════════════

# Asignar vendedor a ruta
@router.post("/admin/{ruta_id}/asignaciones")
def asignar_vendedor(
    ruta_id: str,
    datos:   AsignacionCrear,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_admin),
):
    ruta = db.query(Ruta).filter(Ruta.id == ruta_id).first()
    if not ruta:
        raise HTTPException(status_code=404, detail="Ruta no encontrada.")

    vendedor = db.query(Vendedor).filter(
        Vendedor.id == datos.vendedor_id
    ).first()
    if not vendedor:
        raise HTTPException(status_code=404, detail="Vendedor no encontrado.")

    # Verificar si ya existe esa asignación activa con mismo turno
    existe = db.query(RutaAsignacion).filter(
        RutaAsignacion.ruta_id     == ruta.id,
        RutaAsignacion.vendedor_id == vendedor.id,
        RutaAsignacion.turno       == datos.turno,
        RutaAsignacion.esta_activa == True,
    ).first()
    if existe:
        raise HTTPException(
            status_code=400,
            detail=f"Este vendedor ya tiene asignado el turno "
                   f"'{datos.turno}' en esta ruta.",
        )

    asignacion = RutaAsignacion(
        ruta_id     = ruta.id,
        vendedor_id = vendedor.id,
        turno       = datos.turno,
    )
    db.add(asignacion)
    db.commit()
    return {"mensaje": "Vendedor asignado correctamente."}


# Eliminar asignación
@router.delete("/admin/asignaciones/{asignacion_id}")
def eliminar_asignacion(
    asignacion_id: str,
    db:            Session = Depends(get_db),
    usuario:       Usuario = Depends(requiere_admin),
):
    asig = db.query(RutaAsignacion).filter(
        RutaAsignacion.id == asignacion_id
    ).first()
    if not asig:
        raise HTTPException(
            status_code=404, detail="Asignación no encontrada.")
    db.delete(asig)
    db.commit()
    return {"mensaje": "Asignación eliminada."}


# ══════════════════════════════════════════════════════════
#  ADMIN — COORDENADAS DE EMPRESA
# ══════════════════════════════════════════════════════════
@router.put("/admin/empresas/{empresa_id}/coordenadas")
def actualizar_coordenadas(
    empresa_id: str,
    datos:      CoordenadaEmpresa,
    db:         Session = Depends(get_db),
    usuario:    Usuario = Depends(requiere_admin),
):
    empresa = db.query(Empresa).filter(Empresa.id == empresa_id).first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa no encontrada.")
    empresa.latitud  = datos.latitud
    empresa.longitud = datos.longitud
    db.commit()
    return {
        "mensaje":  "Coordenadas actualizadas.",
        "latitud":  datos.latitud,
        "longitud": datos.longitud,
    }


# ══════════════════════════════════════════════════════════
#  VENDEDOR — ver sus rutas asignadas
# ══════════════════════════════════════════════════════════
@router.get("/mis-rutas")
def mis_rutas(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),
):
    from app.models.vendedor import Vendedor as VendedorModel
    vendedor = db.query(VendedorModel).filter(
        VendedorModel.usuario_id == usuario.id
    ).first()
    if not vendedor:
        return []

    asignaciones = db.query(RutaAsignacion).filter(
        RutaAsignacion.vendedor_id == vendedor.id,
        RutaAsignacion.esta_activa == True,
    ).all()

    return [
        {
            "asignacion_id": str(a.id),
            "turno":         a.turno,
            "ruta":          _ruta_dict(a.ruta),
        }
        for a in asignaciones
        if a.ruta and a.ruta.esta_activa
    ]

# ══════════════════════════════════════════════════════════
#trazado de mapa para rutas

# ══════════════════════════════════════════════════════════
#  ENDPOINT: calcular ruta optimizada de una ruta
# ══════════════════════════════════════════════════════════

class PuntoRuta(BaseModel):
    latitud:  float
    longitud: float

class ParadaRuta(BaseModel):
    empresa_id:              str
    nombre:                  str
    direccion:               Optional[str]
    latitud:                 float
    longitud:                float
    distancia_desde_anterior: float   # metros
    es_inicio:               bool
    es_fin:                  bool

class RutaCalculada(BaseModel):
    paradas:         list[ParadaRuta]
    puntos_polilinea: list[PuntoRuta]
    distancia_total: float   # metros
    tiempo_minutos:  float   # minutos caminando ~5km/h
    fuente:          str     # "osrm" o "haversine" (fallback)


@router.get("/calcular/{ruta_id}", response_model=RutaCalculada)
async def calcular_ruta(
    ruta_id: str,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(requiere_vendedor),   # vendedor y admin
):
    ruta = db.query(Ruta).filter(Ruta.id == ruta_id).first()
    if not ruta:
        raise HTTPException(status_code=404, detail="Ruta no encontrada.")

    empresas = [
        re.empresa for re in ruta.empresas
        if re.empresa
        and re.empresa.esta_activa
        and re.empresa.latitud is not None
        and re.empresa.longitud is not None
    ]

    if len(empresas) < 2:
        raise HTTPException(
            status_code=400,
            detail="La ruta necesita al menos 2 empresas con coordenadas GPS."
        )

    coords_list = [(float(e.latitud), float(e.longitud)) for e in empresas]

    # ── PASO 1: Matriz de duraciones OSRM ────────────
    orden = list(range(len(empresas)))   # fallback: orden original
    fuente = "haversine"

    try:
        coords_str = ";".join(
            f"{lng},{lat}" for lat, lng in coords_list
        )
        table_url = (
            f"https://routing.openstreetmap.de/routed-foot"
            f"/table/v1/foot/{coords_str}?annotations=duration"
        )
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(table_url)

        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == "Ok":
                matriz = [
                    [float(v) for v in row]
                    for row in data["durations"]
                ]
                orden  = _vecino_mas_cercano(matriz)
                fuente = "osrm"

    except Exception:
        pass   # fallback a haversine

    # Si falló la matriz, ordenar por Haversine
    if fuente == "haversine":
        orden = _vecino_mas_cercano_haversine(coords_list)

    empresas_ord = [empresas[i] for i in orden]

    # ── PASO 2: OSRM Route API ────────────────────────
    puntos_polilinea = []
    distancias_legs  = [0.0] + [0.0] * (len(empresas_ord) - 1)

    try:
        coords_str = ";".join(
            f"{float(e.longitud)},{float(e.latitud)}"
            for e in empresas_ord
        )
        route_url = (
            f"https://routing.openstreetmap.de/routed-foot"
            f"/route/v1/foot/{coords_str}"
            f"?overview=full&geometries=geojson"
            f"&steps=true&continue_straight=false"
        )
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(route_url)

        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == "Ok" and data.get("routes"):
                route = data["routes"][0]
                legs  = route["legs"]

                # Distancias reales por tramo
                distancias_legs = [0.0] + [
                    float(leg["distance"]) for leg in legs
                ]

                # Extraer puntos de los steps (máxima precisión)
                vistos = set()
                for leg in legs:
                    for step in leg.get("steps", []):
                        for c in step["geometry"]["coordinates"]:
                            key = (round(c[1], 7), round(c[0], 7))
                            if key not in vistos:
                                vistos.add(key)
                                puntos_polilinea.append(
                                    PuntoRuta(latitud=c[1], longitud=c[0])
                                )

    except Exception:
        pass   # fallback: líneas rectas

    # Fallback polilínea: líneas rectas entre empresas
    if not puntos_polilinea:
        puntos_polilinea = [
            PuntoRuta(latitud=float(e.latitud), longitud=float(e.longitud))
            for e in empresas_ord
        ]
        # Recalcular distancias por Haversine
        distancias_legs = [0.0]
        for i in range(1, len(empresas_ord)):
            distancias_legs.append(_haversine(
                float(empresas_ord[i-1].latitud),
                float(empresas_ord[i-1].longitud),
                float(empresas_ord[i].latitud),
                float(empresas_ord[i].longitud),
            ))

    distancia_total = sum(distancias_legs)
    tiempo_minutos  = distancia_total / 83.3   # 5 km/h

    # ── Construir respuesta ───────────────────────────
    paradas = []
    for i, empresa in enumerate(empresas_ord):
        paradas.append(ParadaRuta(
            empresa_id=               str(empresa.id),
            nombre=                   empresa.nombre,
            direccion=                empresa.direccion,
            latitud=                  float(empresa.latitud),
            longitud=                 float(empresa.longitud),
            distancia_desde_anterior= distancias_legs[i],
            es_inicio=                i == 0,
            es_fin=                   i == len(empresas_ord) - 1,
        ))

    return RutaCalculada(
        paradas=          paradas,
        puntos_polilinea= puntos_polilinea,
        distancia_total=  distancia_total,
        tiempo_minutos=   tiempo_minutos,
        fuente=           fuente,
    )


# ══════════════════════════════════════════════════════════
#  HELPERS ALGORITMOS
# ══════════════════════════════════════════════════════════

def _vecino_mas_cercano(matriz: list) -> list:
    """Prueba todos los inicios posibles, devuelve el orden
    con menor duración total según la matriz OSRM."""
    n = len(matriz)
    if n <= 2:
        return list(range(n))

    mejor_total = float("inf")
    mejor_orden = list(range(n))

    for inicio in range(n):
        visitados = {inicio}
        orden     = [inicio]
        total     = 0.0

        while len(orden) < n:
            actual   = orden[-1]
            min_dur  = float("inf")
            siguiente = -1

            for j in range(n):
                if j not in visitados:
                    dur = matriz[actual][j]
                    if dur < min_dur:
                        min_dur   = dur
                        siguiente = j

            if siguiente == -1:
                break
            visitados.add(siguiente)
            orden.append(siguiente)
            total += min_dur

        if len(orden) == n and total < mejor_total:
            mejor_total = total
            mejor_orden = orden[:]

    return mejor_orden


def _vecino_mas_cercano_haversine(coords: list) -> list:
    """Fallback: misma lógica pero con distancia en línea recta."""
    n = len(coords)
    if n <= 2:
        return list(range(n))

    mejor_dist  = float("inf")
    mejor_orden = list(range(n))

    for inicio in range(n):
        visitados = {inicio}
        orden     = [inicio]
        total     = 0.0

        while len(orden) < n:
            actual = orden[-1]
            min_d  = float("inf")
            sig    = -1

            for j in range(n):
                if j not in visitados:
                    d = _haversine(
                        coords[actual][0], coords[actual][1],
                        coords[j][0],     coords[j][1],
                    )
                    if d < min_d:
                        min_d = d
                        sig   = j

            if sig == -1:
                break
            visitados.add(sig)
            orden.append(sig)
            total += min_d

        if len(orden) == n and total < mejor_dist:
            mejor_dist  = total
            mejor_orden = orden[:]

    return mejor_orden


import math

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2 +
         math.cos(lat1 * p) * math.cos(lat2 * p) *
         math.sin((lon2 - lon1) * p / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))