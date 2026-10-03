"""Lectura de documentos electrónicos DIAN (UBL 2.1).

Acepta el contenedor `AttachedDocument` que entrega el software gratuito de la DIAN y los
proveedores, o el `Invoice` / `CreditNote` / `DebitNote` directo. También archivos ZIP.
"""
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

TIPOS_RAIZ = {"Invoice": "FV", "CreditNote": "NC", "DebitNote": "ND"}
IMPUESTOS = {"01": "IVA", "03": "ICA", "04": "INC", "05": "ReteIVA", "06": "ReteRenta", "07": "ReteICA"}


class ErrorXML(Exception):
    pass


@dataclass
class Parte:
    nit: str = ""
    dv: str = ""
    tipo_doc: str = "31"
    nombre: str = ""
    email: str = ""
    telefono: str = ""
    direccion: str = ""
    ciudad: str = ""
    cod_municipio: str = ""


@dataclass
class Linea:
    descripcion: str
    cantidad: Decimal
    base: Decimal
    iva_pct: Decimal
    iva: Decimal


@dataclass
class DocumentoDIAN:
    tipo: str  # FV, NC, ND; DS para documento soporte
    numero: str
    cufe: str
    fecha: date
    vencimiento: date | None
    moneda: str
    emisor: Parte
    receptor: Parte
    subtotal: Decimal
    descuentos: Decimal
    cargos: Decimal
    base_gravada: Decimal
    total: Decimal
    impuestos: dict = field(default_factory=dict)  # {"IVA": valor, "INC": valor}
    retenciones: dict = field(default_factory=dict)  # {"ReteIVA": valor}
    lineas: list = field(default_factory=list)
    notas: list = field(default_factory=list)
    referencia_numero: str = ""
    referencia_cufe: str = ""
    forma_pago: str = ""  # 1 contado, 2 crédito
    validado_dian: bool | None = None

    @property
    def iva(self):
        return self.impuestos.get("IVA", Decimal(0))

    @property
    def otros_impuestos(self):
        return sum((v for k, v in self.impuestos.items() if k != "IVA"), Decimal(0))

    @property
    def descripcion(self):
        textos = []
        for l in self.lineas:
            if l.descripcion and l.descripcion not in textos:
                textos.append(l.descripcion)
        return "; ".join(textos)[:500]


def _p(ruta):
    """'cac:A/cbc:B' -> '{*}A/{*}B' (ignora prefijos de espacio de nombres)."""
    return "/".join("{*}" + parte.split(":")[-1] if parte not in (".", "..") else parte
                    for parte in ruta.split("/"))


def _txt(el, ruta, defecto=""):
    if el is None:
        return defecto
    n = el.find(_p(ruta))
    return n.text.strip() if n is not None and n.text else defecto


def _dec(el, ruta):
    t = _txt(el, ruta)
    try:
        return Decimal(t) if t else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def _fecha(t):
    if not t or t.startswith("0001"):
        return None
    try:
        return date.fromisoformat(t[:10])
    except ValueError:
        return None


def nit_limpio(texto: str, dv: str = "") -> str:
    """Deja solo dígitos; si viene el DV pegado (10 dígitos terminados en el DV conocido) lo quita."""
    digitos = re.sub(r"\D", "", texto or "")
    if dv and len(digitos) == 10 and digitos[-1] == dv:
        digitos = digitos[:-1]
    return digitos


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _parte(party):
    p = Parte()
    if party is None:
        return p
    for ruta in ("cac:PartyTaxScheme/cbc:CompanyID", "cac:PartyLegalEntity/cbc:CompanyID",
                 "cac:PartyIdentification/cbc:ID"):
        n = party.find(_p(ruta))
        if n is not None and n.text and n.text.strip():
            p.dv = n.get("schemeID", "") if len(n.get("schemeID", "")) == 1 else ""
            p.nit = nit_limpio(n.text, p.dv) or n.text.strip()
            p.tipo_doc = n.get("schemeName", "31") or "31"
            break
    p.nombre = (_txt(party, "cac:PartyTaxScheme/cbc:RegistrationName")
                or _txt(party, "cac:PartyLegalEntity/cbc:RegistrationName")
                or _txt(party, "cac:PartyName/cbc:Name"))
    p.email = _txt(party, "cac:Contact/cbc:ElectronicMail")
    p.telefono = _txt(party, "cac:Contact/cbc:Telephone")
    for ruta in ("cac:PartyTaxScheme/cac:RegistrationAddress", "cac:PhysicalLocation/cac:Address"):
        dirn = party.find(_p(ruta))
        if dirn is not None:
            p.direccion = _txt(dirn, "cac:AddressLine/cbc:Line")
            p.ciudad = _txt(dirn, "cbc:CityName")
            p.cod_municipio = _txt(dirn, "cbc:ID")
            break
    return p


