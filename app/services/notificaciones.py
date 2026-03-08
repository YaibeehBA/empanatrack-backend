# app/services/notificaciones.py
import os
import json
import httpx
import google.auth.transport.requests
import google.oauth2.service_account

from sqlalchemy.orm import Session
from app.models.fcm_token import FcmToken

# ── Configuración ────────────────────────────────────────
# Ruta al archivo de credenciales descargado de Firebase Console
CREDENTIALS_PATH = os.getenv(
    "FIREBASE_CREDENTIALS_PATH",
    "firebase_credentials.json"   # por defecto en la raíz del proyecto
)

# ID del proyecto Firebase (lo encuentras en el .json como "project_id")
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "")

FCM_SCOPES = ["https://www.googleapis.com/auth/firebase.messaging"]


def _get_access_token() -> str:
    """Genera un token OAuth2 temporal para autenticarse con FCM v1."""
    credentials = google.oauth2.service_account.Credentials.from_service_account_file(
        CREDENTIALS_PATH,
        scopes=FCM_SCOPES,
    )
    request = google.auth.transport.requests.Request()
    credentials.refresh(request)
    return credentials.token


def enviar_notificacion(
    db:         Session,
    usuario_id: str,
    titulo:     str,
    cuerpo:     str,
    datos:      dict = {}
) -> bool:

    print(f"\n  🔑 [FCM] usuario_id={usuario_id}")

    # ── Verificar credenciales ───────────────────────────
    if not os.path.exists(CREDENTIALS_PATH):
        print(f"  ❌ [FCM] No se encontro: {CREDENTIALS_PATH}")
        print(f"       Descarga el JSON desde Firebase Console")
        print(f"       → Configuracion del proyecto → Cuentas de servicio")
        return False

    # ── Buscar token FCM del usuario ─────────────────────
    registro = db.query(FcmToken).filter(
        FcmToken.usuario_id == str(usuario_id)
    ).first()

    if not registro:
        print(f"  ❌ [FCM] Sin token para usuario_id={usuario_id}")
        return False

    token = registro.token
    print(f"  ✅ [FCM] Token encontrado: {token[:50]}...")

    # ── Obtener access token OAuth2 ──────────────────────
    try:
        access_token = _get_access_token()
        print(f"  ✅ [FCM] OAuth2 token obtenido OK")
    except Exception as e:
        print(f"  ❌ [FCM] Error obteniendo OAuth2 token: {e}")
        return False

    # ── Obtener project_id si no está en env ─────────────
    project_id = FIREBASE_PROJECT_ID
    if not project_id:
        try:
            with open(CREDENTIALS_PATH) as f:
                creds_data = json.load(f)
                project_id = creds_data.get("project_id", "")
        except Exception:
            pass

    if not project_id:
        print(f"  ❌ [FCM] FIREBASE_PROJECT_ID no configurado")
        return False

    print(f"  ✅ [FCM] Project ID: {project_id}")

    # ── URL FCM v1 ───────────────────────────────────────
    FCM_URL = (
        f"https://fcm.googleapis.com/v1/projects"
        f"/{project_id}/messages:send"
    )

    # ── Payload FCM v1 ───────────────────────────────────
    # Los datos deben ser todos strings en FCM v1
    datos_str = {k: str(v) for k, v in datos.items()}

    payload = {
        "message": {
            "token": token,
            "notification": {
                "title": titulo,
                "body":  cuerpo,
            },
            "android": {
                "priority": "high",
                "notification": {
                    "sound":              "default",
                    "channel_id":         "empanatrack_channel",
                    "notification_priority": "PRIORITY_HIGH",
                    "visibility":         "PUBLIC",
                },
            },
            "data": datos_str,
        }
    }

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json",
    }

    # ── Enviar ───────────────────────────────────────────
    print(f"  📤 [FCM] Enviando a: {FCM_URL}")
    try:
        response = httpx.post(
            FCM_URL,
            json=payload,
            headers=headers,
            timeout=15,
        )

        print(f"  📥 [FCM] HTTP status: {response.status_code}")
        print(f"  📥 [FCM] Raw response: {response.text}")

        if response.status_code == 200:
            print(f"  ✅ [FCM] Notificacion enviada exitosamente")
            return True

        # Analizar errores
        try:
            error_data = response.json()
            error_code = (
                error_data.get("error", {})
                          .get("details", [{}])[0]
                          .get("errorCode", "UNKNOWN")
            )
        except Exception:
            error_code = response.text

        if response.status_code == 404:
            print(f"  ❌ [FCM] 404 — Token inválido o no registrado")
            print(f"       Eliminando token de la BD...")
            db.delete(registro)
            db.commit()
        elif response.status_code == 401:
            print(f"  ❌ [FCM] 401 — OAuth2 token invalido")
            print(f"       Verifica que firebase_credentials.json es correcto")
        elif response.status_code == 400:
            print(f"  ❌ [FCM] 400 — Payload malformado: {error_code}")
        else:
            print(f"  ❌ [FCM] Error {response.status_code}: {error_code}")

        return False

    except httpx.TimeoutException:
        print(f"  ❌ [FCM] TIMEOUT")
        return False
    except Exception as e:
        print(f"  ❌ [FCM] Excepcion: {type(e).__name__}: {e}")
        return False