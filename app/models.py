"""Modelo de datos."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text,
                        UniqueConstraint)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

Dinero = Numeric(18, 2)
CERO = Decimal("0")


class Config(Base):
    __tablename__ = "config"
    clave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str | None] = mapped_column(Text)


class Cuenta(Base):
    """Cuenta del PUC. Las de nivel auxiliar (movimiento=True) reciben asientos."""
    __tablename__ = "cuentas"
    codigo: Mapped[str] = mapped_column(String(12), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120))
    naturaleza: Mapped[str] = mapped_column(String(1))  # D o C
    movimiento: Mapped[bool] = mapped_column(Boolean, default=False)
    activa: Mapped[bool] = mapped_column(Boolean, default=True)


class Tercero(Base):
    __tablename__ = "terceros"
    id: Mapped[int] = mapped_column(primary_key=True)
    tipo_doc: Mapped[str] = mapped_column(String(3), default="31")  # 31 NIT, 13 CC, 22 CE, 42 extranjero
    nit: Mapped[str] = mapped_column(String(20), unique=True)
    dv: Mapped[str | None] = mapped_column(String(1))
    nombre: Mapped[str] = mapped_column(String(200))
    primer_apellido: Mapped[str | None] = mapped_column(String(60))
    segundo_apellido: Mapped[str | None] = mapped_column(String(60))
    primer_nombre: Mapped[str | None] = mapped_column(String(60))
    otros_nombres: Mapped[str | None] = mapped_column(String(60))
    email: Mapped[str | None] = mapped_column(String(120))
    telefono: Mapped[str | None] = mapped_column(String(60))
    direccion: Mapped[str | None] = mapped_column(String(200))
    ciudad: Mapped[str | None] = mapped_column(String(80))
    cod_municipio: Mapped[str | None] = mapped_column(String(5))  # DIVIPOLA 5 dígitos
    pais: Mapped[str] = mapped_column(String(3), default="169")
    es_cliente: Mapped[bool] = mapped_column(Boolean, default=False)
    es_proveedor: Mapped[bool] = mapped_column(Boolean, default=False)
    aplica_reteiva: Mapped[bool] = mapped_column(Boolean, default=False)
    plazo_dias: Mapped[int] = mapped_column(Integer, default=30)
    # Honorarios mensuales fijos (retainer): el tablero avisa si en el mes no se le ha facturado.
    retainer_mensual: Mapped[Decimal | None] = mapped_column(Dinero)
    notas: Mapped[str | None] = mapped_column(Text)

    @property
    def cod_departamento(self):
        return self.cod_municipio[:2] if self.cod_municipio else None

    @property
    def nit_completo(self):
        return f"{self.nit}-{self.dv}" if self.dv else self.nit


class Banco(Base):
    __tablename__ = "bancos"
    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(80))
    numero: Mapped[str | None] = mapped_column(String(40))
    tipo: Mapped[str | None] = mapped_column(String(30))
    cuenta: Mapped[str] = mapped_column(ForeignKey("cuentas.codigo"))
    activo: Mapped[bool] = mapped_column(Boolean, default=True)


class CategoriaGasto(Base):
    __tablename__ = "categorias_gasto"
    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(80), unique=True)
    cuenta: Mapped[str] = mapped_column(ForeignKey("cuentas.codigo"))
    concepto_exogena: Mapped[str] = mapped_column(String(4), default="5016")
    palabras_clave: Mapped[str | None] = mapped_column(Text)  # separadas por coma, para clasificar XML
    # El IVA pagado en activos fijos no es descontable (art. 491 E.T.).
    iva_descontable_def: Mapped[bool] = mapped_column(Boolean, default=True)
    activa: Mapped[bool] = mapped_column(Boolean, default=True)


class Asiento(Base):
    __tablename__ = "asientos"
    __table_args__ = (UniqueConstraint("tipo", "numero"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(String(4))
    numero: Mapped[int] = mapped_column(Integer)
    fecha: Mapped[date] = mapped_column(Date, index=True)
    descripcion: Mapped[str] = mapped_column(String(250))
    tercero_id: Mapped[int | None] = mapped_column(ForeignKey("terceros.id"))
    origen: Mapped[str | None] = mapped_column(String(40), index=True)  # p.ej. "venta:12"
    creado: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    tercero: Mapped[Tercero | None] = relationship()
    lineas: Mapped[list["Movimiento"]] = relationship(
        back_populates="asiento", cascade="all, delete-orphan", order_by="Movimiento.id")

    @property
    def manual(self):
        return self.origen is None

    @property
    def total_debito(self):
        return sum((l.debito for l in self.lineas), CERO)


class Movimiento(Base):
    __tablename__ = "movimientos"
    id: Mapped[int] = mapped_column(primary_key=True)
    asiento_id: Mapped[int] = mapped_column(ForeignKey("asientos.id", ondelete="CASCADE"), index=True)
    cuenta: Mapped[str] = mapped_column(ForeignKey("cuentas.codigo"), index=True)
    tercero_id: Mapped[int | None] = mapped_column(ForeignKey("terceros.id"))
    descripcion: Mapped[str | None] = mapped_column(String(250))
    debito: Mapped[Decimal] = mapped_column(Dinero, default=CERO)
    credito: Mapped[Decimal] = mapped_column(Dinero, default=CERO)

    asiento: Mapped[Asiento] = relationship(back_populates="lineas")
    tercero: Mapped[Tercero | None] = relationship()
    cuenta_rel: Mapped[Cuenta] = relationship()


class DocumentoVenta(Base):
    """Factura de venta (FV), nota crédito (NC) o nota débito (ND) emitida."""
    __tablename__ = "documentos_venta"
    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(String(2), default="FV")
    numero: Mapped[str] = mapped_column(String(40))
    cufe: Mapped[str | None] = mapped_column(String(120), unique=True)
    fecha: Mapped[date] = mapped_column(Date, index=True)
    vencimiento: Mapped[date | None] = mapped_column(Date)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("terceros.id"))
    subtotal: Mapped[Decimal] = mapped_column(Dinero, default=CERO)  # antes de IVA y descuentos
    descuentos: Mapped[Decimal] = mapped_column(Dinero, default=CERO)
    base_gravada: Mapped[Decimal] = mapped_column(Dinero, default=CERO)
    iva: Mapped[Decimal] = mapped_column(Dinero, default=CERO)
    total: Mapped[Decimal] = mapped_column(Dinero, default=CERO)  # valor a pagar de la factura
    referencia_id: Mapped[int | None] = mapped_column(ForeignKey("documentos_venta.id"))  # NC/ND -> factura
    reteiva_aplica: Mapped[bool] = mapped_column(Boolean, default=False)
    reteiva_valor: Mapped[Decimal] = mapped_column(Dinero, default=CERO)
    reteiva_fecha: Mapped[date | None] = mapped_column(Date)
    cert_recibido: Mapped[bool] = mapped_column(Boolean, default=False)
    cert_archivo: Mapped[str | None] = mapped_column(String(250))
    xml_archivo: Mapped[str | None] = mapped_column(String(250))
    pdf_archivo: Mapped[str | None] = mapped_column(String(250))
    notas: Mapped[str | None] = mapped_column(Text)
    anulada: Mapped[bool] = mapped_column(Boolean, default=False)
    creado: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    cliente: Mapped[Tercero] = relationship()
    referencia: Mapped["DocumentoVenta | None"] = relationship(remote_side=[id])
    lineas: Mapped[list["LineaVenta"]] = relationship(cascade="all, delete-orphan")
    aplicaciones: Mapped[list["AplicacionRecaudo"]] = relationship(back_populates="documento")

    @property
    def signo(self):
        return -1 if self.tipo == "NC" else 1

    @hybrid_property
    def ingreso(self):
        return self.total - self.iva


class LineaVenta(Base):
    __tablename__ = "lineas_venta"
    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[int] = mapped_column(ForeignKey("documentos_venta.id", ondelete="CASCADE"))
    descripcion: Mapped[str] = mapped_column(Text)
    cantidad: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal(1))
    base: Mapped[Decimal] = mapped_column(Dinero, default=CERO)
    iva_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=CERO)
    iva: Mapped[Decimal] = mapped_column(Dinero, default=CERO)


class Recaudo(Base):
    """Pago recibido de un cliente; se aplica a una o varias facturas."""
    __tablename__ = "recaudos"
    id: Mapped[int] = mapped_column(primary_key=True)
    fecha: Mapped[date] = mapped_column(Date, index=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("terceros.id"))
    banco_id: Mapped[int] = mapped_column(ForeignKey("bancos.id"))
    valor: Mapped[Decimal] = mapped_column(Dinero)
    medio_electronico: Mapped[bool] = mapped_column(Boolean, default=False)  # tarjeta/pasarela (art. 912 E.T.)
    referencia: Mapped[str | None] = mapped_column(String(80))
    notas: Mapped[str | None] = mapped_column(Text)
    soporte_archivo: Mapped[str | None] = mapped_column(String(250))

    cliente: Mapped[Tercero] = relationship()
    banco: Mapped[Banco] = relationship()
    aplicaciones: Mapped[list["AplicacionRecaudo"]] = relationship(
        back_populates="recaudo", cascade="all, delete-orphan")

    @property
    def aplicado(self):
        return sum((a.valor for a in self.aplicaciones), CERO)

    @property
    def sin_aplicar(self):
        return self.valor - self.aplicado


class AplicacionRecaudo(Base):
    __tablename__ = "aplicaciones_recaudo"
    id: Mapped[int] = mapped_column(primary_key=True)
    recaudo_id: Mapped[int] = mapped_column(ForeignKey("recaudos.id", ondelete="CASCADE"))
    documento_id: Mapped[int] = mapped_column(ForeignKey("documentos_venta.id"))
    valor: Mapped[Decimal] = mapped_column(Dinero)

    recaudo: Mapped[Recaudo] = relationship(back_populates="aplicaciones")
    documento: Mapped[DocumentoVenta] = relationship(back_populates="aplicaciones")


TIPOS_SOPORTE = {
    "FE": "Factura electrónica",
    "NC": "Nota crédito de proveedor",
    "DS": "Documento soporte",
    "CC": "Cuenta de cobro",
    "RE": "Recibo / tiquete",
    "OT": "Otro",
}


class Gasto(Base):
    __tablename__ = "gastos"
    id: Mapped[int] = mapped_column(primary_key=True)
    tipo_soporte: Mapped[str] = mapped_column(String(2), default="FE")
    numero: Mapped[str | None] = mapped_column(String(60))
    cufe: Mapped[str | None] = mapped_column(String(120), unique=True)
    fecha: Mapped[date] = mapped_column(Date, index=True)
    vencimiento: Mapped[date | None] = mapped_column(Date)
    proveedor_id: Mapped[int | None] = mapped_column(ForeignKey("terceros.id"))
    categoria_id: Mapped[int] = mapped_column(ForeignKey("categorias_gasto.id"))
    descripcion: Mapped[str | None] = mapped_column(Text)
    subtotal: Mapped[Decimal] = mapped_column(Dinero, default=CERO)  # base antes de impuestos
    iva: Mapped[Decimal] = mapped_column(Dinero, default=CERO)
    iva_descontable: Mapped[bool] = mapped_column(Boolean, default=True)
    otros_impuestos: Mapped[Decimal] = mapped_column(Dinero, default=CERO)  # INC, etc. (mayor valor del gasto)
    total: Mapped[Decimal] = mapped_column(Dinero, default=CERO)
    forma_pago: Mapped[str] = mapped_column(String(10), default="contado")  # contado | credito
    cuenta_pago: Mapped[str | None] = mapped_column(ForeignKey("cuentas.codigo"))  # para contado
    xml_archivo: Mapped[str | None] = mapped_column(String(250))
    soporte_archivo: Mapped[str | None] = mapped_column(String(250))
    origen: Mapped[str] = mapped_column(String(10), default="manual")  # manual | xml | correo | carpeta | banco
    revisado: Mapped[bool] = mapped_column(Boolean, default=True)
    recurrente: Mapped[bool] = mapped_column(Boolean, default=False)  # se espera cada mes (arriendo, internet…)
    vida_util_meses: Mapped[int | None] = mapped_column(Integer)  # solo activos fijos (cuentas 15xx)
    notas: Mapped[str | None] = mapped_column(Text)
    creado: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    proveedor: Mapped[Tercero | None] = relationship()
    categoria: Mapped[CategoriaGasto] = relationship()
    referencia_id: Mapped[int | None] = mapped_column(ForeignKey("gastos.id"))  # NC de proveedor -> factura
    pagos: Mapped[list["PagoGasto"]] = relationship(back_populates="gasto", cascade="all, delete-orphan")
    referencia: Mapped["Gasto | None"] = relationship(remote_side="Gasto.id", foreign_keys=[referencia_id],
                                                      back_populates="notas_credito")
    notas_credito: Mapped[list["Gasto"]] = relationship(foreign_keys=[referencia_id], back_populates="referencia")

    @property
    def signo(self):
        return -1 if self.tipo_soporte == "NC" else 1

    @property
    def iva_desc_valor(self):
        return self.iva if self.iva_descontable else CERO

    @property
    def pagado(self):
        if self.forma_pago == "contado" or self.tipo_soporte == "NC":
            return self.total
        return sum((p.valor for p in self.pagos), CERO)

    @property
    def saldo(self):
        if self.tipo_soporte == "NC":
            return CERO
        return self.total - self.pagado - sum((n.total for n in self.notas_credito), CERO)


class PagoGasto(Base):
    __tablename__ = "pagos_gasto"
    id: Mapped[int] = mapped_column(primary_key=True)
    gasto_id: Mapped[int] = mapped_column(ForeignKey("gastos.id", ondelete="CASCADE"))
    fecha: Mapped[date] = mapped_column(Date)
    cuenta_pago: Mapped[str] = mapped_column(ForeignKey("cuentas.codigo"))
    valor: Mapped[Decimal] = mapped_column(Dinero)

    gasto: Mapped[Gasto] = relationship(back_populates="pagos")


class PagoImpuesto(Base):
    """Pago de recibo 2593 (anticipo SIMPLE + IVA bimestral) u otra declaración."""
    __tablename__ = "pagos_impuesto"
    id: Mapped[int] = mapped_column(primary_key=True)
    formulario: Mapped[str] = mapped_column(String(6), default="2593")  # 2593 | 260 | 300
    anio: Mapped[int] = mapped_column(Integer)
    bimestre: Mapped[int | None] = mapped_column(Integer)
    fecha: Mapped[date] = mapped_column(Date)
    valor_simple: Mapped[Decimal] = mapped_column(Dinero, default=CERO)
    valor_iva: Mapped[Decimal] = mapped_column(Dinero, default=CERO)
    banco_id: Mapped[int] = mapped_column(ForeignKey("bancos.id"))
    numero_formulario: Mapped[str | None] = mapped_column(String(40))
    archivo: Mapped[str | None] = mapped_column(String(250))

    banco: Mapped[Banco] = relationship()

    @property
    def total(self):
        return self.valor_simple + self.valor_iva


class MovimientoBanco(Base):
    """Línea de un extracto bancario importado, para conciliación."""
    __tablename__ = "movimientos_banco"
    __table_args__ = (UniqueConstraint("banco_id", "huella"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    banco_id: Mapped[int] = mapped_column(ForeignKey("bancos.id"))
    fecha: Mapped[date] = mapped_column(Date, index=True)
    descripcion: Mapped[str] = mapped_column(String(250))
    valor: Mapped[Decimal] = mapped_column(Dinero)  # positivo entra, negativo sale
    referencia: Mapped[str | None] = mapped_column(String(80))
    huella: Mapped[str] = mapped_column(String(64))  # evita importar dos veces la misma línea
    estado: Mapped[str] = mapped_column(String(12), default="pendiente")  # pendiente | conciliado | ignorado
    origen_tipo: Mapped[str | None] = mapped_column(String(12))  # recaudo | pagogasto | gasto | impuesto | asiento
    origen_id: Mapped[int | None] = mapped_column(Integer)
    creado_aqui: Mapped[bool] = mapped_column(Boolean, default=False)  # el documento vinculado lo creó la conciliación
    importado: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    banco: Mapped[Banco] = relationship()


class CorreoProcesado(Base):
    __tablename__ = "correos_procesados"
    id: Mapped[int] = mapped_column(primary_key=True)
    mensaje_id: Mapped[str] = mapped_column(String(250), unique=True)
    fecha: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    asunto: Mapped[str | None] = mapped_column(String(250))
    resultado: Mapped[str | None] = mapped_column(Text)


class Bitacora(Base):
    """Historial de cambios: qué se creó, modificó o eliminó y cuándo."""
    __tablename__ = "bitacora"
    id: Mapped[int] = mapped_column(primary_key=True)
    fecha: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    accion: Mapped[str] = mapped_column(String(10))  # crear | editar | borrar
    entidad: Mapped[str] = mapped_column(String(30))
    entidad_id: Mapped[int | None] = mapped_column(Integer)
    descripcion: Mapped[str] = mapped_column(String(300))
    detalle: Mapped[str | None] = mapped_column(Text)


class Vencimiento(Base):
    __tablename__ = "vencimientos"
    id: Mapped[int] = mapped_column(primary_key=True)
    obligacion: Mapped[str] = mapped_column(String(120))
    periodo: Mapped[str | None] = mapped_column(String(60))
    fecha: Mapped[date] = mapped_column(Date)
    cumplido: Mapped[bool] = mapped_column(Boolean, default=False)
    notas: Mapped[str | None] = mapped_column(Text)
