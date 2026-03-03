from pydantic import BaseModel

class LoginInput(BaseModel):
    nombre_usuario: str
    contrasena:     str

class TokenOutput(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    rol:          str
    nombre:       str