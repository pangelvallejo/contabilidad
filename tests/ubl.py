"""Constructor de XML UBL 2.1 de prueba con la misma estructura que entrega la DIAN (datos ficticios)."""

NS = ('xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" '
      'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2" '
      'xmlns:ext="urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"')

EMPRESA = ("901913577", "4", "ANGEL LECOMPTE S.A.S.")


def _parte(nit, dv, nombre, ciudad="BOGOTÁ, D.C.", cod="11001"):
    return f"""<cac:Party><cac:PartyName><cbc:Name>{nombre}</cbc:Name></cac:PartyName>
      <cac:PhysicalLocation><cac:Address><cbc:ID>{cod}</cbc:ID><cbc:CityName>{ciudad}</cbc:CityName>
        <cac:AddressLine><cbc:Line>CL 1 # 2-3</cbc:Line></cac:AddressLine></cac:Address></cac:PhysicalLocation>
      <cac:PartyTaxScheme><cbc:RegistrationName>{nombre}</cbc:RegistrationName>
        <cbc:CompanyID schemeID="{dv}" schemeName="31" schemeAgencyID="195">{nit}</cbc:CompanyID>
        <cac:TaxScheme><cbc:ID>01</cbc:ID><cbc:Name>IVA</cbc:Name></cac:TaxScheme></cac:PartyTaxScheme>
      <cac:Contact><cbc:ElectronicMail>contacto@{nit}.co</cbc:ElectronicMail></cac:Contact></cac:Party>"""


def documento(*, tipo="Invoice", numero, cufe, fecha, emisor, receptor, base, iva, descripcion="Honorarios",
              vence=None, forma_pago="2", reteiva=None, referencia=None, inc=0):
    total = base + iva + inc
    impuestos = ""
    if iva:
        impuestos += f"""<cac:TaxTotal><cbc:TaxAmount currencyID="COP">{iva}</cbc:TaxAmount>
      <cac:TaxSubtotal><cbc:TaxableAmount currencyID="COP">{base}</cbc:TaxableAmount><cbc:TaxAmount currencyID="COP">{iva}</cbc:TaxAmount>
      <cac:TaxCategory><cbc:Percent>19.00</cbc:Percent><cac:TaxScheme><cbc:ID>01</cbc:ID><cbc:Name>IVA</cbc:Name></cac:TaxScheme></cac:TaxCategory></cac:TaxSubtotal></cac:TaxTotal>"""
    if inc:
        impuestos += f"""<cac:TaxTotal><cbc:TaxAmount currencyID="COP">{inc}</cbc:TaxAmount>
      <cac:TaxSubtotal><cbc:TaxableAmount currencyID="COP">{base}</cbc:TaxableAmount><cbc:TaxAmount currencyID="COP">{inc}</cbc:TaxAmount>
      <cac:TaxCategory><cbc:Percent>8.00</cbc:Percent><cac:TaxScheme><cbc:ID>04</cbc:ID><cbc:Name>INC</cbc:Name></cac:TaxScheme></cac:TaxCategory></cac:TaxSubtotal></cac:TaxTotal>"""
    retencion = ""
    if reteiva:
        retencion = f"""<cac:WithholdingTaxTotal><cbc:TaxAmount currencyID="COP">{reteiva}</cbc:TaxAmount>
      <cac:TaxSubtotal><cbc:TaxableAmount currencyID="COP">{iva}</cbc:TaxableAmount><cbc:TaxAmount currencyID="COP">{reteiva}</cbc:TaxAmount>
      <cac:TaxCategory><cbc:Percent>15.00</cbc:Percent><cac:TaxScheme><cbc:ID>05</cbc:ID><cbc:Name>ReteIVA</cbc:Name></cac:TaxScheme></cac:TaxCategory></cac:TaxSubtotal></cac:WithholdingTaxTotal>"""
    ref = ""
    if referencia:
        ref = f"""<cac:BillingReference><cac:InvoiceDocumentReference><cbc:ID>{referencia[0]}</cbc:ID>
      <cbc:UUID schemeName="CUFE-SHA384">{referencia[1]}</cbc:UUID></cac:InvoiceDocumentReference></cac:BillingReference>"""
    linea = {"Invoice": "InvoiceLine", "CreditNote": "CreditNoteLine", "DebitNote": "DebitNoteLine"}[tipo]
    cant = {"Invoice": "InvoicedQuantity", "CreditNote": "CreditedQuantity", "DebitNote": "DebitedQuantity"}[tipo]
    totales = "RequestedMonetaryTotal" if tipo == "DebitNote" else "LegalMonetaryTotal"
    return f"""<?xml version="1.0" encoding="utf-8"?>
<{tipo} xmlns="urn:oasis:names:specification:ubl:schema:xsd:{tipo}-2" {NS}>
  <ext:UBLExtensions><ext:UBLExtension><ext:ExtensionContent/></ext:UBLExtension></ext:UBLExtensions>
  <cbc:UBLVersionID>UBL 2.1</cbc:UBLVersionID><cbc:CustomizationID>10</cbc:CustomizationID>
  <cbc:ID>{numero}</cbc:ID><cbc:UUID schemeID="1" schemeName="CUFE-SHA384">{cufe}</cbc:UUID>
  <cbc:IssueDate>{fecha}</cbc:IssueDate><cbc:IssueTime>10:00:00-05:00</cbc:IssueTime>
  {"<cbc:InvoiceTypeCode>01</cbc:InvoiceTypeCode>" if tipo == "Invoice" else ""}
  <cbc:Note>Documento de prueba</cbc:Note><cbc:DocumentCurrencyCode>COP</cbc:DocumentCurrencyCode>
  {ref}
  <cac:AccountingSupplierParty><cbc:AdditionalAccountID>1</cbc:AdditionalAccountID>{_parte(*emisor)}</cac:AccountingSupplierParty>
  <cac:AccountingCustomerParty><cbc:AdditionalAccountID>1</cbc:AdditionalAccountID>{_parte(*receptor)}</cac:AccountingCustomerParty>
  <cac:PaymentMeans><cbc:ID>{forma_pago}</cbc:ID><cbc:PaymentMeansCode>ZZZ</cbc:PaymentMeansCode>
    <cbc:PaymentDueDate>{vence or "0001-01-01"}</cbc:PaymentDueDate></cac:PaymentMeans>
  {impuestos}{retencion}
  <cac:{totales}><cbc:LineExtensionAmount currencyID="COP">{base}.00</cbc:LineExtensionAmount>
    <cbc:TaxExclusiveAmount currencyID="COP">{base if iva else 0}.00</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="COP">{total}.00</cbc:TaxInclusiveAmount>
    <cbc:AllowanceTotalAmount currencyID="COP">0.00</cbc:AllowanceTotalAmount>
    <cbc:PayableAmount currencyID="COP">{total}.00</cbc:PayableAmount></cac:{totales}>
  <cac:{linea}><cbc:ID>1</cbc:ID><cbc:{cant} unitCode="94">1</cbc:{cant}>
    <cbc:LineExtensionAmount currencyID="COP">{base}</cbc:LineExtensionAmount>
    {f'<cac:TaxTotal><cbc:TaxAmount currencyID="COP">{iva}</cbc:TaxAmount><cac:TaxSubtotal><cbc:TaxableAmount currencyID="COP">{base}</cbc:TaxableAmount><cbc:TaxAmount currencyID="COP">{iva}</cbc:TaxAmount><cac:TaxCategory><cbc:Percent>19.00</cbc:Percent><cac:TaxScheme><cbc:ID>01</cbc:ID></cac:TaxScheme></cac:TaxCategory></cac:TaxSubtotal></cac:TaxTotal>' if iva else ''}
    <cac:Item><cbc:Description>{descripcion}</cbc:Description></cac:Item>
    <cac:Price><cbc:PriceAmount currencyID="COP">{base}</cbc:PriceAmount><cbc:BaseQuantity unitCode="94">1</cbc:BaseQuantity></cac:Price>
  </cac:{linea}>
</{tipo}>"""


