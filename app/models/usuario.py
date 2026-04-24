from sqlalchemy import Column, String, Boolean, Enum as PgEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid
from app.database import Base

class Usuario(Base):
    __tablename__ = "usuarios"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nombre_usuario  = Column(String(100), nullable=False, unique=True)
    correo          = Column(String(200), unique=True, nullable=True)
    contrasena_hash = Column(String(255), nullable=False)
    rol             = Column(PgEnum("administrador", "vendedor", "cliente","repartidor",
                                   name="rol_usuario"), nullable=False)
    esta_activo     = Column(Boolean, default=True, nullable=False)

    # Relaciones
    vendedor        = relationship("Vendedor", back_populates="usuario", uselist=False)
    cliente         = relationship("Cliente",  back_populates="usuario", uselist=False)