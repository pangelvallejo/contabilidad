# Contabilidad Angel Lecompte S.A.S.

Programa local de contabilidad para una firma de abogados en el **Régimen Simple de Tributación**,
responsable de IVA. Corre en el computador (Windows) y se usa desde el navegador; los datos no salen
del equipo, salvo las copias de seguridad en OneDrive.

## Qué hace

| Módulo | Funciones |
|---|---|
| **Facturas de venta** | Importa el XML (o ZIP con XML y PDF) del sistema gratuito de facturación de la DIAN: CUFE, cliente, base, IVA y total. Crea el cliente automáticamente. Facturas, notas crédito y notas débito. Registro manual si no hay XML. |
| **ReteIVA 15%** | Por factura se marca si el cliente practicó o no la retención (si viene en el XML se toma de ahí). Se puede recordar por cliente. Control de certificados con adjunto y alerta de pendientes. |
| **Recaudos y cartera** | Pagos totales, abonos parciales y un pago aplicado a varias facturas (reparto automático por antigüedad); un anticipo se aplica después editando el recaudo. Cartera por edades, estado de cuenta en PDF por cliente. Marca de pagos con tarjeta/pasarela para el descuento del art. 912 E.T. |
| **Gastos** | Importa XML/ZIP de proveedores y propone la categoría (aprende de la última usada por el proveedor). Registro manual con foto o PDF del soporte. IVA descontable o no, contado o crédito, pagos a proveedores, reembolsos al socio. |
| **IVA** | Liquidación por bimestre: IVA generado − IVA descontable − reteIVA, con detalle exportable. |
| **SIMPLE** | Recibo 2593 por bimestre (anticipo SIMPLE + IVA) con las tarifas de actividades profesionales y de consultoría, registro de pagos, borrador de la declaración anual (F260) con descuento por medios electrónicos y causación del impuesto. Borrador de la declaración anual de IVA (F300). |
| **Calendario** | Vencimientos 2026 para NIT terminado en 7 (editable) con alertas 30 días antes. |
| **Exógena** | Formatos 1001, 1005, 1006, 1007, 1008 y 1009 en Excel, con aviso de terceros a los que les faltan datos. |
| **Contabilidad** | Partida doble con PUC: asientos automáticos por cada documento, asientos manuales, libro diario, libro mayor, balance de prueba, estado de resultados y balance general. Exportación a Excel. |
| **Respaldo** | Copia automática diaria (base de datos y soportes) a OneDrive; se conservan las últimas 30. Restauración desde el programa. |
| **Entradas automáticas** | Lectura del buzón de correo (IMAP, p. ej. Gmail con contraseña de aplicación) y carpeta vigilada: los XML/ZIP se importan solos. Revisión en lote de lo importado. |
| **Bancos** | Importación de extractos (CSV o Excel de cualquier banco) y conciliación: cruce automático con recaudos y pagos; 4x1000, comisiones y pagos faltantes se registran con un clic. |
| **Planeación** | Flujo de caja proyectado a 6 meses, proyección del impuesto anual, aviso de gastos mensuales no registrados y de clientes con honorarios fijos sin facturar. |
| **Control** | Bloqueo automático de periodos declarados, historial de todos los cambios, búsqueda global, cierre formal del año, depreciación automática de activos. |
| **Documentos** | Estados financieros, recibo de caja y estado de cuenta en PDF; ventas por cliente y por mes; paquete en Excel con todos los libros para el contador. |
| **Instalación** | Paquete portable con Python incluido, actualizaciones con un clic desde GitHub y acceso opcional desde el celular en la red local con contraseña. |

## Instalación en Windows

**Opción A, sin instalar Python (recomendada):** descargue `Contabilidad-<versión>-windows.zip` desde la
página de *Releases* del repositorio, descomprímalo en `Documentos\Contabilidad` y abra
**`Contabilidad.bat`**. El paquete trae su propio Python.

**Opción B, con Python instalado:**
1. Instale Python 3.11 o superior desde <https://www.python.org/downloads/>. En el instalador, marque
   **“Add python.exe to PATH”**.
2. Descargue esta carpeta (botón *Code → Download ZIP* en GitHub) y descomprímala, por ejemplo en
   `Documentos\Contabilidad`.
3. Doble clic en **`iniciar.bat`**. Cada vez que arranca comprueba que las librerías estén instaladas (la primera vez tarda 1–2 minutos); después abre el
   programa en el navegador en `http://127.0.0.1:5050`.

