from sqlalchemy import Column, String, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid
from app.database import Base

class Cliente(Base):
    __tablename__ = "clientes"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    usuario_id  = Column(UUID(as_uuid=True), ForeignKey("usuarios.id"),  nullable=True)
    empresa_id  = Column(UUID(as_uuid=True), ForeignKey("empresas.id"),  nullable=True)
    cedula      = Column(String(20),  nullable=False, unique=True)
    nombre      = Column(String(200), nullable=False)
    correo      = Column(String(200), unique=True, nullable=True)
    telefono    = Column(String(20),  nullable=True)
    esta_activo = Column(Boolean, default=True, nullable=False)

    # Relaciones
    usuario     = relationship("Usuario", back_populates="cliente")
    empresa     = relationship("Empresa", back_populates="clientes")
    ventas      = relationship("Venta",   back_populates="cliente")
    pagos       = relationship("Pago",    back_populates="cliente")