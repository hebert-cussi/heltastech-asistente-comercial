"""Prueba cada herramienta SIN el modelo (Sesión 2: si falla aquí, fallará dentro del agente).

Ejecutar desde la carpeta del proyecto:
    python src/probar_herramientas.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from herramientas import buscar_plan, cotizar, registrar_cotizacion, tipo_de_cambio  # noqa: E402

fallas = 0


def revisar(nombre: str, salida: str, debe_ser_error: bool) -> None:
    global fallas
    es_error = salida.startswith("ERROR")
    ok = es_error == debe_ser_error
    fallas += not ok
    print(f"[{'OK ' if ok else 'FALLA'}] {nombre}\n       {salida[:200]}\n")


revisar("buscar_plan: zoom business", buscar_plan("zoom business"), False)
revisar("buscar_plan: odoo facturación (con tilde)", buscar_plan("odoo facturación"), False)
revisar("buscar_plan: producto que no vendemos", buscar_plan("microsoft 365"), True)

revisar("cotizar: 10 Zoom Business anual (10% automático)", cotizar("ZOOM-BUSINESS", 10, "anual"), False)
revisar("cotizar: 5 Odoo Standard mensual", cotizar("odoo-standard", 5, "mensual"), False)
revisar("cotizar: plan inexistente", cotizar("ZOOM-ULTRA", 3), True)
revisar("cotizar: rebaja 30% (regla de negocio)", cotizar("ZOOM-PRO", 2, "anual", 30), True)
revisar("cotizar: Enterprise a medida", cotizar("ZOOM-ENTERPRISE", 50), True)
revisar("cotizar: cantidad 0", cotizar("ZOOM-PRO", 0), True)
revisar("cotizar: periodo inválido", cotizar("ZOOM-PRO", 2, "semanal"), True)

revisar("tipo_de_cambio: 100 USD a BOB (API real)", tipo_de_cambio(100, "USD", "BOB"), False)
revisar("tipo_de_cambio: moneda inválida", tipo_de_cambio(100, "USD", "XYZ"), True)

total = json.loads(cotizar("ZOOM-BUSINESS", 10, "anual"))["total_periodo_usd"]
revisar("registrar_cotizacion: total correcto", registrar_cotizacion("Constructora Andina SRL", "ZOOM-BUSINESS", 10, "anual", total), False)
revisar("registrar_cotizacion: total manipulado", registrar_cotizacion("Constructora Andina SRL", "ZOOM-BUSINESS", 10, "anual", 1.0), True)
revisar("registrar_cotizacion: sin empresa", registrar_cotizacion("", "ZOOM-PRO", 1, "anual", 0), True)

print(f"Resultado: {'todas las pruebas pasaron' if not fallas else f'{fallas} prueba(s) fallaron'}")
sys.exit(1 if fallas else 0)
