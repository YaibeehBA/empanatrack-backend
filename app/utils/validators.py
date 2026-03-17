def validar_cedula_ecuador(cedula: str) -> str:
    if not cedula.isdigit() or len(cedula) != 10:
        raise ValueError('La cédula debe tener exactamente 10 dígitos')

    provincia = int(cedula[:2])
    if provincia < 1 or provincia > 24:
        raise ValueError('Los dos primeros dígitos no son válidos')

    if int(cedula[2]) >= 6:
        raise ValueError('El tercer dígito no es válido')

    coeficientes = [2, 1, 2, 1, 2, 1, 2, 1, 2]
    total = 0
    for i, coef in enumerate(coeficientes):
        valor = int(cedula[i]) * coef
        if valor >= 10:
            valor -= 9
        total += valor

    residuo = total % 10
    digito_verificador = 0 if residuo == 0 else 10 - residuo

    if digito_verificador != int(cedula[9]):
        raise ValueError('La cédula no es válida')

    return cedula  

def validar_telefono_ecuador(telefono: str) -> str:
    if not telefono.isdigit() or len(telefono) != 10:
        raise ValueError('El teléfono debe tener exactamente 10 dígitos numéricos')
    return telefono

def validar_coordenada_latitud(latitud: float) -> float:
    if not (-90 <= latitud <= 90):
        raise ValueError('Latitud debe estar entre -90 y 90')
    return latitud

def validar_coordenada_longitud(longitud: float) -> float:
    if not (-180 <= longitud <= 180):
        raise ValueError('Longitud debe estar entre -180 y 180')
    return longitud