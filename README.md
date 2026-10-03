# Contabilidad Angel Lecompte S.A.S.

Programa local de contabilidad para una firma de abogados en el **Régimen Simple de Tributación**,
responsable de IVA. Corre en el computador (Windows) y se usa desde el navegador; los datos no salen
del equipo, salvo las copias de seguridad en OneDrive.

## Qué hace

| Módulo | Funciones |
|---|---|
| **Facturas de venta** | Importa el XML (o ZIP con XML y PDF) del sistema gratuito de facturación de la DIAN: CUFE, cliente, base, IVA y total. Crea el cliente automáticamente. Facturas, notas crédito y notas débito. Registro manual si no hay XML. |
| **ReteIVA 15%** | Por factura se marca si el cliente practicó o no la retención (si viene en el XML se toma de ahí). Se puede recordar por cliente. Control de certificados con adjunto y alerta de pendientes. |
| **Recaudos y cartera** | Pagos totales, abonos parciales y un pago aplicado a varias facturas (reparto automático por antigüedad). Cartera por edades, estado de cuenta en PDF por cliente. Marca de pagos con tarjeta/pasarela para el descuento del art. 912 E.T. |
| **Gastos** | Importa XML/ZIP de proveedores y propone la categoría (aprende de la última usada por el proveedor). Registro manual con foto o PDF del soporte. IVA descontable o no, contado o crédito, pagos a proveedores, reembolsos al socio. |
| **IVA** | Liquidación por bimestre: IVA generado − IVA descontable − reteIVA, con detalle exportable. |
| **SIMPLE** | Recibo 2593 por bimestre (anticipo SIMPLE + IVA) con las tarifas de actividades profesionales y de consultoría, registro de pagos, borrador de la declaración anual (F260) con descuento por medios electrónicos y causación del impuesto. Borrador de la declaración anual de IVA (F300). |
| **Calendario** | Vencimientos 2026 para NIT terminado en 7 (editable) con alertas 30 días antes. |
| **Exógena** | Formatos 1001, 1005, 1006, 1007, 1008 y 1009 en Excel, con aviso de terceros a los que les faltan datos. |
| **Contabilidad** | Partida doble con PUC: asientos automáticos por cada documento, asientos manuales, libro diario, libro mayor, balance de prueba, estado de resultados y balance general. Exportación a Excel. |
| **Respaldo** | Copia automática diaria (base de datos y soportes) a OneDrive; se conservan las últimas 30. |

## Instalación en Windows

1. Instale Python 3.11 o superior desde <https://www.python.org/downloads/>. En el instalador, marque
   **“Add python.exe to PATH”**.
2. Descargue esta carpeta (botón *Code → Download ZIP* en GitHub) y descomprímala, por ejemplo en
   `Documentos\Contabilidad`.
3. Doble clic en **`iniciar.bat`**. La primera vez instala lo necesario (1–2 minutos); después abre el
   programa en el navegador en `http://127.0.0.1:5050`.
4. Opcional: clic derecho en `crear_acceso_directo.ps1` → *Ejecutar con PowerShell* para tener un
   acceso directo en el escritorio.

Mientras use el programa, deje abierta la ventana negra; ciérrela para salir.

### Dónde quedan los datos

En `C:\Users\<usuario>\ContabilidadAngelLecompte\` (base de datos `contabilidad.db` y carpeta
`adjuntos`). Esa carpeta es independiente del código: puede actualizar el programa sin perder datos.
Los respaldos van a `OneDrive\Respaldos Contabilidad Angel Lecompte\` (configurable). Para restaurar,
descomprima un respaldo en la carpeta de datos con el programa cerrado.

### Actualizar

Reemplace los archivos del programa por la versión nueva y vuelva a abrir `iniciar.bat`. Las tablas y
campos nuevos se agregan solos a la base de datos existente.

## Uso diario

1. **Emitió una factura en la DIAN** → *Importar XML de facturas* y suba el ZIP o el XML. Revise si el
   cliente le practicó reteIVA.
2. **Le pagaron** → *Recaudos → Registrar recaudo*: elija el cliente, el banco y el valor; reparta entre
   las facturas.
3. **Le llegó una factura de un proveedor** → *Importar XML de proveedores*. Revise la categoría de los
   marcados “revisar”.
4. **Gasto sin factura electrónica** (taxi, parqueadero, cuenta de cobro) → *Registrar gasto* y adjunte
   la foto.
5. **Antes del vencimiento del 2593** → *Recibo 2593*: copie los valores al portal de la DIAN, pague y
   registre el pago.
6. **Cierre del año** → *Declaración SIMPLE (F260)* y *Declaración IVA (F300)*; *Exógena*.

## Parámetros tributarios

Están en `app/config.py` y se actualizan con nuevas versiones del programa:

- UVT 2026: $52.374.
- SIMPLE, actividades profesionales y de consultoría (art. 908 E.T., Ley 2277 de 2022): tarifas anuales
  5,9% / 7,3% / 12% / 14,5% y bimestrales 5,9% hasta 1.000 UVT, 7,3% hasta 2.500, 12% hasta 5.000 y
  14,5% hasta 16.666. Límite de 12.000 UVT para profesiones liberales.
- ReteIVA: 15% del IVA. IVA: 19%.
- Base de ingresos del SIMPLE: por causación (fecha de factura) o por caja (fecha de recaudo), según
  *Configuración*.

Los borradores de F260, F300 y exógena se presentan por conceptos; verifíquelos contra los formularios
y resoluciones vigentes antes de declarar.

## Desarrollo

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest          # pruebas
.venv/bin/python -m app             # abre la app (datos en ~/ContabilidadAngelLecompte o $CONTA_DATOS)
```

Estructura: `app/dian_xml.py` (lector UBL), `app/importacion.py` (XML → venta o gasto),
`app/contab.py` (asientos automáticos), `app/impuestos.py` (IVA, 2593, F260, F300), `app/reportes.py`
(libros y estados), `app/exogena.py`, `app/respaldo.py`, `app/routes/` (pantallas).

## Pendiente (siguientes fases)

- Lectura automática del buzón de Gmail donde lleguen las facturas de proveedores.
- Conciliación bancaria con extractos de Bold y Banco Caja Social.
