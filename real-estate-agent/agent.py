"""
Real Estate Agent — CLI conversacional sobre remates judiciales en Chile.

Usage:
  cd real-estate-agent && python agent.py
  ANTHROPIC_API_KEY=sk-... python agent.py
"""

from __future__ import annotations
import json
import os
import sys

# Path setup — hyphenated dir can't be a package
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
for _p in (_THIS_DIR, _PROJECT_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import anthropic
from tools import TOOL_DEFINITIONS, dispatch

# ---------------------------------------------------------------------------
# ANSI colors
# ---------------------------------------------------------------------------
CYAN   = "\033[96m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
GRAY   = "\033[90m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """Eres un analista experto en remates judiciales inmobiliarios en Chile.

Tu rol es ayudar a evaluar activos en remate judicial para inversiones de tipo flip (compra → renovación → venta).
Tienes acceso a un motor financiero calibrado con datos reales del mercado chileno 2024-2025.

CONTEXTO DEL MERCADO:
- Unidad de Fomento (UF): unidad indexada a inflación (~$38.000 CLP). Todos los precios están en UF.
- Remates judiciales: subastas de propiedades con deudas hipotecarias. Se compran con descuento significativo.
- Ciclo flip típico: 9-11 meses (adjudicación → renovación → venta).
- ROI objetivo: 18-22% (buena operación). ≥25% = excelente. <15% = no recomendable.
- Costos de entrada: 4.5% (martillero 1.5% + notaría/CBR 2% + otros 1%).
- Deudas ocultas: gastos comunes + contribuciones atrasadas (normalmente 18-36 meses en mora).

ESCENARIOS DISPONIBLES:
- cosmetico: pintura, pisos, limpieza. 4.5 UF/m², 6 meses, venta al 83% del mercado.
- estandar: baños, cocina, electricidad. 8 UF/m², 10 meses, venta al 88%. (REFERENCIA)
- deteriorado: renovación pesada, ocupante posible. 13 UF/m², 13 meses, venta al 85%.
- stress: problemas estructurales, todo sale mal. 18 UF/m², 17 meses, venta al 80%.

ACTIVOS ACTUALES (Tier A, abril-mayo 2026):
- #77833: Viña del Mar, 50m², 650 UF base. Ya vendido como validación (60 UF/m²).
- #77948: La Serena, 59m², 700 UF base. 8 comparables, 3d/2b. PRIORITARIO.
- #77947: La Serena, 49m², 600 UF base. 3d/1b (ajuste -12%). Mismo día que #77948.

Cuando el usuario pregunte sobre activos, usa las herramientas disponibles para dar análisis concretos con números.
Siempre menciona los riesgos relevantes: confianza de comparables, conflicto de capital, deudas ocultas.
Responde en español. Sé directo y conciso — este es un contexto de inversión real."""

# ---------------------------------------------------------------------------
# Agentic loop
# ---------------------------------------------------------------------------

def run_agent(client: anthropic.Anthropic, messages: list) -> tuple:
    """Run the agentic loop. Returns (final_text, updated_messages)."""
    MAX_ITER = 12

    for iteration in range(MAX_ITER):
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=4096,
            thinking={"type": "adaptive", "budget_tokens": 2000},
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )

        text_parts = []
        tool_uses = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_uses.append(block)

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn" or not tool_uses:
            return " ".join(text_parts).strip(), messages

        tool_results = []
        for tu in tool_uses:
            print(f"{GRAY}  [tool] {tu.name}({json.dumps(tu.input, ensure_ascii=False)[:120]}){RESET}")
            result_str = dispatch(tu.name, tu.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": result_str,
            })

        messages.append({"role": "user", "content": tool_results})

    return "(máximo de iteraciones alcanzado)", messages


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------

def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(f"{YELLOW}Advertencia: ANTHROPIC_API_KEY no está definida.{RESET}")

    client = anthropic.Anthropic(api_key=api_key)
    messages = []

    print(f"\n{BOLD}{CYAN}╔{'═'*46}╗{RESET}")
    print(f"{BOLD}{CYAN}║   Agente de Remates Inmobiliarios — Chile    ║{RESET}")
    print(f"{BOLD}{CYAN}╚{'═'*46}╝{RESET}")
    print(f"{GRAY}Activos Tier A: #77833 (Viña), #77948 (La Serena), #77947 (La Serena){RESET}")
    print(f"{GRAY}Escribe 'salir' para terminar. 'reset' para nueva conversación.{RESET}\n")

    while True:
        try:
            user_input = input(f"{GREEN}Tú: {RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{GRAY}Hasta luego.{RESET}")
            break

        if not user_input:
            continue
        if user_input.lower() in ("salir", "exit", "quit"):
            print(f"{GRAY}Hasta luego.{RESET}")
            break
        if user_input.lower() == "reset":
            messages = []
            print(f"{GRAY}[Conversación reiniciada]{RESET}\n")
            continue

        messages.append({"role": "user", "content": user_input})

        try:
            reply, messages = run_agent(client, messages)
        except Exception as e:
            print(f"{YELLOW}Error: {e}{RESET}\n")
            messages.pop()
            continue

        print(f"\n{CYAN}{BOLD}Agente:{RESET} {reply}\n")


if __name__ == "__main__":
    main()
