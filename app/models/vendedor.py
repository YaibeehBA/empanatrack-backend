from sqlalchemy import Column, String, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid
from app.database import Base

class Vendedor(Base):
    __tablename__ = "vendedores"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    usuario_id      = Column(UUID(as_uuid=True), ForeignKey("usuarios.id"),
                             nullable=False, unique=True)
    nombre_completo = Column(String(200), nullable=False)
    telefono        = Column(String(20),  nullable=True)
    esta_activo     = Column(Boolean, default=True, nullable=False)

    # Relaciones
    usuario         = relationship("Usuario",  back_populates="vendedor")
    rutas           = relationship("Ruta", back_populates="vendedor")
    ventas          = relationship("Venta",    back_populates="vendedor")
    pagos           = relationship("Pago",     back_populates="vendedor")