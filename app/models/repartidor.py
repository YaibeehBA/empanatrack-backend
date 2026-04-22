import uuid
from sqlalchemy import Column, String, Boolean, ForeignKey, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy import func
from app.database import Base


class Repartidor(Base):
    __tablename__ = "repartidores"

    id              = Column(UUID(as_uuid=True), primary_key=True,
                             default=uuid.uuid4)
    usuario_id      = Column(UUID(as_uuid=True),
                             ForeignKey("usuarios.id"), nullable=False)
    nombre_completo = Column(String(200), nullable=False)
    telefono        = Column(String(20),  nullable=True)
    esta_activo     = Column(Boolean, default=True, nullable=False)
    creado_en       = Column(TIMESTAMP(timezone=True),
                             server_default=func.now())

    usuario = relationship("Usuario")