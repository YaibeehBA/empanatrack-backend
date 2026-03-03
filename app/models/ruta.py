# app/models/ruta.py
from sqlalchemy import Column, String, Boolean, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid
from app.database import Base

class Ruta(Base):
    __tablename__ = "rutas"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendedor_id = Column(UUID(as_uuid=True), ForeignKey("vendedores.id"), nullable=False)
    nombre      = Column(String(200), nullable=False)
    descripcion = Column(Text, nullable=True)
    esta_activa = Column(Boolean, default=True, nullable=False)

    # Relaciones
 
    vendedor = relationship("Vendedor", back_populates="rutas")