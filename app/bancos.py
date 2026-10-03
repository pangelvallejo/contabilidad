"""Importación de extractos bancarios (CSV o Excel) y conciliación con los movimientos registrados.

El lector reconoce las columnas por su nombre (fecha, descripción, valor o débito/crédito) sin importar
el banco; si el archivo no trae encabezados reconocibles, el usuario indica las columnas en la pantalla.
"""
import csv
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from openpyxl import load_workbook

from . import contab
from .models import CERO, Asiento, Banco, MovimientoBanco, Movimiento, PagoGasto, PagoImpuesto, Recaudo

NOMBRES = {
    "fecha": ("fecha", "fecha transaccion", "fecha de transaccion", "fecha movimiento", "date", "fecha operacion"),
    "descripcion": ("descripcion", "concepto", "detalle", "transaccion", "description", "referencia 1", "movimiento"),
    "valor": ("valor", "monto", "importe", "amount", "valor transaccion", "vlr"),
    "debito": ("debito", "debitos", "retiro", "retiros", "salida", "salidas", "cargo", "egreso", "debit"),
    "credito": ("credito", "creditos", "consignacion", "consignaciones", "deposito", "entrada", "abono", "ingreso",
                "credit"),
    "referencia": ("referencia", "documento", "no. documento", "comprobante", "ref", "referencia 2"),
}


def _norm(t):
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower().strip()
    return re.sub(r"[^a-z0-9 ]+", " ", t).strip()


def _numero(v):
    if v is None or v == "":
        return CERO
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v))
    t = str(v).strip().replace("$", "").replace(" ", "")
    neg = t.startswith("-") or t.startswith("(") or t.endswith("-")
    t = t.strip("()-")
    try:
        return -contab.d(t) if neg else contab.d(t)
    except (InvalidOperation, ValueError):
        return CERO


