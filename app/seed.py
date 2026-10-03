"""Datos iniciales: PUC, categorías de gasto, bancos, configuración y calendario."""
from datetime import date

from .models import Banco, CategoriaGasto, Config, Cuenta, Vencimiento

# (código, nombre, naturaleza). Las cuentas sin subcuentas en esta lista son de movimiento.
PUC = [
    ("1", "ACTIVO", "D"),
    ("11", "Disponible", "D"),
    ("1105", "Caja", "D"),
    ("110505", "Caja general", "D"),
    ("110510", "Cajas menores", "D"),
    ("1120", "Cuentas de ahorro", "D"),
    ("112005", "Bancos", "D"),
    ("11200501", "Bold CF", "D"),
    ("11200502", "Banco Caja Social", "D"),
    ("13", "Deudores", "D"),
    ("1305", "Clientes", "D"),
    ("130505", "Clientes nacionales", "D"),
    ("1330", "Anticipos y avances", "D"),
    ("133005", "Anticipos a proveedores", "D"),
    ("1355", "Anticipo de impuestos y saldos a favor", "D"),
    ("135517", "Impuesto a las ventas retenido (reteIVA)", "D"),
    ("135595", "Anticipos impuesto unificado SIMPLE", "D"),
    ("1380", "Deudores varios", "D"),
    ("138095", "Otros deudores", "D"),
    ("15", "Propiedades, planta y equipo", "D"),
    ("1524", "Equipo de oficina", "D"),
    ("152405", "Muebles y enseres", "D"),
    ("1528", "Equipo de computación y comunicación", "D"),
    ("152805", "Equipos de procesamiento de datos", "D"),
    ("1592", "Depreciación acumulada", "C"),
    ("159215", "Depreciación equipo de oficina", "C"),
    ("159220", "Depreciación equipo de computación", "C"),
    ("2", "PASIVO", "C"),
    ("22", "Proveedores", "C"),
    ("2205", "Proveedores nacionales", "C"),
    ("220505", "Proveedores nacionales", "C"),
    ("23", "Cuentas por pagar", "C"),
    ("2355", "Deudas con accionistas o socios", "C"),
    ("235505", "Reembolsos de gastos a socios", "C"),
    ("2380", "Acreedores varios", "C"),
    ("238095", "Otros acreedores", "C"),
    ("24", "Impuestos, gravámenes y tasas", "C"),
    ("2404", "De renta y complementarios", "C"),
    ("240405", "Impuesto unificado SIMPLE por pagar", "C"),
    ("2408", "Impuesto sobre las ventas por pagar", "C"),
    ("240805", "IVA generado", "C"),
    ("240810", "IVA descontable", "D"),
    ("240815", "IVA pagado en recibos 2593", "D"),
    ("28", "Otros pasivos", "C"),
    ("2805", "Anticipos y avances recibidos", "C"),
    ("280505", "Anticipos de clientes", "C"),
    ("3", "PATRIMONIO", "C"),
    ("31", "Capital social", "C"),
    ("3105", "Capital suscrito y pagado", "C"),
    ("310505", "Capital suscrito y pagado", "C"),
    ("36", "Resultados del ejercicio", "C"),
    ("3605", "Utilidad del ejercicio", "C"),
    ("360505", "Utilidad del ejercicio", "C"),
    ("3610", "Pérdida del ejercicio", "D"),
    ("361005", "Pérdida del ejercicio", "D"),
    ("37", "Resultados de ejercicios anteriores", "C"),
    ("3705", "Utilidades acumuladas", "C"),
    ("370505", "Utilidades acumuladas", "C"),
    ("4", "INGRESOS", "C"),
    ("41", "Operacionales", "C"),
    ("4155", "Actividades empresariales y de consultoría", "C"),
    ("415595", "Honorarios por servicios jurídicos", "C"),
    ("4175", "Devoluciones en ventas", "D"),
    ("417505", "Notas crédito sobre servicios", "D"),
    ("42", "No operacionales", "C"),
    ("4210", "Financieros", "C"),
    ("421005", "Intereses", "C"),
    ("4250", "Recuperaciones", "C"),
    ("425050", "Reintegro de costos y gastos", "C"),
    ("4295", "Diversos", "C"),
    ("429581", "Ajuste al peso", "C"),
    ("5", "GASTOS", "D"),
    ("51", "Operacionales de administración", "D"),
    ("5110", "Honorarios", "D"),
    ("511025", "Asesoría jurídica", "D"),
    ("511030", "Asesoría contable y financiera", "D"),
    ("511095", "Otros honorarios", "D"),
    ("5115", "Impuestos", "D"),
    ("511595", "Gravamen a los movimientos financieros (4x1000)", "D"),
    ("5120", "Arrendamientos", "D"),
    ("512010", "Construcciones y edificaciones", "D"),
    ("5125", "Contribuciones y afiliaciones", "D"),
    ("512510", "Afiliaciones y sostenimiento", "D"),
    ("5130", "Seguros", "D"),
    ("513095", "Otros seguros", "D"),
    ("5135", "Servicios", "D"),
    ("513505", "Aseo y vigilancia", "D"),
    ("513520", "Procesamiento electrónico de datos (software y nube)", "D"),
    ("513525", "Acueducto y alcantarillado", "D"),
    ("513530", "Energía eléctrica", "D"),
    ("513535", "Teléfono e internet", "D"),
    ("513540", "Correo y mensajería", "D"),
    ("513595", "Otros servicios", "D"),
    ("5140", "Gastos legales", "D"),
    ("514005", "Notariales", "D"),
    ("514010", "Registro mercantil", "D"),
    ("514015", "Trámites y licencias", "D"),
    ("5145", "Mantenimiento y reparaciones", "D"),
    ("514525", "Mantenimiento equipo de computación", "D"),
    ("5155", "Gastos de viaje", "D"),
    ("515505", "Alojamiento y manutención", "D"),
    ("515515", "Pasajes aéreos", "D"),
    ("515520", "Pasajes terrestres", "D"),
    ("5160", "Depreciaciones", "D"),
    ("516015", "Depreciación equipo de oficina", "D"),
    ("516020", "Depreciación equipo de computación", "D"),
    ("5195", "Diversos", "D"),
    ("519520", "Gastos de representación", "D"),
    ("519525", "Elementos de aseo y cafetería", "D"),
    ("519530", "Útiles, papelería y fotocopias", "D"),
    ("519545", "Taxis y buses", "D"),
    ("519560", "Casino y restaurante", "D"),
    ("519565", "Parqueaderos", "D"),
    ("519595", "Otros gastos diversos", "D"),
    ("52", "Operacionales de ventas", "D"),
    ("5235", "Servicios", "D"),
    ("523560", "Publicidad, propaganda y promoción", "D"),
    ("53", "No operacionales", "D"),
    ("5305", "Financieros", "D"),
    ("530505", "Gastos bancarios", "D"),
    ("530515", "Comisiones", "D"),
    ("530520", "Intereses", "D"),
    ("5315", "Gastos extraordinarios", "D"),
    ("531520", "Impuestos asumidos", "D"),
    ("54", "Impuesto de renta y complementarios", "D"),
    ("5405", "Impuesto de renta y complementarios", "D"),
    ("540505", "Impuesto unificado SIMPLE", "D"),
]

