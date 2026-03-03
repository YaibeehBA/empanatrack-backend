# app/database.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import DATABASE_URL

# El engine es la conexión real a PostgreSQL
engine = create_engine(DATABASE_URL)

# SessionLocal es la fábrica de sesiones — cada request obtiene una sesión propia
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base es la clase de la que heredan todos tus modelos SQLAlchemy
class Base(DeclarativeBase):
    pass

# Dependencia de FastAPI: abre una sesión, la entrega al endpoint, y la cierra al terminar
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()