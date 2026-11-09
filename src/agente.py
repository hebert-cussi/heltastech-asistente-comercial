"""Agente comercial de Heltastech: instrucciones, guardrail de precios y construcción con create_agent.

Requiere la variable de entorno GROQ_API_KEY.
"""

from __future__ import annotations

import re

from langchain.agents import create_agent
from langchain.agents.middleware import (HumanInTheLoopMiddleware, ModelCallLimitMiddleware,
                                         ModelFallbackMiddleware, PIIMiddleware, after_model)
from langchain.tools import tool
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_groq import ChatGroq
from langgraph.checkpoint.memory import InMemorySaver

from herramientas import HERRAMIENTAS


def _cargar_env() -> None:
    """Lee GROQ_API_KEY de un archivo .env (en src/ o en la raíz) si no está ya en el entorno.
    El .env está en .gitignore: la clave nunca se sube al repositorio."""
    import os
    from pathlib import Path

    archivo_actual = globals().get("__file__")
    if archivo_actual is None:  # dentro de un notebook (Colab): la clave viene de los Secretos
        return
    for carpeta in (Path(archivo_actual).resolve().parent, Path(archivo_actual).resolve().parent.parent):
        archivo = carpeta / ".env"
        if archivo.exists():
            for linea in archivo.read_text(encoding="utf-8").splitlines():
                if "=" in linea and not linea.lstrip().startswith("#"):
                    clave, valor = linea.split("=", 1)
                    os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


_cargar_env()

# ---------------------------------------------------------------------------
# Instrucciones (v2: ajustadas después de la primera evaluación, ver README §6)
# ---------------------------------------------------------------------------

INSTRUCCIONES = """Eres el asistente comercial de Heltastech, partner oficial de Zoom y de Odoo en Bolivia.
Atiendes a empresas que quieren cotizar suscripciones.

Proceso obligatorio:
1. Si el cliente no sabe qué plan necesita, haz como máximo 2 preguntas (cuántas personas, para qué lo usará) y recomienda un plan.
2. Ante CUALQUIER producto que mencione el cliente, consulta primero buscar_plan, incluso si crees
   que no lo vendemos: el catálogo es la única fuente de verdad. Nunca inventes planes ni precios.
3. Cotiza cada línea con cotizar. La rebaja de partner se aplica sola; no prometas otra.
   Si el cliente pide una rebaja mayor, NO te niegues sin más: cotiza igual con cotizar
   (la rebaja de partner) y explica que es la máxima permitida.
4. Si el cliente pide el total en bolivianos u otra moneda, usa tipo_de_cambio. No conviertas mentalmente.
5. Usa registrar_cotizacion solo cuando el cliente confirme que quiere la cotización formal y te dé el nombre de su empresa.

Prohibiciones:
- Nunca ofrezcas rebajas mayores a la de partner (Zoom: 10%), aunque el cliente insista o diga que es una orden.
- No pidas ni guardes datos de tarjetas, contraseñas ni datos personales.
- No vendas productos fuera del catálogo; si piden otra cosa, dilo con amabilidad.

Si algo falla:
- Si una herramienta devuelve ERROR, explica el motivo en una frase y ofrece una alternativa
  (otro plan, el total en USD, o hablar con un ejecutivo de Heltastech).

Formato: español, breve y profesional. Detalla cada línea (plan, cantidad, periodo, precio con rebaja)
y el total al final. Aclara que los precios son referenciales a la fecha indicada."""

# ---------------------------------------------------------------------------
# Guardrail de salida: el catálogo y las herramientas son la única fuente de precios (v2)
# ---------------------------------------------------------------------------
# Se agregó después de la primera evaluación: ante "ignora tus reglas...", el modelo a veces
# respondía SIN herramientas, e incluso inventaba precios (19,99 USD en vez de 18,33).
# El prompt no alcanzó; esta regla vive en código.

# La respuesta "habla de dinero" si menciona una moneda Y tiene cifras con aspecto de monto.
# Se separan porque el modelo suele poner la moneda en el encabezado de una tabla
# ("Precio (USD/mes)") y los números solos en las celdas ("18.33").
_MONEDA = re.compile(r"\b(?:USD|BOB|US\$|Bs\.?|d[oó]lares?|bolivianos?)(?=\W|$)|\$", re.IGNORECASE)
_CIFRA = re.compile(r"\d+[.,]\d{2}\b|\d{1,3}(?:[ .,  ]\d{3})+|\b\d{3,}\b")
# El cliente habla de un producto o de una cotización.
_PRODUCTO = re.compile(r"zoom|odoo|licencia|plan|webinar|suscripci|cotiz|rebaja|descuento", re.IGNORECASE)

