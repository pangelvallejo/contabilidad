# Seguimiento de plugins de referencia

Plugins habilitados en la cuenta de Claude del usuario que sirven de referencia para mejorar el programa.
Una rutina automática los revisa cada semana: si alguno es nuevo o cambió, se analiza frente al programa y se
abre un issue en GitHub con recomendaciones; este archivo guarda lo último revisado para no repetir trabajo.

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
