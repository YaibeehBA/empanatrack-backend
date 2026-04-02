import uuid
from sqlalchemy import (
    Column, String, Text, Boolean, ForeignKey,
    DECIMAL, Integer, TIMESTAMP
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy import func
from app.database import Base


class Configuracion(Base):
    __tablename__ = "configuracion"

    id             = Column(UUID(as_uuid=True), primary_key=True,
                            default=uuid.uuid4)
    clave          = Column(String(100), nullable=False, unique=True)
    valor          = Column(Text, nullable=False)
    descripcion    = Column(Text, nullable=True)
    actualizado_en = Column(TIMESTAMP(timezone=True),
                            server_default=func.now())


class Pedido(Base):
    __tablename__ = "pedidos"

    id                = Column(UUID(as_uuid=True), primary_key=True,
                               default=uuid.uuid4)
    cliente_id        = Column(UUID(as_uuid=True),
                               ForeignKey("clientes.id"), nullable=False)
    vendedor_id       = Column(UUID(as_uuid=True),
                               ForeignKey("vendedores.id"), nullable=True)
    estado            = Column(String(30),   nullable=False,
                               default="pendiente")
    tipo_pago         = Column(String(20),   nullable=False,
                               default="contraentrega")
    total             = Column(DECIMAL(10,2), nullable=False, default=0)
    direccion_entrega = Column(Text, nullable=True)
    latitud_entrega   = Column(DECIMAL(10,8), nullable=True)
    longitud_entrega  = Column(DECIMAL(11,8), nullable=True)
    notas             = Column(Text, nullable=True)
    costo_envio       = Column(DECIMAL(10,2), nullable=False, default=0)
    comprobante_url   = Column(String(500), nullable=True)
    aceptado_en       = Column(TIMESTAMP(timezone=True), nullable=True)
    creado_en         = Column(TIMESTAMP(timezone=True),
                               server_default=func.now())

    cliente  = relationship("Cliente")
    vendedor = relationship("Vendedor")
    items    = relationship("PedidoItem", back_populates="pedido",
                            cascade="all, delete-orphan")


class PedidoItem(Base):
    __tablename__ = "pedido_items"

    id          = Column(UUID(as_uuid=True), primary_key=True,
                         default=uuid.uuid4)
    pedido_id   = Column(UUID(as_uuid=True),
                         ForeignKey("pedidos.id", ondelete="CASCADE"),
                         nullable=False)
    producto_id = Column(UUID(as_uuid=True),
                         ForeignKey("productos.id"), nullable=False)
    cantidad    = Column(Integer,       nullable=False, default=1)
    precio_unit = Column(DECIMAL(10,2), nullable=False)
    subtotal    = Column(DECIMAL(10,2), nullable=False)

    pedido   = relationship("Pedido",   back_populates="items")
    producto = relationship("Producto")