# app/core/dependencies.py
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials  # Importa esto
from sqlalchemy.orm import Session
from app.database import get_db
from app.core.security import decodificar_token
from app.models.usuario import Usuario
import jwt

security = HTTPBearer()
oauth2_scheme = security 

def get_usuario_actual(
    token_data: HTTPAuthorizationCredentials = Depends(oauth2_scheme),  # ✅ Cambiado
    db: Session = Depends(get_db)
) -> Usuario:
    """
    Dependencia que inyectas en cualquier endpoint que requiera login.
    Lee el token del header Authorization, lo valida y devuelve el usuario.
    """
    credenciales_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No autorizado. Token inválido o expirado.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        # ✅ Extraer el string del objeto
        token_string = token_data.credentials
        print(f"🔍 Token recibido: {token_string[:50]}...")  # Debug
        
        payload = decodificar_token(token_string)  # ✅ Pasar el string
        usuario_id: str = payload.get("sub")
        
        if usuario_id is None:
            raise credenciales_exception
            
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="El token ha expirado.")
    except jwt.InvalidTokenError as e:
        print(f"❌ Token inválido: {e}")
        raise credenciales_exception

    usuario = db.query(Usuario).filter(
        Usuario.id == usuario_id,
        Usuario.esta_activo == True
    ).first()

    if usuario is None:
        raise credenciales_exception

    return usuario


def requiere_admin(usuario: Usuario = Depends(get_usuario_actual)) -> Usuario:
    if usuario.rol != "administrador":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permisos para esta acción."
        )
    return usuario


def requiere_vendedor(usuario: Usuario = Depends(get_usuario_actual)) -> Usuario:
    if usuario.rol not in ("vendedor", "administrador"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo los vendedores pueden realizar esta acción."
        )
    return usuario