"""Herramientas del Asistente Comercial de Heltastech (Zoom / Odoo).

Funciones Python normales: se prueban solas y luego se convierten en herramientas
de LangChain con `tool(funcion)`. El docstring de cada una es lo que lee el modelo.

Reglas de diseño (Sesión 2):
- Nunca lanzan errores hacia afuera: devuelven un texto que empieza con "ERROR:".
- Las reglas de negocio (rebaja máxima, planes válidos) viven aquí, en código, no en el prompt.
- La API externa tiene timeout y devuelve solo los campos necesarios.

DATOS SIMULADOS: los precios del catálogo son valores de referencia para el prototipo.
Antes de la demo, verificarlos en zoom.us/pricing y odoo.com/pricing y actualizar FECHA_PRECIOS.
"""

from __future__ import annotations

import json
import unicodedata
from datetime import date

import requests

# ---------------------------------------------------------------------------
# Configuración del negocio
# ---------------------------------------------------------------------------

FECHA_PRECIOS = "2026-11-08"  # fecha en que se tomaron los precios de referencia

# Rebaja de partner sobre el precio de lista público, por producto.
# Zoom: 10% (dato del negocio). Odoo: PENDIENTE de confirmar con el equipo.
REBAJA_PARTNER_PCT = {"zoom": 10.0, "odoo": 0.0}

PERIODOS = ("mensual", "anual")

# Precio por licencia y por mes, en USD. "anual" = precio mensual equivalente pagando el año.
# precio None = el plan se cotiza a medida: lo atiende un ejecutivo, no el agente.
CATALOGO: dict[str, dict] = {
    "ZOOM-PRO": {
        "producto": "zoom", "nombre": "Zoom Workplace Pro",
        "descripcion": "Reuniones de hasta 30 horas y 100 participantes, grabación en la nube.",
        "precio_usd": {"mensual": 15.99, "anual": 13.33}, "min_licencias": 1,
    },
    "ZOOM-BUSINESS": {
        "producto": "zoom", "nombre": "Zoom Workplace Business",
        "descripcion": "Reuniones de hasta 300 participantes, SSO, dominios administrados.",
        "precio_usd": {"mensual": 21.99, "anual": 18.33}, "min_licencias": 1,
    },
    "ZOOM-BUSINESS-PLUS": {
        "producto": "zoom", "nombre": "Zoom Workplace Business Plus",
        "descripcion": "Business + telefonía Zoom Phone y traducción de subtítulos.",
        "precio_usd": {"mensual": 26.99, "anual": 22.49}, "min_licencias": 1,
    },
    "ZOOM-WEBINARS-500": {
        "producto": "zoom", "nombre": "Zoom Webinars (500 asistentes)",
        "descripcion": "Complemento para seminarios web de hasta 500 asistentes.",
        "precio_usd": {"mensual": 79.00, "anual": 57.42}, "min_licencias": 1,
    },
    "ZOOM-ENTERPRISE": {
        "producto": "zoom", "nombre": "Zoom Workplace Enterprise",
        "descripcion": "Para grandes organizaciones. Precio a medida.",
        "precio_usd": None, "min_licencias": 1,
    },
    "ODOO-STANDARD": {
        "producto": "odoo", "nombre": "Odoo Standard (todas las apps, en la nube)",
        "descripcion": "Facturación, inventario, ventas, CRM, contabilidad y más. Por usuario.",
        "precio_usd": {"mensual": 9.10, "anual": 7.25}, "min_licencias": 1,
    },
    "ODOO-CUSTOM": {
        "producto": "odoo", "nombre": "Odoo Custom (Studio, multiempresa, API externa)",
        "descripcion": "Standard + Odoo Studio, multiempresa y acceso a la API externa. Por usuario.",
        "precio_usd": {"mensual": 13.60, "anual": 10.90}, "min_licencias": 1,
    },
}

# Cotizaciones registradas (simula el sistema de la empresa).
COTIZACIONES: dict[str, dict] = {}


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFD", str(texto).lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


# ---------------------------------------------------------------------------
# Herramienta 1 · buscar_plan (lectura, catálogo simulado)
# ---------------------------------------------------------------------------