def contenedor(interno: str, numero: str, cufe: str, fecha: str, emisor, receptor) -> str:
    """Envuelve el documento en un AttachedDocument como el que entrega la DIAN."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<AttachedDocument xmlns="urn:oasis:names:specification:ubl:schema:xsd:AttachedDocument-2" {NS}>
  <cbc:UBLVersionID>UBL 2.1</cbc:UBLVersionID><cbc:CustomizationID>Documentos adjuntos</cbc:CustomizationID>
  <cbc:ProfileID>Factura Electrónica de Venta</cbc:ProfileID><cbc:ID>{numero}</cbc:ID>
  <cbc:IssueDate>{fecha}</cbc:IssueDate><cbc:DocumentType>Contenedor de Factura Electrónica de Venta</cbc:DocumentType>
  <cbc:ParentDocumentID>{numero}</cbc:ParentDocumentID>
  <cac:SenderParty><cac:PartyTaxScheme><cbc:RegistrationName>{emisor[2]}</cbc:RegistrationName>
    <cbc:CompanyID schemeID="{emisor[1]}" schemeName="31">{emisor[0]}</cbc:CompanyID></cac:PartyTaxScheme></cac:SenderParty>
  <cac:ReceiverParty><cac:PartyTaxScheme><cbc:RegistrationName>{receptor[2]}</cbc:RegistrationName>
    <cbc:CompanyID schemeID="{receptor[1]}" schemeName="31">{receptor[0]}</cbc:CompanyID></cac:PartyTaxScheme></cac:ReceiverParty>
  <cac:Attachment><cac:ExternalReference><cbc:MimeCode>text/xml</cbc:MimeCode><cbc:EncodingCode>UTF-8</cbc:EncodingCode>
    <cbc:Description><![CDATA[{interno}]]></cbc:Description></cac:ExternalReference></cac:Attachment>
  <cac:ParentDocumentLineReference><cbc:LineID>1</cbc:LineID><cac:DocumentReference><cbc:ID>{numero}</cbc:ID>
    <cbc:UUID schemeName="CUFE-SHA384">{cufe}</cbc:UUID><cbc:IssueDate>{fecha}</cbc:IssueDate>
    <cbc:DocumentType>ApplicationResponse</cbc:DocumentType>
    <cac:ResultOfVerification><cbc:ValidatorID>DIAN</cbc:ValidatorID><cbc:ValidationResultCode>02</cbc:ValidationResultCode>
    </cac:ResultOfVerification></cac:DocumentReference></cac:ParentDocumentLineReference>
</AttachedDocument>"""


def factura_venta(numero, fecha, cliente, base, *, cufe=None, contenedor_dian=True, **kw):
    cufe = cufe or f"cufe-{numero.lower()}"
    iva = round(base * 0.19)
    interno = documento(numero=numero, cufe=cufe, fecha=fecha, emisor=EMPRESA, receptor=cliente, base=base, iva=iva, **kw)
    return contenedor(interno, numero, cufe, fecha, EMPRESA, cliente) if contenedor_dian else interno


def factura_compra(numero, fecha, proveedor, base, iva, *, cufe=None, **kw):
    cufe = cufe or f"cufe-{numero.lower()}"
    interno = documento(numero=numero, cufe=cufe, fecha=fecha, emisor=proveedor, receptor=EMPRESA, base=base, iva=iva,
                        **kw)
    return contenedor(interno, numero, cufe, fecha, proveedor, EMPRESA)