def _impuestos(contenedor, etiqueta):
    """Suma por tipo de impuesto los TaxTotal/WithholdingTaxTotal directos del documento."""
    res, bases = {}, {}
    for total in contenedor.findall(_p(etiqueta)):
        for sub in total.findall(_p("cac:TaxSubtotal")):
            codigo = _txt(sub, "cac:TaxCategory/cac:TaxScheme/cbc:ID")
            nombre = IMPUESTOS.get(codigo, _txt(sub, "cac:TaxCategory/cac:TaxScheme/cbc:Name") or codigo)
            res[nombre] = res.get(nombre, Decimal(0)) + _dec(sub, "cbc:TaxAmount")
            bases[nombre] = bases.get(nombre, Decimal(0)) + _dec(sub, "cbc:TaxableAmount")
    return res, bases


def _documento_ubl(raiz) -> DocumentoDIAN:
    nombre = _local(raiz.tag)
    tipo = TIPOS_RAIZ[nombre]
    if tipo == "FV" and _txt(raiz, "cbc:InvoiceTypeCode") == "05":
        tipo = "DS"
    impuestos, bases = _impuestos(raiz, "cac:TaxTotal")
    retenciones, _ = _impuestos(raiz, "cac:WithholdingTaxTotal")
    tot = raiz.find(_p("cac:LegalMonetaryTotal"))
    if tot is None:
        tot = raiz.find(_p("cac:RequestedMonetaryTotal"))  # notas débito
    lineas = []
    etiqueta_linea = {"FV": "InvoiceLine", "DS": "InvoiceLine", "NC": "CreditNoteLine", "ND": "DebitNoteLine"}[tipo]
    for ln in raiz.findall("{*}" + etiqueta_linea):
        desc = _txt(ln, "cac:Item/cbc:Description") or _txt(ln, "cbc:Note")
        cantidad = (_dec(ln, "cbc:InvoicedQuantity") or _dec(ln, "cbc:CreditedQuantity")
                    or _dec(ln, "cbc:DebitedQuantity") or Decimal(1))
        iva = Decimal(0)
        pct = Decimal(0)
        for sub in ln.findall(_p("cac:TaxTotal/cac:TaxSubtotal")):
            if _txt(sub, "cac:TaxCategory/cac:TaxScheme/cbc:ID") == "01":
                iva += _dec(sub, "cbc:TaxAmount")
                pct = _dec(sub, "cac:TaxCategory/cbc:Percent") or pct
        lineas.append(Linea(desc, cantidad, _dec(ln, "cbc:LineExtensionAmount"), pct, iva))

    medios = raiz.findall(_p("cac:PaymentMeans"))
    medio = next((m for m in medios if _txt(m, "cbc:ID") == "2"), None) or next(
        (m for m in medios if _fecha(_txt(m, "cbc:PaymentDueDate"))), None) or (medios[0] if medios else None)
    vencimiento = _fecha(_txt(medio, "cbc:PaymentDueDate")) or _fecha(_txt(raiz, "cbc:DueDate"))
    return DocumentoDIAN(
        tipo=tipo,
        numero=_txt(raiz, "cbc:ID"),
        cufe=_txt(raiz, "cbc:UUID"),
        fecha=_fecha(_txt(raiz, "cbc:IssueDate")),
        vencimiento=vencimiento,
        moneda=_txt(raiz, "cbc:DocumentCurrencyCode", "COP"),
        emisor=_parte(raiz.find(_p("cac:AccountingSupplierParty/cac:Party"))),
        receptor=_parte(raiz.find(_p("cac:AccountingCustomerParty/cac:Party"))),
        subtotal=_dec(tot, "cbc:LineExtensionAmount"),
        descuentos=_dec(tot, "cbc:AllowanceTotalAmount"),
        cargos=_dec(tot, "cbc:ChargeTotalAmount"),
        base_gravada=bases.get("IVA", Decimal(0)),
        total=_dec(tot, "cbc:PayableAmount"),
        impuestos=impuestos,
        retenciones=retenciones,
        lineas=lineas,
        notas=[n.text.strip() for n in raiz.findall("{*}Note") if n.text and n.text.strip()],
        referencia_numero=_txt(raiz, "cac:BillingReference/cac:InvoiceDocumentReference/cbc:ID"),
        referencia_cufe=_txt(raiz, "cac:BillingReference/cac:InvoiceDocumentReference/cbc:UUID"),
        forma_pago=_txt(medio, "cbc:ID"),
    )


