from sqlalchemy import Column, String, Boolean, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid
from app.database import Base

class Producto(Base):
    __tablename__ = "productos"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre      = Column(String(200), nullable=False)
    precio      = Column(Numeric(10, 2), nullable=False)
    esta_activo = Column(Boolean, default=True, nullable=False)

    # Relaciones
    detalle_ventas = relationship("DetalleVenta", back_populates="producto")