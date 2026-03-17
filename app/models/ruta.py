import uuid
from sqlalchemy import Column, String, Text, Boolean, ForeignKey, DECIMAL
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy import TIMESTAMP, func
from app.database import Base


class Ruta(Base):
    __tablename__ = "rutas"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre      = Column(String(200), nullable=False)
    descripcion = Column(Text, nullable=True)
    esta_activa = Column(Boolean, default=True, nullable=False)
    creado_en   = Column(TIMESTAMP(timezone=True), server_default=func.now())

    empresas    = relationship("RutaEmpresa",    back_populates="ruta",
                               cascade="all, delete-orphan")
    asignaciones = relationship("RutaAsignacion", back_populates="ruta",
                                cascade="all, delete-orphan")


class RutaEmpresa(Base):
    __tablename__ = "ruta_empresas"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ruta_id    = Column(UUID(as_uuid=True), ForeignKey("rutas.id"), nullable=False)
    empresa_id = Column(UUID(as_uuid=True), ForeignKey("empresas.id"), nullable=False)

    ruta    = relationship("Ruta",    back_populates="empresas")
    empresa = relationship("Empresa")


class RutaAsignacion(Base):
    __tablename__ = "ruta_asignaciones"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ruta_id     = Column(UUID(as_uuid=True), ForeignKey("rutas.id"),     nullable=False)
    vendedor_id = Column(UUID(as_uuid=True), ForeignKey("vendedores.id"), nullable=False)
    turno       = Column(String(20), default="unica", nullable=False)
    esta_activa = Column(Boolean, default=True, nullable=False)
    creado_en   = Column(TIMESTAMP(timezone=True), server_default=func.now())

    ruta     = relationship("Ruta",     back_populates="asignaciones")
    vendedor = relationship("Vendedor")