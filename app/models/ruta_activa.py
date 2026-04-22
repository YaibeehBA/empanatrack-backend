import uuid
from sqlalchemy import (
    Column, String, Integer, Boolean,
    Date, ForeignKey, DECIMAL
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy import TIMESTAMP, func
from app.database import Base


class StockDiario(Base):
    __tablename__ = "stock_diario"

    id          = Column(UUID(as_uuid=True), primary_key=True,
                         default=uuid.uuid4)
    vendedor_id = Column(UUID(as_uuid=True),
                         ForeignKey("vendedores.id"), nullable=False)
    fecha       = Column(Date, nullable=False,
                         server_default=func.current_date())
    producto_id = Column(UUID(as_uuid=True),
                         ForeignKey("productos.id"), nullable=False)
    cantidad    = Column(Integer, nullable=False, default=0)
    creado_en   = Column(TIMESTAMP(timezone=True),
                         server_default=func.now())

    vendedor = relationship("Vendedor")
    producto = relationship("Producto")


class SesionRuta(Base):
    __tablename__ = "sesiones_ruta"

    id            = Column(UUID(as_uuid=True), primary_key=True,
                           default=uuid.uuid4)
    asignacion_id = Column(UUID(as_uuid=True),
                           ForeignKey("ruta_asignaciones.id"),
                           nullable=False)
    vendedor_id   = Column(UUID(as_uuid=True),
                           ForeignKey("vendedores.id"), nullable=False)
    fecha         = Column(Date, nullable=False,
                           server_default=func.current_date())
    estado        = Column(String(20), nullable=False,
                           default="iniciada")
    iniciada_en   = Column(TIMESTAMP(timezone=True),
                           server_default=func.now())
    completada_en = Column(TIMESTAMP(timezone=True), nullable=True)
    lat_inicio    = Column(DECIMAL(10, 8), nullable=True)
    lng_inicio    = Column(DECIMAL(11, 8), nullable=True)

    vendedor  = relationship("Vendedor")
    visitas   = relationship("VisitaVerificada",
                             back_populates="sesion")


class VisitaVerificada(Base):
    __tablename__ = "visitas_verificadas"

    id               = Column(UUID(as_uuid=True), primary_key=True,
                              default=uuid.uuid4)
    sesion_id        = Column(UUID(as_uuid=True),
                              ForeignKey("sesiones_ruta.id"),
                              nullable=False)
    empresa_id       = Column(UUID(as_uuid=True),
                              ForeignKey("empresas.id"), nullable=False)
    vendedor_id      = Column(UUID(as_uuid=True),
                              ForeignKey("vendedores.id"), nullable=False)
    fecha            = Column(Date, nullable=False,
                              server_default=func.current_date())
    llegada_en       = Column(TIMESTAMP(timezone=True), nullable=False)
    marcada_en       = Column(TIMESTAMP(timezone=True), nullable=True)
    minutos_estadia  = Column(Integer, nullable=True)
    lat_verificada   = Column(DECIMAL(10, 8), nullable=True)
    lng_verificada   = Column(DECIMAL(11, 8), nullable=True)
    distancia_metros = Column(Integer, nullable=True)
    es_valida        = Column(Boolean, default=False)

    sesion  = relationship("SesionRuta", back_populates="visitas")
    empresa = relationship("Empresa")
    
