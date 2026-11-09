"""Utilidades para leer la traza del agente: USUARIO · ACTÚA · OBSERVA · RESPONDE (como en clase)."""

from __future__ import annotations

import time

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def mostrar_traza(mensajes) -> None:
    for m in mensajes:
        if isinstance(m, HumanMessage):
            etiqueta = "CONTROL " if m.text.startswith("[CONTROL") else "USUARIO "
            print(f"{etiqueta}  {m.text}")
        elif isinstance(m, AIMessage) and m.tool_calls:
            for tc in m.tool_calls:
                args = ", ".join(f"{k}={v!r}" for k, v in tc["args"].items())
                print(f"ACTÚA     {tc['name']}({args})")
        elif isinstance(m, ToolMessage):
            print(f"OBSERVA   {str(m.content)[:220]}")
        elif isinstance(m, AIMessage):
            print(f"RESPONDE  {m.text}")


def conversar(agente, entrada, hilo: str):
    """Envía un texto (o un Command de aprobación) al agente y muestra solo lo nuevo de este turno."""
    cfg = {"configurable": {"thread_id": hilo}}
    previos = len(agente.get_state(cfg).values.get("messages", []))
    if isinstance(entrada, str):
        entrada = {"messages": [HumanMessage(entrada)]}
    t0 = time.perf_counter()
    r = agente.invoke(entrada, cfg)
    mostrar_traza(r["messages"][previos:])
    if r.get("__interrupt__"):
        for pedido in r["__interrupt__"][0].value["action_requests"]:
            print(f"\n⏸  PAUSA: quiere ejecutar {pedido['name']}({pedido['args']}) -> requiere aprobación")
    print(f"({time.perf_counter() - t0:.1f} s)")
    return r


def herramientas_usadas(resultado) -> list[str]:
    return [tc["name"] for m in resultado["messages"] if isinstance(m, AIMessage) for tc in m.tool_calls]
