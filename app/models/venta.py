from sqlalchemy import Column, String, Boolean, Numeric, ForeignKey, Enum as PgEnum, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from app.database import Base

class Venta(Base):
    __tablename__ = "ventas"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendedor_id     = Column(UUID(as_uuid=True), ForeignKey("vendedores.id"), nullable=False)
    cliente_id      = Column(UUID(as_uuid=True), ForeignKey("clientes.id"),  nullable=True)
    tipo            = Column(PgEnum("contado", "credito",  name="tipo_venta"),   nullable=False)
    monto_total     = Column(Numeric(10, 2), nullable=False)
    monto_pagado    = Column(Numeric(10, 2), nullable=False, default=0)
    monto_pendiente = Column(Numeric(10, 2), nullable=False)
    estado          = Column(PgEnum("pendiente", "parcial", "pagado", name="estado_venta"),
                             nullable=False, default="pendiente")
    notas           = Column(Text, nullable=True)
    fecha_venta     = Column(String, server_default=func.now())

    # Relaciones
    vendedor        = relationship("Vendedor",     back_populates="ventas")
    cliente         = relationship("Cliente",      back_populates="ventas")
    detalle         = relationship("DetalleVenta", back_populates="venta", cascade="all, delete-orphan")
    pagos           = relationship("Pago",         back_populates="venta")


class DetalleVenta(Base):
    __tablename__ = "detalle_ventas"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    venta_id        = Column(UUID(as_uuid=True), ForeignKey("ventas.id"),    nullable=False)
    producto_id     = Column(UUID(as_uuid=True), ForeignKey("productos.id"), nullable=False)
    cantidad        = Column(String, nullable=False)
    precio_unitario = Column(Numeric(10, 2), nullable=False)
    subtotal        = Column(Numeric(10, 2), nullable=False)

    # Relaciones
    venta           = relationship("Venta",    back_populates="detalle")
    producto        = relationship("Producto", back_populates="detalle_ventas")