#!/usr/bin/env python3
"""
simular_gps_vendedor.py
───────────────────────
Simula el movimiento GPS del vendedor en el emulador Android
usando la misma lógica de ruta_utils.py (OSRM + nearest-neighbor).

"""

import subprocess
import time
import math
import sys
import asyncio
import requests
from dotenv import load_dotenv

load_dotenv()

from app.utils.ruta_utils import calcular_orden_y_polilinea

# ══════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════
BASE_URL       = "https://empatrack.up.railway.app"
NOMBRE_USUARIO = "vendedor"   # ← nombre_usuario del vendedor de prueba
CONTRASENA     = "admin1234"     # ← contraseña

# Posición de partida (lejos del punto de inicio ~300-500m)
LAT_ORIGEN = -1.665867
LNG_ORIGEN = -78.657708

DELAY_PASOS = 0.25   # segundos entre cada punto GPS inyectado
DELAY_FINAL = 3.0   # segundos quieto al llegar
# ══════════════════════════════════════════════════════════


def haversine(lat1, lng1, lat2, lng2) -> float:
    R  = 6_371_000
    p1 = math.radians(lat1); p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a  = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ─────────────────────────────────────────────────────────
#  DENSIFICACIÓN — puntos intermedios entre esquinas OSRM
# ─────────────────────────────────────────────────────────
METROS_POR_PASO = 3  # un punto GPS cada 3m → movimiento fluido

def densificar(puntos: list) -> list:
    """
    OSRM devuelve solo los vértices donde hay curvas o esquinas.
    Entre dos esquinas puede haber 50m sin ningún punto intermedio,
    haciendo que el marcador salte en el mapa.
    Esta función inserta puntos cada METROS_POR_PASO metros entre
    cada par de puntos consecutivos, respetando la geometría OSRM.
    """
    if len(puntos) < 2:
        return puntos
    resultado = [puntos[0]]
    for i in range(len(puntos) - 1):
        lat1, lng1 = puntos[i]
        lat2, lng2 = puntos[i + 1]
        dist = haversine(lat1, lng1, lat2, lng2)
        n = max(1, int(dist / METROS_POR_PASO))
        for j in range(1, n + 1):
            t = j / n
            resultado.append((lat1 + (lat2 - lat1) * t, lng1 + (lng2 - lng1) * t))
    return resultado


# ─────────────────────────────────────────────────────────
#  BACKEND
# ─────────────────────────────────────────────────────────
def login() -> str:
    print(f"\n🔐 Login como '{NOMBRE_USUARIO}'...")
    try:
        r = requests.post(
            f"{BASE_URL}/auth/login",
            json={"nombre_usuario": NOMBRE_USUARIO, "contrasena": CONTRASENA},
            timeout=15,
        )
    except requests.exceptions.ConnectionError:
        print(f"❌ No se puede conectar a {BASE_URL}")
        sys.exit(1)

    if r.status_code != 200:
        print(f"❌ Error {r.status_code}: {r.text}")
        sys.exit(1)

    token = r.json().get("access_token")
    if not token:
        print(f"❌ No vino access_token: {r.json()}")
        sys.exit(1)

    print("✅ Login OK")
    return token


def obtener_empresas_pendientes(token: str) -> list[dict]:
    print("\n📡 Consultando /ruta-activa/estado-hoy...")
    r = requests.get(
        f"{BASE_URL}/ruta-activa/estado-hoy",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    if r.status_code != 200:
        print(f"❌ Error {r.status_code}: {r.text}")
        sys.exit(1)

    data = r.json()

    if not data.get("tiene_ruta"):
        print("❌ El vendedor no tiene ruta asignada hoy.")
        sys.exit(1)

    if not data.get("stock_lleno"):
        print("⚠️  Falta cargar el stock en la app antes de simular.")
        sys.exit(1)

    pendientes = [
        e for e in data.get("empresas", [])
        if not e.get("visitada")
        and e.get("latitud")  is not None
        and e.get("longitud") is not None
    ]

    if not pendientes:
        print("❌ No hay empresas pendientes con coordenadas GPS.")
        sys.exit(1)

    print(f"✅ {len(pendientes)} empresa(s) pendiente(s)")
    return pendientes


# ─────────────────────────────────────────────────────────
#  RUTA — delega todo a ruta_utils.py
# ─────────────────────────────────────────────────────────
async def calcular_ruta(
    lat_origen: float,
    lng_origen: float,
    empresas:   list[dict],
) -> tuple[list[tuple[float, float]], str]:
    """
    Usa calcular_orden_y_polilinea() de ruta_utils para:
      - calcular el orden óptimo (OSRM Table + nearest-neighbor)
      - trazar la ruta por calles reales (OSRM Route)

    Solo usamos el primer segmento: origen → punto de inicio óptimo.
    El resto de segmentos (entre empresas) no se necesitan para la sim.
    """
    # coords en formato (lat, lng) que espera ruta_utils
    # Ponemos el origen como primer punto para que calcule
    # la ruta desde ahí hasta la primera empresa óptima
    coords_empresas = [
        (float(e["latitud"]), float(e["longitud"]))
        for e in empresas
    ]

    print("\n📐 Calculando orden óptimo (OSRM Table + nearest-neighbor)...")
    resultado = await calcular_orden_y_polilinea(coords_empresas)

    # La primera empresa en el orden óptimo es el punto de inicio
    idx_inicio   = resultado.orden[0]
    emp_inicio   = empresas[idx_inicio]
    nombre       = emp_inicio["nombre"]
    lat_fin      = float(emp_inicio["latitud"])
    lng_fin      = float(emp_inicio["longitud"])

    print(f"   ✅ Fuente orden: {resultado.fuente_orden}")
    print(f"   🏁 Punto de inicio óptimo: '{nombre}'")
    print(f"      Orden de visita:")
    for i, idx in enumerate(resultado.orden):
        print(f"      {i+1}. {empresas[idx]['nombre']}")

    # Ahora calcular la ruta por calles desde el origen del vendedor
    # hasta el punto de inicio — un solo segmento adicional
    print(f"\n🗺️  Trazando ruta por calles: origen → '{nombre}'...")
    resultado_tramo = await calcular_orden_y_polilinea([
        (lat_origen, lng_origen),
        (lat_fin,    lng_fin),
    ])

    # El primer (y único) segmento tiene los puntos de la ruta
    # El primer (y único) segmento tiene los puntos de la ruta
    segmento = resultado_tramo.segmentos[0]
    puntos_osrm = segmento.puntos   # vértices de esquinas OSRM
    puntos      = densificar(puntos_osrm)  # puntos cada 8m entre esquinas
    print(f"   ✅ Fuente tramo : {segmento.fuente}")
    print(f"   📍 Puntos OSRM  : {len(puntos_osrm)} (vértices)")
    print(f"   📍 Tras densif. : {len(puntos)} (cada ~{METROS_POR_PASO}m)")

    return puntos, nombre, lat_fin, lng_fin


# ─────────────────────────────────────────────────────────
#  ADB
# ─────────────────────────────────────────────────────────
def verificar_adb() -> bool:
    try:
        r = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=5
        )
        emuladores = [
            l for l in r.stdout.splitlines()
            if "emulator" in l and "device" in l
        ]
        if not emuladores:
            print("❌ No se encontró emulador Android corriendo.")
            print("   Inicia el emulador desde Android Studio.")
            return False
        print(f"✅ Emulador: {emuladores[0].split()[0]}")
        return True
    except FileNotFoundError:
        print("❌ 'adb' no encontrado en PATH.")
        return False