# (nombre, cuenta, concepto exógena 1001, palabras clave, IVA descontable por defecto)
CATEGORIAS = [
    ("Arriendo de oficina", "512010", "5005", "arrendamiento,arriendo,canon,administracion propiedad horizontal", True),
    ("Energía", "513530", "5004", "energia,enel,codensa,epm,kwh", True),
    ("Acueducto", "513525", "5004", "acueducto,alcantarillado", True),
    ("Teléfono e internet", "513535", "5004",
     "claro,comcel,movistar,colombia telecomunicaciones,tigo,une epm,etb,wom,internet,telefonia,celular,plan de datos",
     True),
    ("Software y nube", "513520", "5004",
     "microsoft,google,adobe,software,licencia,suscripcion,dropbox,zoom,apple,openai,anthropic,amazon web services,"
     "legis,vlex,notinet,multilegis,saas", True),
    ("Honorarios contables", "511030", "5002", "contador,contable,contabilidad,revisoria", True),
    ("Honorarios jurídicos", "511025", "5002", "honorarios juridicos,abogado", True),
    ("Otros honorarios", "511095", "5002", "honorarios", True),
    ("Notariales", "514005", "5016", "notaria,notarial,autenticacion,escritura", True),
    ("Registro mercantil y cámara de comercio", "514010", "5016",
     "registro mercantil,inscripcion,matricula mercantil,renovacion matricula,impuesto de registro,"
     "certificado de existencia,derechos de inscripcion", True),
    ("Trámites y licencias", "514015", "5016", "tramite,licencia,permiso", True),
    ("Afiliaciones y membresías", "512510", "5016", "afiliacion,membresia,sostenimiento,colegio de abogados,cuota anual",
     True),
    ("Seguros", "513095", "5016", "seguro,poliza,sura,bolivar,allianz,mapfre,axa colpatria", True),
    ("Mantenimiento de equipos", "514525", "5004", "mantenimiento,reparacion,soporte tecnico", True),
    ("Aseo y vigilancia", "513505", "5004", "vigilancia,servicio de aseo", True),
    ("Papelería y útiles", "519530", "5016", "papeleria,panamericana,fotocopia,tinta,toner,impresion,utiles", True),
    ("Aseo y cafetería", "519525", "5016", "cafeteria,cafe,supermercado,exito,carulla,olimpica,jumbo,d1,ara", True),
    ("Restaurantes y atenciones", "519560", "5016", "restaurante,almuerzo,comida,rappi,crepes,starbucks,juan valdez",
     True),
    ("Gastos de representación", "519520", "5016", "representacion,regalo,obsequio", True),
    ("Taxis y transporte urbano", "519545", "5016", "uber,taxi,cabify,didi,indriver,transporte", True),
    ("Parqueaderos", "519565", "5016", "parqueadero,parking,city parking", True),
    ("Mensajería", "513540", "5004",
     "mensajeria,servientrega,envia,interrapidisimo,4-72,coordinadora,deprisa,domicilio", True),
    ("Viajes: tiquetes", "515515", "5016", "avianca,latam,tiquete,vuelo,aerolinea,jetsmart,wingo,satena", True),
    ("Viajes: hoteles y viáticos", "515505", "5016", "hotel,alojamiento,airbnb,hospedaje", True),
    ("Publicidad y página web", "523560", "5004", "publicidad,marketing,linkedin,dominio,hosting,pagina web", True),
    ("Gastos bancarios", "530505", "5016", "cuota de manejo,gastos bancarios,chequera", True),
    ("Comisiones (pasarela Bold, bancos)", "530515", "5016", "comision,bold", True),
    ("Gravamen 4x1000", "511595", "5016", "gmf,4x1000,4 x 1000,gravamen", False),
    ("Intereses", "530520", "5006", "interes", False),
    ("Equipo de cómputo (activo)", "152805", "5008", "computador,portatil,laptop,monitor,macbook,tablet", False),
    ("Muebles y enseres (activo)", "152405", "5008", "escritorio,silla,mueble,archivador", False),
    ("Impuestos asumidos", "531520", "5016", "impuestos asumidos", False),
    ("Otros gastos", "519595", "5016", "", True),
]

