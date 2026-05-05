from typing    import Any, Callable, List, Optional, TypeVar
from fastapi   import Query
from pydantic  import BaseModel
from sqlalchemy.orm import Query as SAQuery

T = TypeVar("T")


# ══════════════════════════════════════════════════════════
#  PARÁMETROS DE PAGINACIÓN — se inyectan con Depends()
# ══════════════════════════════════════════════════════════
class PaginaParams:
    def __init__(
        self,
        pagina:     int = Query(default=1,  ge=1,
                                description="Número de página (empieza en 1)"),
        por_pagina: int = Query(default=10, ge=1, le=100,
                                description="Registros por página (máx 100)"),
    ):
        self.pagina     = pagina
        self.por_pagina = por_pagina

    @property
    def offset(self) -> int:
        return (self.pagina - 1) * self.por_pagina


# ══════════════════════════════════════════════════════════
#  RESPUESTA PAGINADA — estructura estándar
# ══════════════════════════════════════════════════════════
class RespuestaPaginada(BaseModel):
    datos:       List[Any]
    pagina:      int
    por_pagina:  int
    total:       int
    tiene_mas:   bool        # ← el frontend lo usa para saber si cargar más

    class Config:
        arbitrary_types_allowed = True


# ══════════════════════════════════════════════════════════
#  HELPER — paginar un SQLAlchemy Query
# ══════════════════════════════════════════════════════════
def paginar_query(
    query:       SAQuery,
    params:      PaginaParams,
    serializar:  Callable[[Any], dict],
) -> dict:
    """
    Recibe un SQLAlchemy Query ya filtrado y ordenado,
    aplica paginación y devuelve el dict estándar.

    Parámetros:
        query      — query SA sin .all() ni .limit()
        params     — PaginaParams inyectado por Depends()
        serializar — función que convierte cada fila a dict

    Retorna dict compatible con RespuestaPaginada.
    """
    total  = query.count()
    filas  = query.offset(params.offset).limit(params.por_pagina).all()
    datos  = [serializar(f) for f in filas]

    return {
        "datos":      datos,
        "pagina":     params.pagina,
        "por_pagina": params.por_pagina,
        "total":      total,
        "tiene_mas":  (params.offset + len(datos)) < total,
    }


# ══════════════════════════════════════════════════════════
#  HELPER — paginar una lista raw (cuando ya tienes los datos
#  en memoria o vienen de una consulta con text())
# ══════════════════════════════════════════════════════════
def paginar_lista(
    lista:      List[Any],
    params:     PaginaParams,
    serializar: Optional[Callable[[Any], dict]] = None,
) -> dict:
    """
    Pagina una lista Python ya cargada.
    Usar solo cuando no es posible paginar en la query SQL
    (p.ej. resultados de text() con mappings).

    Para listas grandes preferir paginar_query().
    """
    total  = len(lista)
    inicio = params.offset
    fin    = inicio + params.por_pagina
    trozo  = lista[inicio:fin]

    datos = [serializar(f) for f in trozo] if serializar else list(trozo)

    return {
        "datos":      datos,
        "pagina":     params.pagina,
        "por_pagina": params.por_pagina,
        "total":      total,
        "tiene_mas":  fin < total,
    }