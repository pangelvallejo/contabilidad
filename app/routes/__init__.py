"""Utilidades comunes de las vistas."""
from datetime import date

from flask import Response, request

from ..contab import d


def fecha_arg(nombre, defecto=None):
    valor = request.args.get(nombre) or request.form.get(nombre)
    if not valor:
        return defecto
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return defecto


def periodo():
    """Rango desde/hasta de la consulta; por defecto el año en curso hasta hoy."""
    hoy = date.today()
    desde = fecha_arg("desde", date(hoy.year, 1, 1))
    hasta = fecha_arg("hasta", date(hoy.year, 12, 31))
    return desde, hasta


def anio_arg():
    try:
        return int(request.args.get("anio") or date.today().year)
    except ValueError:
        return date.today().year


def dinero(nombre):
    return d(request.form.get(nombre) or "0")


def check(nombre):
    return request.form.get(nombre) in ("1", "on", "true", "si")


def descargar(contenido: bytes, nombre: str, tipo: str):
    return Response(contenido, mimetype=tipo,
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def destino_seguro(valor, defecto):
    """Solo permite redirigir a rutas internas de la aplicación."""
    return valor if valor and valor.startswith("/") and not valor.startswith("//") else defecto
