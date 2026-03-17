import uuid
from sqlalchemy import Column, String, Boolean, DECIMAL
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base

class Empresa(Base):
    __tablename__ = "empresas"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre      = Column(String(300), nullable=False)
    direccion   = Column(String(500), nullable=True)
    telefono    = Column(String(20),  nullable=True)
    esta_activa = Column(Boolean, default=True, nullable=False)
    latitud     = Column(DECIMAL(10, 8), nullable=True)   
    longitud    = Column(DECIMAL(11, 8), nullable=True)   

    clientes    = relationship("Cliente", back_populates="empresa")