def buscar_plan(consulta: str) -> str:
    """Busca planes de Zoom u Odoo en el catálogo de Heltastech por palabras clave
    (por ejemplo 'zoom business', 'webinars', 'odoo facturacion').
    Devuelve id del plan, producto, nombre, descripción y precio de lista en USD por licencia y mes.
    Úsala SIEMPRE antes de cotizar: nunca inventes planes ni precios."""
    palabras = [p for p in _normalizar(consulta).split() if len(p) >= 3]
    if not palabras:
        return "ERROR: la consulta está vacía. Indica el producto (Zoom u Odoo) o una necesidad."

    hallados = []
    for plan_id, plan in CATALOGO.items():
        texto = _normalizar(f"{plan_id} {plan['producto']} {plan['nombre']} {plan['descripcion']}")
        puntaje = sum(1 for p in palabras if p[:6] in texto)
        if puntaje:
            hallados.append((puntaje, plan_id, plan))

    if not hallados:
        return (f"ERROR: no hay planes que coincidan con '{consulta}'. "
                "Heltastech vende suscripciones de Zoom y de Odoo.")

    hallados.sort(key=lambda x: -x[0])
    resultado = [
        {
            "plan_id": plan_id,
            "producto": plan["producto"],
            "nombre": plan["nombre"],
            "descripcion": plan["descripcion"],
            "precio_lista_usd": plan["precio_usd"] or "a medida (requiere ejecutivo)",
        }
        for _, plan_id, plan in hallados[:5]
    ]
    return json.dumps(resultado, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Herramienta 2 · cotizar (lectura, regla de negocio en código)
# ---------------------------------------------------------------------------

def cotizar(plan_id: str, cantidad: int, periodo: str = "anual", rebaja_pct: float | None = None) -> str:
    """Cotiza una línea: un plan, una cantidad de licencias y un periodo de pago ('mensual' o 'anual').
    Aplica automáticamente la rebaja de partner de Heltastech (Zoom: 10%).
    'rebaja_pct' solo se usa si el cliente pide una rebaja específica; si supera la máxima, devuelve ERROR.
    Devuelve precio de lista, rebaja aplicada y total en USD (por mes y por el periodo completo)."""
    plan_id = str(plan_id).upper().strip()
    if plan_id not in CATALOGO:
        return f"ERROR: el plan '{plan_id}' no existe. Usa buscar_plan para ver los planes disponibles."

    plan = CATALOGO[plan_id]
    if plan["precio_usd"] is None:
        return (f"ERROR: {plan['nombre']} se cotiza a medida. "
                "Ofrece al cliente agendar una llamada con un ejecutivo de Heltastech.")

    try:
        cantidad = int(cantidad)
    except (TypeError, ValueError):
        return "ERROR: la cantidad debe ser un número entero de licencias."
    if cantidad < plan["min_licencias"]:
        return f"ERROR: la cantidad mínima para {plan['nombre']} es {plan['min_licencias']} licencia(s)."

    periodo = _normalizar(periodo).strip()
    if periodo not in PERIODOS:
        return "ERROR: el periodo debe ser 'mensual' o 'anual'."

    maxima = REBAJA_PARTNER_PCT[plan["producto"]]
    if rebaja_pct is None:
        rebaja = maxima
    else:
        try:
            rebaja = float(rebaja_pct)
        except (TypeError, ValueError):
            return "ERROR: la rebaja debe ser un número (porcentaje)."
        if rebaja < 0:
            return "ERROR: la rebaja no puede ser negativa."
        if rebaja > maxima:
            # Regla de negocio: el prompt orienta, el código garantiza.
            return (f"ERROR: la rebaja máxima para {plan['producto'].capitalize()} es {maxima:g}%. "
                    f"No se puede aplicar {rebaja:g}%.")

    precio_lista = plan["precio_usd"][periodo]
    precio_final = round(precio_lista * (1 - rebaja / 100), 2)
    meses = 12 if periodo == "anual" else 1
    total_mes = round(precio_final * cantidad, 2)

    return json.dumps({
        "plan_id": plan_id,
        "nombre": plan["nombre"],
        "cantidad": cantidad,
        "periodo": periodo,
        "precio_lista_usd": precio_lista,
        "rebaja_pct": rebaja,
        "precio_final_usd": precio_final,
        "total_mensual_usd": total_mes,
        "total_periodo_usd": round(total_mes * meses, 2),
        "meses_facturados": meses,
        "precios_de_referencia_al": FECHA_PRECIOS,
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Herramienta 3 · tipo_de_cambio (lectura, API REAL)
# ---------------------------------------------------------------------------

_http = requests.Session()
_http.headers["User-Agent"] = "Heltastech-Asistente-Comercial/0.1 (proyecto academico)"


def tipo_de_cambio(monto: float, desde: str = "USD", hacia: str = "BOB") -> str:
    """Convierte un monto entre monedas con la tasa del día (API pública ExchangeRate-API).
    Usa códigos ISO de 3 letras: USD, BOB (bolivianos), EUR, PEN...
    Úsala cuando el cliente pida el total en bolivianos u otra moneda; no hagas la conversión mentalmente."""
    desde, hacia = str(desde).upper().strip(), str(hacia).upper().strip()
    try:
        monto = float(monto)
    except (TypeError, ValueError):
        return "ERROR: el monto debe ser un número."
    try:
        respuesta = _http.get(f"https://open.er-api.com/v6/latest/{desde}", timeout=10)
        respuesta.raise_for_status()
        datos = respuesta.json()
    except requests.RequestException as error:
        return f"ERROR: el servicio de tipo de cambio no respondió ({type(error).__name__}). Informa el total en USD."

    if datos.get("result") != "success":
        return f"ERROR: la moneda '{desde}' no es válida."
    if hacia not in datos.get("rates", {}):
        return f"ERROR: la moneda '{hacia}' no es válida."

    tasa = datos["rates"][hacia]
    return json.dumps({
        "monto": monto, "desde": desde, "hacia": hacia, "tasa": tasa,
        "resultado": round(monto * tasa, 2),
        "actualizado": datos.get("time_last_update_utc", ""),
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Herramienta 4 · registrar_cotizacion (ESCRITURA: requiere aprobación humana)
# ---------------------------------------------------------------------------

def registrar_cotizacion(empresa: str, plan_id: str, cantidad: int, periodo: str, total_periodo_usd: float) -> str:
    """Registra una cotización formal en el sistema de Heltastech y devuelve su número.
    Úsala SOLO cuando el cliente confirme que quiere la cotización formal.
    Es una acción de escritura: un ejecutivo comercial debe aprobarla."""
    if not str(empresa).strip():
        return "ERROR: falta el nombre de la empresa del cliente."
    plan_id = str(plan_id).upper().strip()
    if plan_id not in CATALOGO:
        return f"ERROR: el plan '{plan_id}' no existe."

    # Se recalcula en código: no confiamos en el total que manda el modelo.
    calculo = cotizar(plan_id, cantidad, periodo)
    if calculo.startswith("ERROR"):
        return calculo
    esperado = json.loads(calculo)["total_periodo_usd"]
    try:
        total_modelo = float(total_periodo_usd)
    except (TypeError, ValueError):
        total_modelo = None
    if total_modelo is None or abs(total_modelo - esperado) > 0.01:
        return (f"ERROR: el total enviado ({total_periodo_usd}) no coincide con el calculado "
                f"({esperado}). Vuelve a cotizar con la herramienta cotizar.")

    numero = f"COT-{date.today().year}-{len(COTIZACIONES) + 1:04d}"
    COTIZACIONES[numero] = {
        "empresa": empresa.strip(), "plan_id": plan_id, "cantidad": int(cantidad),
        "periodo": _normalizar(periodo).strip(), "total_periodo_usd": esperado,
        "fecha": date.today().isoformat(),
    }
    return json.dumps({"numero_cotizacion": numero, **COTIZACIONES[numero]}, ensure_ascii=False)


HERRAMIENTAS = [buscar_plan, cotizar, tipo_de_cambio, registrar_cotizacion]
