# app/services/notificaciones.py
import os
import httpx
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import UUID
from app.models.fcm_token import FcmToken

# Pega aquí tu Server Key de Firebase
FCM_SERVER_KEY = os.getenv("FCM_SERVER_KEY", "TU_SERVER_KEY_AQUI")
FCM_URL        = "https://fcm.googleapis.com/fcm/send"


def enviar_notificacion(
    db:         Session,
    usuario_id: UUID,
    titulo:     str,
    cuerpo:     str,
    datos:      dict = {},
) -> bool:
    """
    Envía una notificación push a un usuario específico.
    Retorna True si fue exitoso.
    """
    # Buscar el token FCM del usuario
    registro = db.query(FcmToken).filter(
        FcmToken.usuario_id == usuario_id
    ).first()

    if not registro:
        # El usuario no tiene token — no tiene la app instalada
        return False

    payload = {
        "to": registro.token,
        "notification": {
            "title": titulo,
            "body":  cuerpo,
            "sound": "default",
        },
        "data": datos,
        "priority": "high",
    }

    headers = {
        "Authorization": f"key={FCM_SERVER_KEY}",
        "Content-Type":  "application/json",
    }

    try:
        response = httpx.post(
            FCM_URL,
            json=    payload,
            headers= headers,
            timeout= 10,
        )
        resultado = response.json()
        return resultado.get("success", 0) == 1
    except Exception as e:
        print(f"Error enviando notificación FCM: {e}")
        return False