def adb_geo_fix(lat: float, lng: float) -> bool:
    """geo fix recibe: longitud primero, luego latitud."""
    r = subprocess.run(
        ["adb", "emu", "geo", "fix", str(lng), str(lat)],
        capture_output=True, text=True,
    )
    return r.returncode == 0


# ─────────────────────────────────────────────────────────
#  SIMULACIÓN
# ─────────────────────────────────────────────────────────
def simular_movimiento(
    puntos:         list[tuple[float, float]],
    nombre_destino: str,
    lat_destino:    float,
    lng_destino:    float,
):
    total = len(puntos)
    print(f"\n🚶 Moviendo vendedor hacia '{nombre_destino}'")
    print(f"   {total} puntos GPS — {DELAY_PASOS}s por punto\n")

    # Establecer posición inicial antes de moverse
    adb_geo_fix(puntos[0][0], puntos[0][1])
    time.sleep(1.5)

    for i, (lat, lng) in enumerate(puntos):
        restante = haversine(lat, lng, lat_destino, lng_destino)
        ok  = adb_geo_fix(lat, lng)
        ico = "✅" if ok else "❌"
        pct = i / max(total - 1, 1)
        bar = "█" * int(pct * 25) + "░" * (25 - int(pct * 25))
        print(
            f"\r  {ico} [{bar}] {i+1:3d}/{total}  {restante:7.1f} m restantes",
            end="", flush=True,
        )
        if i < total - 1:
            time.sleep(DELAY_PASOS)

    print()

    # Fijar exactamente en el destino varias veces para asegurar detección
    print(f"\n📍 Fijando posición exacta en '{nombre_destino}'...")
    for _ in range(5):
        adb_geo_fix(lat_destino, lng_destino)
        time.sleep(0.4)

    print(f"\n⏳ Esperando {DELAY_FINAL}s para que Flutter detecte la posición...")
    time.sleep(DELAY_FINAL)

    print(f"\n🟢 Vendedor en '{nombre_destino}' — pulsa INICIAR RUTA en la app")


# ─────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────
def main():
    print("=" * 54)
    print("  🫓 EmpanaTrack — Simulador GPS Vendedor")
    print(f"  Backend : {BASE_URL}")
    print("=" * 54)

    if not verificar_adb():
        sys.exit(1)

    token    = login()
    empresas = obtener_empresas_pendientes(token)

    lat_ini = LAT_ORIGEN
    lng_ini = LNG_ORIGEN

    # Si el origen está muy cerca de la primera empresa, alejarlo
    if haversine(lat_ini, lng_ini,
                 float(empresas[0]["latitud"]),
                 float(empresas[0]["longitud"])) < 100:
        print("⚠️  Origen muy cercano — ajustando +300m al norte...")
        lat_ini = float(empresas[0]["latitud"]) + 0.003
        lng_ini = float(empresas[0]["longitud"])

    print(f"\n📍 Posición de partida: ({lat_ini:.6f}, {lng_ini:.6f})")

    # Calcular ruta delegando a ruta_utils.py
    puntos, nombre, lat_fin, lng_fin = asyncio.run(
        calcular_ruta(lat_ini, lng_ini, empresas)
    )

    dist = haversine(lat_ini, lng_ini, lat_fin, lng_fin)
    print(f"\n📏 Distancia a recorrer : {dist:.0f} m")
    print(f"   Puntos de ruta OSRM  : {len(puntos)}")

    simular_movimiento(puntos, nombre, lat_fin, lng_fin)

    print("\n" + "=" * 54)
    print("  Simulación completada 🚀")
    print("=" * 54 + "\n")


if __name__ == "__main__":
    main()