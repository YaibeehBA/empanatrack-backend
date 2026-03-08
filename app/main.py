from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import notificaciones
from app.routers import admin, auth, ventas, clientes, reportes, productos, pagos, vendedores

app = FastAPI(
    title       = "EmpanaTrack API",
    description = "Sistema de gestión de ventas a crédito para vendedores de empanadas.",
    version     = "1.0.0",
)

# CORS: permite que la app Flutter se conecte desde cualquier origen
# En producción reemplaza ["*"] por la URL exacta de tu app
app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# Registrar todos los routers
app.include_router(auth.router)
app.include_router(ventas.router)
app.include_router(clientes.router)
app.include_router(reportes.router)
app.include_router(productos.router)
app.include_router(pagos.router)
app.include_router(admin.router)
app.include_router(notificaciones.router)
app.include_router(vendedores.router)

@app.get("/", tags=["Health"])
def health_check():
    return {"status": "ok", "mensaje": "EmpanaTrack API corriendo 🫓"}