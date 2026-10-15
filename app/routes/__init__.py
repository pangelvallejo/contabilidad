"""Utilidades comunes de las vistas."""
from datetime import date

from flask import Response, request

from ..contab import d, redondear


def fecha_arg(nombre, defecto=None):
    valor = request.args.get(nombre) or request.form.get(nombre)
    if not valor:
        return defecto
    try:
        f = date.fromisoformat(valor)
    except ValueError:
        return defecto
    return f if 2000 <= f.year <= 2100 else defecto


def periodo():
    """Rango desde/hasta de la consulta; por defecto el año en curso completo."""
    hoy = date.today()
    desde = fecha_arg("desde", date(hoy.year, 1, 1))
    hasta = fecha_arg("hasta", date(hoy.year, 12, 31))
    return desde, hasta


def anio_arg():
    try:
        anio = int(request.args.get("anio") or date.today().year)
    except ValueError:
        return date.today().year
    return anio if 2000 <= anio <= 2100 else date.today().year


def dinero(nombre):
    texto = (request.form.get(nombre) or "0").strip()
    try:
        return redondear(d(texto))  # lo que se guarda son pesos con dos decimales: se valida ya redondeado
    except Exception as e:  # noqa: BLE001 — Decimal lanza InvalidOperation; d() ValueError (NaN, tope)
        if "demasiado grande" in str(e):
            raise ValueError(str(e)) from e
        raise ValueError(f"El valor '{texto}' no es un número válido (use 1.234.567,89).") from e


def entero_requerido(nombre, mensaje):
    """Entero obligatorio del formulario; si falta o no es numérico, un mensaje entendible en vez del error técnico."""
    valor = request.form.get(nombre, "").strip()
    if not valor.lstrip("-").isdecimal():
        raise ValueError(mensaje)
    return int(valor)


def explicar(e: Exception) -> str:
    """Texto para el usuario de un error inesperado: traduce los de la base de datos y recorta lo técnico."""
    texto = str(e)
    if "FOREIGN KEY" in texto:
        return "Hace referencia a un registro que ya no existe (cliente, categoría, cuenta o banco)."
    if "UNIQUE" in texto:
        campo = texto.rsplit(".", 1)[-1].split()[0] if "." in texto else ""
        return f"Ya existe un registro con ese mismo dato{f' ({campo})' if campo else ''}."
    if "sqlite3." in texto or "[SQL:" in texto:
        return "La base de datos rechazó la operación: " + texto.split("[SQL:")[0].split(")", 1)[-1].strip()[:200]
    return texto


def check(nombre):
    return request.form.get(nombre) in ("1", "on", "true", "si")


def descargar(contenido: bytes, nombre: str, tipo: str):
    return Response(contenido, mimetype=tipo,
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def destino_seguro(valor, defecto):
    """Solo permite redirigir a rutas internas de la aplicación."""
    return valor if valor and valor.startswith("/") and not valor.startswith("//") else defecto


POR_PAGINA = 100


def paginar(consulta, por_pagina=POR_PAGINA):
    """Devuelve (elementos, pagina, total_paginas) según ?pagina=."""
    try:
        pagina = max(1, int(request.args.get("pagina") or 1))
    except ValueError:
        pagina = 1
    total = consulta.order_by(None).count()
    paginas = max(1, -(-total // por_pagina))
    pagina = min(pagina, paginas)
    return consulta.offset((pagina - 1) * por_pagina).limit(por_pagina).all(), pagina, paginas


def paginar_lista(elementos, por_pagina=POR_PAGINA):
    """Como `paginar`, pero sobre una lista ya calculada (cuando los totales necesitan todos los elementos)."""
    try:
        pagina = max(1, int(request.args.get("pagina") or 1))
    except ValueError:
        pagina = 1
    paginas = max(1, -(-len(elementos) // por_pagina))
    pagina = min(pagina, paginas)
    return elementos[(pagina - 1) * por_pagina: pagina * por_pagina], pagina, paginas
