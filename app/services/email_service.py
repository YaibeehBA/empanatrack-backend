import os
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType

conf = ConnectionConfig(
    MAIL_USERNAME   = os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD   = os.getenv("MAIL_PASSWORD"),
    MAIL_FROM       = os.getenv("MAIL_FROM"),
    MAIL_PORT       = int(os.getenv("MAIL_PORT", 587)),
    MAIL_SERVER     = os.getenv("MAIL_SERVER", "smtp.gmail.com"),
    MAIL_STARTTLS   = True,
    MAIL_SSL_TLS    = False,
    USE_CREDENTIALS = True,
)

async def enviar_codigo_recuperacion(
    correo_destino: str,
    nombre:         str,
    codigo:         str,
) -> None:
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px;
                margin: auto; padding: 32px; border-radius: 12px;
                border: 1px solid #e5e7eb;">
      <h2 style="color: #b45309;">🫓 EmpanaTrack</h2>
      <p>Hola <strong>{nombre}</strong>,</p>
      <p>Recibimos una solicitud para restablecer tu contraseña.</p>
      <p>Tu código de verificación es:</p>
      <div style="font-size: 36px; font-weight: bold; letter-spacing: 10px;
                  text-align: center; color: #b45309; padding: 20px;
                  background: #fef3c7; border-radius: 8px; margin: 20px 0;">
        {codigo}
      </div>
      <p style="color: #6b7280; font-size: 13px;">
        Este código expira en <strong>15 minutos</strong>.<br>
        Si no solicitaste esto, ignora este mensaje.
      </p>
    </div>
    """

    mensaje = MessageSchema(
        subject    = "🔐 Código de recuperación — EmpanaTrack",
        recipients = [correo_destino],
        body       = html,
        subtype    = MessageType.html,
    )

    fm = FastMail(conf)
    await fm.send_message(mensaje)