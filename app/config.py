"""Rutas y parámetros fijos de la aplicación."""
import os
from pathlib import Path

# Carpeta de datos fuera del código, para que actualizar el programa no toque la información.
DATOS_DIR = Path(os.environ.get("CONTA_DATOS", Path.home() / "ContabilidadAngelLecompte"))
DB_PATH = DATOS_DIR / "contabilidad.db"
ADJUNTOS_DIR = DATOS_DIR / "adjuntos"

PUERTO = int(os.environ.get("CONTA_PUERTO", "5050"))

# --- Parámetros tributarios (fijos por decisión del usuario; se actualizan con una nueva versión) ---

# UVT 2026: $52.374 (Resolución DIAN 000238 de 2025).
UVT = {2026: 52374}

# Tarifa de reteIVA que practican los clientes agentes de retención (15% del IVA).
TARIFA_RETEIVA = 0.15
TARIFA_IVA = 0.19

# SIMPLE — art. 908 E.T. numeral 3 (servicios profesionales y de consultoría), según la Ley 2155 de 2021, vigente
# de nuevo por la Sentencia C-540 de 2023 (que declaró inexequibles las tarifas y el tope de 12.000 UVT de la Ley
# 2277 de 2022 para este grupo). Límite general para permanecer en el SIMPLE: 100.000 UVT (art. 905 E.T.).
SIMPLE_TARIFA_ANUAL = [  # (hasta UVT, tarifa consolidada)
    (6000, 0.059),
    (15000, 0.073),
    (30000, 0.12),
    (100000, 0.145),
]
SIMPLE_TARIFA_BIMESTRAL = [  # (hasta UVT de ingresos del bimestre, tarifa)
    (1000, 0.059),
    (2500, 0.073),
    (5000, 0.12),
    (16666, 0.145),
]
SIMPLE_LIMITE_UVT = 100000
# Descuento art. 912 E.T.: 0,5% de los ingresos recibidos por tarjetas y otros medios electrónicos.
SIMPLE_DESCUENTO_MEDIOS_ELECTRONICOS = 0.005

BIMESTRES = {
    1: ("Enero", "Febrero"),
    2: ("Marzo", "Abril"),
    3: ("Mayo", "Junio"),
    4: ("Julio", "Agosto"),
    5: ("Septiembre", "Octubre"),
    6: ("Noviembre", "Diciembre"),
}
