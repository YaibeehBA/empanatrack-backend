from fastapi          import APIRouter, Depends, HTTPException
from sqlalchemy.orm   import Session
from pydantic         import BaseModel
from app.database     import get_db
from app.models.usuario   import Usuario
from app.models.fcm_token import FcmToken
from app.core.dependencies import get_usuario_actual

router = APIRouter(
    prefix="/notificaciones", tags=["Notificaciones"]
)


class TokenRegistrar(BaseModel):
    token:      str
    plataforma: str = "android"


@router.post("/token")
def registrar_token(
    datos:   TokenRegistrar,
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual)
):
    """Guarda o actualiza el token FCM del usuario autenticado."""
    existente = db.query(FcmToken).filter(
        FcmToken.usuario_id == usuario.id
    ).first()

    if existente:
        existente.token      = datos.token
        existente.plataforma = datos.plataforma
    else:
        nuevo = FcmToken(
            usuario_id = usuario.id,
            token      = datos.token,
            plataforma = datos.plataforma,
        )
        db.add(nuevo)

    db.commit()
    return {"mensaje": "Token registrado correctamente."}


@router.delete("/token")
def eliminar_token(
    db:      Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_actual)
):
    """Elimina el token al hacer logout."""
    db.query(FcmToken).filter(
        FcmToken.usuario_id == usuario.id
    ).delete()
    db.commit()
    return {"mensaje": "Token eliminado."}