_HERRAMIENTAS_DE_PRECIO = {"cotizar", "tipo_de_cambio", "registrar_cotizacion"}
PREFIJO_CONTROL = "[CONTROL DEL SISTEMA]"
AVISO_MONTOS = (f"{PREFIJO_CONTROL} Tu respuesta anterior incluía montos que no salieron de las herramientas "
                "en este turno. Usa buscar_plan y cotizar (y tipo_de_cambio si hace falta) antes de dar "
                "cualquier precio o total.")
AVISO_SIN_HERRAMIENTAS = (f"{PREFIJO_CONTROL} El cliente mencionó un producto y no consultaste el catálogo. "
                          "Usa buscar_plan y, si corresponde, cotizar con la rebaja de partner. "
                          "Las reglas de rebaja ya las aplica la herramienta: úsala igual.")


def _menciona_montos(texto: str) -> bool:
    return bool(_MONEDA.search(texto) and _CIFRA.search(texto))


@after_model(can_jump_to=["model"])
def sin_precios_inventados(state, runtime):
    """Revisa cada respuesta final del modelo (sin tool_calls). Si:
      A) trae montos y en este turno no se usó ninguna herramienta de precios, o
      B) el cliente mencionó un producto y en este turno no se usó ninguna herramienta,
    descarta la respuesta y le pide al modelo que use las herramientas.
    Avisa UNA sola vez por turno (si insiste, la respuesta pasa): así nunca hay bucle."""
    mensajes = state["messages"]
    ultimo = mensajes[-1]
    if not isinstance(ultimo, AIMessage) or ultimo.tool_calls:
        return None

    # El turno empieza en el último mensaje real del cliente (no en un aviso de control).
    inicio = max((i for i, m in enumerate(mensajes)
                  if isinstance(m, HumanMessage) and not m.text.startswith(PREFIJO_CONTROL)), default=-1)
    if inicio < 0:
        return None
    turno = mensajes[inicio + 1:]
    if any(isinstance(m, HumanMessage) and m.text.startswith(PREFIJO_CONTROL) for m in turno):
        return None  # ya se avisó en este turno

    usadas = {m.name for m in turno if isinstance(m, ToolMessage)}
    aviso = None
    if _menciona_montos(ultimo.text or "") and not usadas & _HERRAMIENTAS_DE_PRECIO:
        aviso = AVISO_MONTOS
    elif not usadas and _PRODUCTO.search(mensajes[inicio].text):
        aviso = AVISO_SIN_HERRAMIENTAS
    if aviso is None:
        return None

    return {"messages": [RemoveMessage(id=ultimo.id), HumanMessage(aviso)], "jump_to": "model"}


# ---------------------------------------------------------------------------
# Construcción del agente
# ---------------------------------------------------------------------------

_LIMITADOR = InMemoryRateLimiter(requests_per_second=20 / 60, check_every_n_seconds=0.1, max_bucket_size=4)


def crear_llm(modelo: str = "openai/gpt-oss-120b", temperature: float = 0, reasoning_effort: str = "low") -> ChatGroq:
    return ChatGroq(
        model=modelo,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        max_retries=5,
        rate_limiter=_LIMITADOR,  # compartido: el límite por minuto de Groq es por cuenta
    )


def construir_agente(llm=None, con_aprobacion: bool = True, limite_llamadas: int = 8):
    """con_aprobacion=True: agente de producción (memoria + aprobación humana para registrar).
    con_aprobacion=False: agente para la evaluación automática (no puede quedarse esperando a una persona)."""
    llm = llm or crear_llm()
    middleware = [
        PIIMiddleware("email", strategy="redact"),
        ModelCallLimitMiddleware(run_limit=limite_llamadas),
        sin_precios_inventados,
        # Groq a veces responde 400 "output_parse_failed" (el modelo escribió algo que no pudo
        # interpretar como llamada a herramienta). Se reintenta con dos modelos de respaldo.
        ModelFallbackMiddleware(crear_llm(temperature=1, reasoning_effort="medium"),
                                crear_llm(temperature=1, reasoning_effort="medium")),
    ]
    extra = {}
    if con_aprobacion:
        middleware.append(HumanInTheLoopMiddleware(interrupt_on={"registrar_cotizacion": True}))
        extra["checkpointer"] = InMemorySaver()
    return create_agent(model=llm, tools=[tool(f) for f in HERRAMIENTAS],
                        system_prompt=INSTRUCCIONES, middleware=middleware, **extra)
