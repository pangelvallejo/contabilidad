# Seguimiento de plugins de referencia

Plugins habilitados en la cuenta de Claude del usuario que sirven de referencia para mejorar el programa.
La idea es revisarlos cada semana: si alguno es nuevo o cambió, se analiza frente al programa y se deja el
análisis en `docs/revisiones_plugins/<fecha>.md`; este archivo guarda lo último revisado para no repetir trabajo.

**Estado (octubre de 2026):** los plugins habilitados en claude.ai (Accountable, Finance, Small Business) no se
sincronizan a las sesiones de Claude Code en la nube: ni la sesión de desarrollo ni la rutina programada los ven
(`ListPlugins` devuelve vacío y no hay archivos de skills en el contenedor). Mientras eso no cambie, la revisión
se hace así:

1. En claude.ai (chat), con los tres plugins activos, pedir: *"Describe en detalle qué skills, informes, flujos de
   trabajo y reglas contables incluye el plugin <nombre>; copia el contenido de sus instrucciones"*. Guardar la
   respuesta en `docs/plugins/<nombre>.md`.
2. En una sesión de Claude Code sobre este repositorio pedir la revisión con el texto de la sección
   "Instrucción para la revisión" de abajo. El resultado queda en `docs/revisiones_plugins/<fecha>.md` y en la tabla.
3. Cuando el directorio de plugins muestre una actualización (etiqueta "New" o cambio de versión), repetir 1 y 2.

Alternativa: crear la rutina semanal desde claude.ai → Rutinas, adjuntando los tres plugins, con la misma
instrucción; si en el futuro las sesiones programadas reciben los plugins, la rutina los leerá sola.

| Plugin | Origen | Última versión o huella revisada | Fecha de revisión | Issue |
|---|---|---|---|---|
| Accountable | Anthropic Directory | pendiente (primera revisión) | — | — |
| Finance | Anthropic | pendiente (primera revisión) | — | — |
| Small Business | Anthropic | pendiente (primera revisión) | — | — |

## Procedimiento de cada revisión

1. Obtener los plugins habilitados (herramienta ListPlugins y ListSkills) y leer el contenido de sus skills.
2. Calcular una huella (sha256 del contenido concatenado de los skills de cada plugin) y compararla con esta tabla.
3. Para cada plugin nuevo o cambiado: comparar sus capacidades con el programa (README.md, app/) y escribir
   recomendaciones concretas, priorizadas y aplicables a una firma de abogados en el régimen SIMPLE.
4. Abrir un issue "Revisión de plugins <fecha>" con el análisis; no cambiar código desde la rutina.
5. Actualizar esta tabla y abrir un PR solo con este archivo.

## Recomendaciones ya evaluadas

(Se llena en cada revisión: qué se adoptó, qué se descartó y por qué.)

## Instrucción para la revisión

> Eres el revisor de plugins del programa de contabilidad de Angel Lecompte S.A.S. (firma de abogados en Bogotá,
> régimen SIMPLE, responsable de IVA). Lee los plugins disponibles (herramientas ListPlugins y ListSkills, o los
> archivos de `docs/plugins/`) y `docs/plugins_seguimiento.md`. Calcula una huella sha256 del contenido de cada
> plugin y compárala con la tabla. Para cada plugin nuevo o cambiado, compara sus capacidades con el programa
> (README.md, app/, app/templates/) y escribe recomendaciones concretas y priorizadas: qué trae el plugin, si aplica
> a una firma de abogados colombiana en el SIMPLE (sin empleados, factura en el software gratuito de la DIAN), cómo
> se implementaría y qué valor aporta; descarta con una línea lo que no aplique. Guarda el análisis en
> `docs/revisiones_plugins/<AAAA-MM-DD>.md`, actualiza la tabla y la sección "Recomendaciones ya evaluadas", y
> abre un pull request solo con esos archivos. No modifiques código del programa desde la revisión.