CONFIG = {
    "empresa_nombre": "ANGEL LECOMPTE S.A.S.",
    "empresa_nit": "901913577",
    "empresa_dv": "4",
    "empresa_direccion": "",
    "empresa_ciudad": "Bogotá D.C.",
    "empresa_cod_municipio": "11001",
    "empresa_email": "",
    "empresa_telefono": "",
    "empresa_ciiu": "6910",
    # causacion: ingresos según fecha de factura; caja: según recaudos (base gravable SIMPLE)
    "simple_base": "causacion",
    "carpeta_respaldo": "",
    "ultimo_respaldo": "",
    "respaldos_a_conservar": "30",
}

# Calendario 2026 para NIT terminado en 7 (Decreto 2229 de 2023). Editable desde la app.
VENCIMIENTOS = [
    ("Recibo 2593: anticipo SIMPLE + IVA", "Bimestre 1 (ene-feb) 2026", date(2026, 5, 21)),
    ("Recibo 2593: anticipo SIMPLE + IVA", "Bimestre 2 (mar-abr) 2026", date(2026, 6, 19)),
    ("Recibo 2593: anticipo SIMPLE + IVA", "Bimestre 3 (may-jun) 2026", date(2026, 7, 17)),
    ("Recibo 2593: anticipo SIMPLE + IVA", "Bimestre 4 (jul-ago) 2026", date(2026, 9, 17)),
    ("Recibo 2593: anticipo SIMPLE + IVA", "Bimestre 5 (sep-oct) 2026", date(2026, 11, 20)),
    ("Recibo 2593: anticipo SIMPLE + IVA", "Bimestre 6 (nov-dic) 2026", date(2027, 1, 21)),
    ("Declaración anual consolidada de IVA (F300)", "Año gravable 2026", date(2027, 2, 19)),
    ("Declaración anual SIMPLE (F260)", "Año gravable 2026", date(2027, 4, 23)),
]
NOTA_FECHA_ESTIMADA = "Fecha estimada con el calendario 2026; confirmar con el decreto de plazos de 2027."


def sembrar(session):
    if session.get(Cuenta, "1") is None:
        codigos = [c for c, _, _ in PUC]
        for codigo, nombre, nat in PUC:
            tiene_hijas = any(o != codigo and o.startswith(codigo) for o in codigos)
            session.add(Cuenta(codigo=codigo, nombre=nombre, naturaleza=nat, movimiento=not tiene_hijas))
        session.flush()

    if not session.query(CategoriaGasto).first():
        for nombre, cuenta, concepto, claves, desc in CATEGORIAS:
            session.add(CategoriaGasto(nombre=nombre, cuenta=cuenta, concepto_exogena=concepto,
                                       palabras_clave=claves, iva_descontable_def=desc))

    if not session.query(Banco).first():
        session.add(Banco(nombre="Bold CF", cuenta="11200501", tipo="Ahorros"))
        session.add(Banco(nombre="Banco Caja Social", cuenta="11200502", tipo="Ahorros"))

    for clave, valor in CONFIG.items():
        if session.get(Config, clave) is None:
            session.add(Config(clave=clave, valor=valor))

    if not session.query(Vencimiento).first():
        hoy = date.today()
        for obligacion, periodo, fecha in VENCIMIENTOS:
            nota = NOTA_FECHA_ESTIMADA if fecha.year == 2027 and fecha.month > 1 else None
            # Lo vencido antes de instalar el programa se marca cumplido para no generar falsas alertas.
            if fecha < hoy:
                nota = "Vencido antes de instalar el programa; marcado como cumplido. Verifique."
            session.add(Vencimiento(obligacion=obligacion, periodo=periodo, fecha=fecha, notas=nota,
                                    cumplido=fecha < hoy))
    session.commit()
