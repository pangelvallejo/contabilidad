"""Convierte documentos DIAN leídos en facturas de venta o gastos."""
import re
import unicodedata
from dataclasses import dataclass
from datetime import timedelta

from . import archivos, contab
from .config import TARIFA_RETEIVA
from .dian_xml import DocumentoDIAN, Parte, leer_archivo, nit_limpio
from .formato import pesos
from .models import Banco, CategoriaGasto, DocumentoVenta, Gasto, LineaVenta, Tercero


@dataclass
class Resultado:
    archivo: str
    ok: bool
    mensaje: str
    url: str | None = None


def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", texto)


def tercero_desde_parte(session, parte: Parte, *, cliente=False, proveedor=False) -> Tercero:
    nit = nit_limpio(parte.nit, parte.dv) or parte.nit
    t = session.query(Tercero).filter_by(nit=nit).one_or_none()
    if t is None:
        t = Tercero(nit=nit, nombre=parte.nombre or nit, plazo_dias=30)
        session.add(t)
    # Completa datos faltantes sin pisar lo que el usuario ya editó
    for campo in ("dv", "email", "telefono", "direccion", "ciudad", "cod_municipio"):
        valor = getattr(parte, campo)
        if valor and not getattr(t, campo):
            setattr(t, campo, valor[:200])
    if parte.tipo_doc and t.tipo_doc in (None, "", "31"):
        t.tipo_doc = parte.tipo_doc
    t.es_cliente = t.es_cliente or cliente
    t.es_proveedor = t.es_proveedor or proveedor
    session.flush()
    return t


def _buscar_clave(texto_n: str, clave: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(clave)}(?:es|s)?(?![a-z0-9])", texto_n))


def sugerir_categoria(session, proveedor: Tercero | None, texto: str) -> tuple[CategoriaGasto, bool]:
    """Devuelve (categoría, segura). Prioriza la última categoría usada con el mismo proveedor,
    luego las palabras clave que coinciden con el nombre del proveedor y por último con la descripción."""
    if proveedor is not None and proveedor.id:
        ultimo = (session.query(Gasto).filter(Gasto.proveedor_id == proveedor.id, Gasto.revisado.is_(True))
                  .order_by(Gasto.fecha.desc(), Gasto.id.desc()).first())
        if ultimo:
            return ultimo.categoria, True
    nombre_n = f" {normalizar(proveedor.nombre if proveedor else '')} "
    texto_n = f" {normalizar(texto)} "
    mejor, puntos = None, (0, 0)
    for cat in session.query(CategoriaGasto).filter_by(activa=True):
        for clave in (cat.palabras_clave or "").split(","):
            clave = normalizar(clave).strip()
            if not clave:
                continue
            if _buscar_clave(nombre_n, clave):
                candidato = (2, len(clave))
            elif _buscar_clave(texto_n, clave):
                candidato = (1, len(clave))
            else:
                continue
            if candidato > puntos:
                mejor, puntos = cat, candidato
    if mejor:
        return mejor, False
    defecto = (session.query(CategoriaGasto).filter_by(nombre="Otros gastos").first()
               or session.query(CategoriaGasto).filter_by(activa=True).order_by(CategoriaGasto.id).first())
    if defecto is None:
        raise ValueError("No hay categorías de gasto activas; cree una en Configuración.")
    return defecto, False


def _guardar_adjuntos(carpeta, leido):
    xml = archivos.guardar(carpeta, leido.nombre.rsplit("/", 1)[-1] if leido.nombre.lower().endswith(".xml")
                           else "documento.xml", leido.xml) if leido.xml else None
    pdf = archivos.guardar(carpeta, leido.pdf_nombre, leido.pdf) if leido.pdf else None
    return xml, pdf


def _existe(session, doc: DocumentoDIAN, direccion: str):
    """Documento ya registrado: por CUFE o, si no trae, por número, tercero y fecha."""
    if doc.cufe:
        return (session.query(DocumentoVenta).filter_by(cufe=doc.cufe).first()
                or session.query(Gasto).filter_by(cufe=doc.cufe).first())
    if direccion == "venta":
        return (session.query(DocumentoVenta).join(Tercero, DocumentoVenta.cliente_id == Tercero.id)
                .filter(DocumentoVenta.numero == doc.numero, DocumentoVenta.fecha == doc.fecha,
                        Tercero.nit == nit_limpio(doc.receptor.nit, doc.receptor.dv)).first())
    return (session.query(Gasto).join(Tercero, Gasto.proveedor_id == Tercero.id)
            .filter(Gasto.numero == doc.numero, Gasto.fecha == doc.fecha,
                    Tercero.nit == nit_limpio(doc.emisor.nit, doc.emisor.dv)).first())


