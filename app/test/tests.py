from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

hash_bd = "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TiGniCY0V7qP5.4GhBMnS9bJJxKG"
hash_nuevo = pwd_context.hash("Admin1234!")

print(f"Hash en BD: {hash_bd}")
print(f"Hash nuevo de 'Admin1234!': {hash_nuevo}")
print(f"¿Son iguales? {hash_bd == hash_nuevo}")
print(f"¿Verifica? {pwd_context.verify('Admin1234!', hash_bd)}")