# app/models/pago.py
from sqlalchemy import Column, Numeric, ForeignKey, String, Enum as PgEnum, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from app.database import Base

class Pago(Base):
    __tablename__ = "pagos"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    venta_id    = Column(UUID(as_uuid=True), ForeignKey("ventas.id"),     nullable=True)
    cliente_id  = Column(UUID(as_uuid=True), ForeignKey("clientes.id"),   nullable=False)
    vendedor_id = Column(UUID(as_uuid=True), ForeignKey("vendedores.id"), nullable=False)
    monto       = Column(Numeric(10, 2), nullable=False)
    tipo        = Column(PgEnum("efectivo", "transferencia", "adelanto", name="tipo_pago"),
                         nullable=False, default="efectivo")
    notas       = Column(Text, nullable=True)
    fecha_pago  = Column(String, server_default=func.now())

    # Relaciones - CORREGIDAS
    venta       = relationship("Venta", back_populates="pagos", foreign_keys=[venta_id])  # Cambiado a "Venta"
    cliente     = relationship("Cliente", back_populates="pagos", foreign_keys=[cliente_id])
    vendedor    = relationship("Vendedor", back_populates="pagos", foreign_keys=[vendedor_id])