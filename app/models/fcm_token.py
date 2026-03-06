from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
from app.database import Base

class FcmToken(Base):
    __tablename__ = "fcm_tokens"

    id         = Column(UUID(as_uuid=True), primary_key=True,
                        default=uuid.uuid4)
    usuario_id = Column(UUID(as_uuid=True),
                        ForeignKey("usuarios.id"), nullable=False,
                        unique=True)
    token      = Column(String, nullable=False)
    plataforma = Column(String(20), default="android")
    creado_en  = Column(DateTime(timezone=True),
                        server_default=func.now())