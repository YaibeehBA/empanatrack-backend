def validar_cedula_ecuador(cedula: str) -> bool:
    # Debe tener exactamente 10 dígitos
    if not cedula.isdigit() or len(cedula) != 10:
        return False

    # Provincia: entre 01 y 24
    provincia = int(cedula[:2])
    if provincia < 1 or provincia > 24:
        return False

    # Tercer dígito debe ser menor a 6
    if int(cedula[2]) >= 6:
        return False

    # Módulo 10
    coeficientes = [2, 1, 2, 1, 2, 1, 2, 1, 2]
    total = 0
    for i, coef in enumerate(coeficientes):
        valor = int(cedula[i]) * coef
        if valor >= 10:
            valor -= 9
        total += valor

    residuo = total % 10
    digito_verificador = 0 if residuo == 0 else 10 - residuo

    return digito_verificador == int(cedula[9])

def validar_telefono_ecuador(telefono: str) -> str:
    if not telefono.isdigit() or len(telefono) != 10:
        raise ValueError('El teléfono debe tener exactamente 10 dígitos numéricos')
    return telefono