def leer_xml(contenido: bytes | str) -> DocumentoDIAN:
    try:
        if isinstance(contenido, str):
            # Texto ya decodificado (CDATA): se ignora la declaración encoding interna.
            raiz = ET.fromstring(contenido.lstrip("\ufeff \r\n\t"))
        else:
            raiz = ET.fromstring(contenido.lstrip(b"\xef\xbb\xbf \r\n\t"))
    except ET.ParseError as e:
        raise ErrorXML(f"El archivo no es un XML válido: {e}") from e
    nombre = _local(raiz.tag)
    if nombre == "AttachedDocument":
        interno = raiz.find(_p("cac:Attachment/cac:ExternalReference/cbc:Description"))
        if interno is None or not (interno.text or "").strip():
            raise ErrorXML("El contenedor AttachedDocument no trae el documento electrónico adjunto.")
        doc = leer_xml(interno.text.strip())
        codigo = _txt(raiz, "cac:ParentDocumentLineReference/cac:DocumentReference/"
                            "cac:ResultOfVerification/cbc:ValidationResultCode")
        if codigo:
            doc.validado_dian = codigo.lstrip("0") == "2"
        return doc
    if nombre in TIPOS_RAIZ:
        return _documento_ubl(raiz)
    if nombre == "ApplicationResponse":
        raise ErrorXML("Es un acuse/evento de la DIAN (ApplicationResponse), no una factura.")
    raise ErrorXML(f"Tipo de documento no reconocido: {nombre}")


@dataclass
class ArchivoLeido:
    nombre: str
    documento: DocumentoDIAN | None
    xml: bytes | None
    pdf: bytes | None
    pdf_nombre: str | None
    error: str | None = None


def _base_documento(ruta: str) -> str:
    """'carpeta/ad09001234.xml' -> '09001234': sin carpeta, extensión ni prefijo ad/fv/fe/nc/nd de la DIAN."""
    base = ruta.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
    return re.sub(r"^(ad|fv|fe|nc|nd|ar)", "", base)


def _util(ruta: str) -> bool:
    partes = ruta.replace("\\", "/").split("/")
    return "__MACOSX" not in partes and not partes[-1].startswith("._")


def leer_archivo(nombre: str, contenido: bytes, _nivel=0) -> list[ArchivoLeido]:
    """Lee un .xml o un .zip (con uno o varios XML y sus PDF; admite ZIP dentro de ZIP)."""
    if nombre.lower().endswith(".zip") or contenido[:2] == b"PK":
        if _nivel > 2:
            return [ArchivoLeido(nombre, None, None, None, None, "ZIP con demasiados niveles anidados")]
        try:
            z = zipfile.ZipFile(io.BytesIO(contenido))
        except zipfile.BadZipFile:
            return [ArchivoLeido(nombre, None, None, None, None, "ZIP dañado o no válido")]
        entradas = [n for n in z.namelist() if _util(n) and not n.endswith("/")]
        xmls = [n for n in entradas if n.lower().endswith(".xml")]
        pdfs = [n for n in entradas if n.lower().endswith(".pdf")]
        res = []
        for x in xmls:
            r = leer_archivo(x, z.read(x))[0]
            pdf = next((p for p in pdfs if _base_documento(p) == _base_documento(x)), None)
            if pdf is None and r.documento is not None and r.documento.numero:
                pdf = next((p for p in pdfs if r.documento.numero.lower() in p.lower()), None)
            if pdf is None and len(xmls) == 1 and len(pdfs) == 1:
                pdf = pdfs[0]
            if pdf:
                r.pdf, r.pdf_nombre = z.read(pdf), pdf.rsplit("/", 1)[-1]
            res.append(r)
        for interno in (n for n in entradas if n.lower().endswith(".zip")):
            res.extend(leer_archivo(interno, z.read(interno), _nivel + 1))
        if not res:
            res.append(ArchivoLeido(nombre, None, None, None, None, "El ZIP no contiene archivos XML"))
        return res
    try:
        return [ArchivoLeido(nombre, leer_xml(contenido), contenido, None, None)]
    except ErrorXML as e:
        return [ArchivoLeido(nombre, None, contenido, None, None, str(e))]
