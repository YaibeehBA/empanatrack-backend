# Crea un archivo temporal reset_password.py
from passlib.context import CryptContext
import psycopg2

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Conexión a tu BD
conn = psycopg2.connect(
    host="localhost",
    database="empanatrack",
    user="postgres",
    password="admin"
)
cur = conn.cursor()

# Nueva contraseña
nueva_contrasena = "Admin1234!"
nuevo_hash = pwd_context.hash(nueva_contrasena)

print(f"Nueva contraseña: {nueva_contrasena}")
print(f"Nuevo hash: {nuevo_hash}")

# Actualizar en BD
cur.execute(
    "UPDATE usuarios SET contrasena_hash = %s WHERE nombre_usuario = %s",
    (nuevo_hash, "admin")
)
conn.commit()
print("✅ Contraseña actualizada")

cur.close()
conn.close()