def _fecha(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    t = str(v or "").strip()[:19]
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%y", "%d.%m.%Y", "%Y%m%d", "%d/%m/%Y %H:%M",
                "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def leer_tabla(nombre: str, contenido: bytes):
    """Devuelve lista de filas (listas de celdas) del CSV o de la primera hoja del Excel."""
    if nombre.lower().endswith((".xlsx", ".xlsm")):
        wb = load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
        ws = wb.active
        return [[c for c in fila] for fila in ws.iter_rows(values_only=True)]
    texto = None
    for cod in ("utf-8-sig", "latin-1"):
        try:
            texto = contenido.decode(cod)
            break
        except UnicodeDecodeError:
            continue
    muestra = texto[:4000]
    try:
        dialecto = csv.Sniffer().sniff(muestra, delimiters=";,\t|")
    except csv.Error:
        dialecto = csv.excel
        dialecto.delimiter = ";" if muestra.count(";") > muestra.count(",") else ","
    return [fila for fila in csv.reader(io.StringIO(texto), dialecto)]


def detectar_columnas(filas):
    """Busca la fila de encabezados y devuelve (índice fila encabezado, {campo: índice columna})."""
    for i, fila in enumerate(filas[:30]):
        normal = [_norm(c) for c in fila]
        mapa = {}
        for campo, nombres in NOMBRES.items():
            for j, c in enumerate(normal):
                if c in nombres and campo not in mapa:
                    mapa[campo] = j
        if "fecha" in mapa and ("valor" in mapa or "debito" in mapa or "credito" in mapa):
            return i, mapa
    return None, {}


@dataclass
class LineaExtracto:
    fecha: date
    descripcion: str
    valor: Decimal
    referencia: str | None


def extraer(filas, mapa, fila_encabezado):
    lineas = []
    inicio = fila_encabezado + 1 if fila_encabezado is not None else 0
    for fila in filas[inicio:]:
        if not fila or all(c in (None, "") for c in fila):
            continue

        def celda(campo):
            j = mapa.get(campo)
            return fila[j] if j is not None and j < len(fila) else None

        f = _fecha(celda("fecha"))
        if f is None:
            continue
        if "valor" in mapa:
            valor = _numero(celda("valor"))
        else:
            valor = _numero(celda("credito")) - abs(_numero(celda("debito")))
        if valor == 0:
            continue
        desc = str(celda("descripcion") or "").strip()[:250] or "Movimiento"
        ref = str(celda("referencia") or "").strip()[:80] or None
        lineas.append(LineaExtracto(f, desc, contab.redondear(valor), ref))
    return lineas


def importar_extracto(session, banco: Banco, nombre: str, contenido: bytes, mapa_manual=None):
    """Guarda las líneas nuevas del extracto. Devuelve (nuevas, repetidas, sin_reconocer)."""
    filas = leer_tabla(nombre, contenido)
    if mapa_manual:
        enc, mapa = mapa_manual.get("fila_encabezado"), mapa_manual
    else:
        enc, mapa = detectar_columnas(filas)
    if not mapa:
        raise ValueError("No se reconocieron las columnas del extracto (se necesitan fecha y valor, o débito/crédito).")
    nuevas = repetidas = 0
    for n, l in enumerate(extraer(filas, mapa, enc)):
        huella = hashlib.sha1(f"{l.fecha}|{l.valor}|{_norm(l.descripcion)}|{l.referencia or ''}|{n}".encode()).hexdigest()
        if session.query(MovimientoBanco).filter_by(banco_id=banco.id, huella=huella).first():
            repetidas += 1
            continue
        session.add(MovimientoBanco(banco_id=banco.id, fecha=l.fecha, descripcion=l.descripcion, valor=l.valor,
                                    referencia=l.referencia, huella=huella))
        nuevas += 1
    session.commit()
    return nuevas, repetidas


# ------------------------------------------------------------------ conciliación

def _ya_conciliados(session):
    return {(m.origen_tipo, m.origen_id) for m in session.query(MovimientoBanco).filter_by(estado="conciliado")}


def candidatos(session, mov: MovimientoBanco, dias=5):
    """Movimientos registrados en el programa que podrían corresponder a la línea del extracto."""
    usados = _ya_conciliados(session)
    desde, hasta = mov.fecha - timedelta(days=dias), mov.fecha + timedelta(days=dias)
    res = []
    cuenta = mov.banco.cuenta
    if mov.valor > 0:
        for r in session.query(Recaudo).filter(Recaudo.banco_id == mov.banco_id, Recaudo.fecha.between(desde, hasta),
                                               Recaudo.valor == mov.valor):
            if ("recaudo", r.id) not in usados:
                res.append(("recaudo", r.id, f"Recaudo {r.cliente.nombre} {r.fecha:%d/%m}", r.valor))
    else:
        v = -mov.valor
        for p in session.query(PagoGasto).filter(PagoGasto.cuenta_pago == cuenta, PagoGasto.fecha.between(desde, hasta),
                                                 PagoGasto.valor == v):
            if ("pagogasto", p.id) not in usados:
                nombre = p.gasto.proveedor.nombre if p.gasto.proveedor else p.gasto.categoria.nombre
                res.append(("pagogasto", p.id, f"Pago {nombre} {p.fecha:%d/%m}", p.valor))
        from .models import Gasto
        for g in session.query(Gasto).filter(Gasto.cuenta_pago == cuenta, Gasto.forma_pago == "contado",
                                             Gasto.fecha.between(desde, hasta), Gasto.total == v):
            if ("gasto", g.id) not in usados:
                nombre = g.proveedor.nombre if g.proveedor else g.categoria.nombre
                res.append(("gasto", g.id, f"Gasto {nombre} {g.fecha:%d/%m}", g.total))
        for p in session.query(PagoImpuesto).filter(PagoImpuesto.banco_id == mov.banco_id,
                                                    PagoImpuesto.fecha.between(desde, hasta)):
            if p.total == v and ("impuesto", p.id) not in usados:
                res.append(("impuesto", p.id, f"Impuesto {p.formulario} {p.fecha:%d/%m}", p.total))
    # Asientos manuales sobre la cuenta del banco
    for m in (session.query(Movimiento).join(Asiento).filter(Movimiento.cuenta == cuenta, Asiento.origen.is_(None),
                                                            Asiento.fecha.between(desde, hasta))):
        neto = m.debito - m.credito
        if neto == mov.valor and ("asiento", m.asiento_id) not in usados:
            res.append(("asiento", m.asiento_id, f"Asiento {m.asiento.descripcion[:40]}", abs(neto)))
    return res


def conciliar_automatico(session, banco_id):
    n = 0
    for mov in session.query(MovimientoBanco).filter_by(banco_id=banco_id, estado="pendiente"):
        c = candidatos(session, mov)
        if len(c) == 1:
            mov.origen_tipo, mov.origen_id, mov.estado = c[0][0], c[0][1], "conciliado"
            n += 1
    session.commit()
    return n


def saldo_extracto(session, banco_id, hasta=None):
    q = session.query(MovimientoBanco).filter_by(banco_id=banco_id)
    if hasta:
        q = q.filter(MovimientoBanco.fecha <= hasta)
    return sum((m.valor for m in q), CERO)


def sugerir_categoria_bancaria(descripcion: str):
    """Para cargos típicos del banco: 4x1000, comisiones, cuota de manejo, intereses."""
    d = _norm(descripcion)
    if "4x1000" in d or "gmf" in d or "gravamen" in d or "4 x 1000" in d:
        return "Gravamen 4x1000"
    if "comision" in d or "cuota de manejo" in d or "cuota manejo" in d:
        return "Comisiones (pasarela Bold, bancos)"
    if "iva" in d and ("comision" in d or "cuota" in d):
        return "Comisiones (pasarela Bold, bancos)"
    if "interes" in d:
        return "Intereses"
    return None
