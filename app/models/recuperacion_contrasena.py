from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
import uuid
from app.database import Base

class RecuperacionContrasena(Base):
    __tablename__ = "recuperacion_contrasenas"

    id         = Column(UUID(as_uuid=True), primary_key=True,
                        default=uuid.uuid4)
    usuario_id = Column(UUID(as_uuid=True),
                        ForeignKey("usuarios.id"), nullable=False)
    codigo     = Column(String(6), nullable=False)
    expira_en  = Column(DateTime(timezone=True), nullable=False)
    usado      = Column(Boolean, default=False, nullable=False)
    creado_en  = Column(DateTime(timezone=True),
                        default=lambda: datetime.now(timezone.utc))

    usuario = relationship("Usuario")