Opcional: clic derecho en `crear_acceso_directo.ps1` → *Ejecutar con PowerShell* para tener un acceso
directo en el escritorio.

Mientras use el programa, deje abierta la ventana negra (puede minimizarla); ciérrela para salir.

### Si Windows bloquea el programa al abrirlo

Si la ventana muestra `DLL load failed ... An Application Control policy has blocked this file`, es el
"Control de aplicaciones inteligente" de Windows 11 (o una política de la empresa) que impide cargar componentes
sin firma de algunas librerías. Desde la versión 1.2.2 el programa lo detecta solo, retira esos componentes y
sigue con su versión en Python puro (la primera vez muestra un aviso). Si usa una versión anterior, actualícela
o borre los archivos `.pyd` de las carpetas `sqlalchemy`, `markupsafe` y `fontTools` dentro de
`.venv\Lib\site-packages` (o `python\Lib\site-packages` en el paquete portable) y vuelva a abrirlo.

Conviene instalar el programa fuera de OneDrive (por ejemplo en `C:\Contabilidad`): OneDrive sincroniza miles
de archivos de las librerías sin necesidad, y los respaldos ya se copian solos a la carpeta que usted elija.

### Dónde quedan los datos

En `C:\Users\<usuario>\ContabilidadAngelLecompte\` (base de datos `contabilidad.db` y carpeta
`adjuntos`). Esa carpeta es independiente del código: puede actualizar el programa sin perder datos.
Los respaldos van a `OneDrive\Respaldos Contabilidad Angel Lecompte\` (configurable). Para restaurar,
descomprima un respaldo en la carpeta de datos con el programa cerrado.

### Actualizar

En *Configuración → Versión y actualizaciones*, **Buscar actualizaciones** consulta la última versión
publicada en GitHub y **Actualizar** hace un respaldo, descarga el paquete, lo aplica y reinicia el
programa. También puede reemplazar los archivos a mano. Las tablas y campos nuevos se agregan solos a la
base de datos existente.

Para publicar una versión nueva (desarrollador): suba la etiqueta `vX.Y.Z`; GitHub Actions corre las
pruebas, construye el paquete portable con Windows y lo adjunta al lanzamiento
(`.github/workflows/paquete.yml`, `herramientas/crear_paquete.py`).

## Uso diario

1. **Emitió una factura en la DIAN** → deje el ZIP en la carpeta vigilada o súbalo en *Importar XML de
   facturas*. Revise si el cliente le practicó reteIVA.
2. **Le pagaron** → en la factura, *Marcar como pagada* (pago total) o *Recaudos → Registrar recaudo*
   para abonos y pagos de varias facturas. Con el extracto del banco, *Conciliación bancaria* lo hace
   por usted.
3. **Facturas de proveedores** → llegan solas desde el correo configurado (o la carpeta vigilada).
   Confírmelas en *Revisar importados*.
4. **Gasto sin factura electrónica** (taxi, parqueadero, cuenta de cobro) → *Registrar gasto* y adjunte
   la foto. Para los que se repiten cada mes, márquelos como recurrentes o use *Duplicar*.
5. **Fin de mes** → importe el extracto en *Conciliación bancaria* y registre con un clic 4x1000 y
   comisiones. Mire *Flujo de caja y proyección*.
6. **Antes del vencimiento del 2593** → *Recibo 2593*: copie los valores al portal de la DIAN, pague y
   registre el pago; el bimestre queda bloqueado contra cambios accidentales.
7. **Cierre del año** → *Declaración SIMPLE (F260)*, *Declaración IVA (F300)*, *Exógena* y
   *Cierre del año*. Entregue al contador el *Paquete para el contador*.

## Parámetros tributarios

Están en `app/config.py` y se actualizan con nuevas versiones del programa:

- UVT 2026: $52.374. La UVT de años siguientes se registra en *Configuración* sin cambiar el programa.
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

## Configuración del correo (facturas de proveedores)

1. Cree o use una cuenta de Gmail para recibir las facturas y active la verificación en dos pasos.
2. En *Cuenta de Google → Seguridad → Contraseñas de aplicaciones*, cree una para "Contabilidad".
3. En *Configuración → Facturas de proveedores por correo*, escriba el correo y esa contraseña, pruebe
   la conexión y guarde. El programa revisa el buzón cada 15 minutos mientras está abierto.
