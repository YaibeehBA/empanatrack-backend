from datetime import datetime, timedelta, timezone
from passlib.context import CryptContext
import jwt
from app.config import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES

# Contexto de hashing — usa bcrypt
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hashear_contrasena(contrasena: str) -> str:
    """Convierte una contraseña en texto plano a un hash bcrypt."""
    return pwd_context.hash(contrasena)


def verificar_contrasena(contrasena_plana: str, contrasena_hash: str) -> bool:
    """Verifica que una contraseña coincide con su hash."""
    return pwd_context.verify(contrasena_plana, contrasena_hash)


def crear_token(data: dict) -> str:
    """Genera un token JWT con los datos del usuario y una expiración."""
    print(f"DEBUG - ACCESS_TOKEN_EXPIRE_MINUTES vale: {ACCESS_TOKEN_EXPIRE_MINUTES}")
    print(f"DEBUG - Tipo: {type(ACCESS_TOKEN_EXPIRE_MINUTES)}")
    
    payload = data.copy()
    expira  = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    print(f"DEBUG - Fecha expira: {expira}")
    print(f"DEBUG - Timestamp: {int(expira.timestamp())}")
    
    payload.update({"exp": int(expira.timestamp())})
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    print(f"DEBUG - Token generado: {token[:50]}...")
    return token

def decodificar_token(token: str) -> dict:
    """Decodifica un token JWT. Lanza excepción si es inválido o expiró."""
    print(f"🔍 Intentando decodificar token: {token[:50]}...")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        print(f"✅ Token decodificado: {payload}")
        print(f"📅 Expira: {datetime.fromtimestamp(payload['exp'])}")
        return payload
    except jwt.ExpiredSignatureError:
        print("❌ Token EXPIRADO")
        raise
    except jwt.InvalidTokenError as e:
        print(f"❌ Token INVÁLIDO: {e}")
        raise