def importar_venta(session, doc: DocumentoDIAN, leido) -> DocumentoVenta:
    cliente = tercero_desde_parte(session, doc.receptor, cliente=True)
    tipo = doc.tipo if doc.tipo in ("FV", "NC", "ND") else "FV"
    v = DocumentoVenta(tipo=tipo, numero=doc.numero, cufe=doc.cufe or None, fecha=doc.fecha, cliente=cliente,
                       subtotal=doc.subtotal, descuentos=doc.descuentos, base_gravada=doc.base_gravada,
                       iva=doc.iva, total=doc.total, notas="\n".join(doc.notas)[:2000] or None)
    v.vencimiento = doc.vencimiento or (doc.fecha + timedelta(days=cliente.plazo_dias or 0))
    if tipo == "NC":
        ref = None
        if doc.referencia_cufe:
            ref = session.query(DocumentoVenta).filter_by(cufe=doc.referencia_cufe).first()
        if ref is None and doc.referencia_numero:
            ref = session.query(DocumentoVenta).filter_by(numero=doc.referencia_numero, tipo="FV").first()
        if ref is None:
            raise ValueError(f"La nota crédito afecta la factura {doc.referencia_numero or '(sin referencia)'}, "
                             "que no está registrada. Importe primero esa factura.")
        v.referencia = ref
    rete_xml = doc.retenciones.get("ReteIVA")
    if tipo != "NC" and (rete_xml or cliente.aplica_reteiva):
        v.reteiva_aplica = True
        v.reteiva_valor = rete_xml or contab.redondear(doc.iva * contab.d(TARIFA_RETEIVA), "1")
        v.reteiva_fecha = doc.fecha
    for l in doc.lineas:
        v.lineas.append(LineaVenta(descripcion=l.descripcion, cantidad=l.cantidad, base=l.base,
                                   iva_pct=l.iva_pct, iva=l.iva))
    session.add(v)
    session.flush()
    contab.contabilizar_venta(session, v)
    v.xml_archivo, v.pdf_archivo = _guardar_adjuntos("ventas", leido)
    return v


def importar_gasto(session, doc: DocumentoDIAN, leido, origen="xml") -> Gasto:
    proveedor = tercero_desde_parte(session, doc.emisor, proveedor=True)
    categoria, segura = sugerir_categoria(session, proveedor, f"{doc.descripcion} {proveedor.nombre}")
    credito = doc.forma_pago == "2"  # forma de pago "crédito" según el XML
    vencimiento = doc.vencimiento if (doc.vencimiento and doc.vencimiento > doc.fecha) else None
    if credito and vencimiento is None:
        vencimiento = doc.fecha + timedelta(days=proveedor.plazo_dias or 30)
    g = Gasto(tipo_soporte="NC" if doc.tipo == "NC" else ("DS" if doc.tipo == "DS" else "FE"),
              numero=doc.numero, cufe=doc.cufe or None, fecha=doc.fecha, vencimiento=vencimiento,
              proveedor=proveedor, categoria=categoria, descripcion=doc.descripcion or None,
              subtotal=doc.subtotal - doc.descuentos + doc.cargos, iva=doc.iva,
              iva_descontable=categoria.iva_descontable_def and doc.iva > 0,
              otros_impuestos=doc.otros_impuestos, total=doc.total, origen=origen, revisado=segura,
              forma_pago="credito" if (credito or doc.tipo == "NC") else "contado")
    if g.forma_pago == "contado":
        # Por defecto queda pagado desde el primer banco; el usuario lo ajusta al revisar.
        banco = session.query(Banco).filter_by(activo=True).order_by(Banco.id).first()
        g.cuenta_pago = banco.cuenta if banco else contab.CTA_CAJA
    session.add(g)
    session.flush()
    contab.contabilizar_gasto(session, g)
    g.xml_archivo, g.soporte_archivo = _guardar_adjuntos("gastos", leido)  # al final: si algo falla no quedan huérfanos
    return g


def importar_archivo(session, nombre: str, contenido: bytes, forzar: str | None = None, origen="xml"):
    """Importa un XML/ZIP. `forzar` = 'venta' | 'gasto' ignora la verificación de NIT (pruebas)."""
    nit = nit_limpio(contab.config(session, "empresa_nit", ""), contab.config(session, "empresa_dv", ""))
    resultados = []
    for leido in leer_archivo(nombre, contenido):
        etiqueta = leido.nombre.rsplit("/", 1)[-1]
        if leido.error:
            resultados.append(Resultado(etiqueta, False, leido.error))
            continue
        doc = leido.documento
        if doc.moneda and doc.moneda != "COP":
            resultados.append(Resultado(etiqueta, False, f"Moneda {doc.moneda} no soportada (solo COP)."))
            continue
        direccion = forzar
        if direccion is None:
            if nit_limpio(doc.emisor.nit, doc.emisor.dv) == nit:
                direccion = "venta"
            elif nit_limpio(doc.receptor.nit, doc.receptor.dv) == nit:
                direccion = "gasto"
        if direccion is None:
            resultados.append(Resultado(
                etiqueta, False,
                f"El documento {doc.numero} no es de la firma: emisor {doc.emisor.nombre} ({doc.emisor.nit}), "
                f"receptor {doc.receptor.nombre} ({doc.receptor.nit})."))
            continue
        if _existe(session, doc, direccion):
            resultados.append(Resultado(etiqueta, False, f"Ya estaba registrado ({doc.numero})."))
            continue
        try:
            with session.begin_nested():
                if direccion == "venta":
                    v = importar_venta(session, doc, leido)
                    nombre_tipo = {"FV": "Factura", "NC": "Nota crédito", "ND": "Nota débito"}[v.tipo]
                    msg = f"{nombre_tipo} {v.numero} a {v.cliente.nombre} por {pesos(v.total)}"
                    resultados.append(Resultado(etiqueta, True, msg, f"/ventas/{v.id}"))
                else:
                    g = importar_gasto(session, doc, leido, origen)
                    msg = f"Gasto {g.numero} de {g.proveedor.nombre} por {pesos(g.total)} → {g.categoria.nombre}"
                    if not g.revisado:
                        msg += " (revisar categoría)"
                    resultados.append(Resultado(etiqueta, True, msg, f"/gastos/{g.id}"))
        except Exception as e:  # noqa: BLE001 — se informa al usuario y se sigue con los demás archivos
            resultados.append(Resultado(etiqueta, False, f"Error al registrar: {e}"))
    session.commit()
    return resultados
