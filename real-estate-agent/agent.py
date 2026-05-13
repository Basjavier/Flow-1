"""
Agente conversacional de remates inmobiliarios.

Uso:
    python real-estate-agent/agent.py
    python real-estate-agent/agent.py --no-color   # salida sin colores ANSI

El agente utiliza Claude Opus 4.7 con tool use para responder preguntas sobre
los activos del portafolio Tier-A y ejecutar análisis financieros bajo demanda.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import anthropic

# Make project root and this directory importable when run directly
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
for _p in (_THIS_DIR, _PROJECT_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools import TOOL_DEFINITIONS, dispatch  # type: ignore

# ---------------------------------------------------------------------------
# ANSI colour helpers
# ---------------------------------------------------------------------------

_USE_COLOR = True


def _c(code: str, text: str) -> str:
    if not _USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def cyan(t: str) -> str:
    return _c("36", t)


def green(t: str) -> str:
    return _c("32", t)


def yellow(t: str) -> str:
    return _c("33", t)


def dim(t: str) -> str:
    return _c("2", t)


def bold(t: str) -> str:
    return _c("1", t)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Eres un analista institucional especializado en remates judiciales inmobiliarios chilenos.

Tu rol es asesorar a inversionistas sobre las oportunidades de compra en remate, interpretar los análisis
financieros del portafolio Tier-A, y ayudar a tomar decisiones de oferta fundamentadas.

Contexto del mercado (Chile 2025-2026):
- Los remates judiciales ofrecen descuentos del 15-35 % vs el mercado libre.
- El ciclo típico de flip (adjudicación → renovación → venta) es de 9-11 meses en escenario estándar.
- Los costos de entrada incluyen martillero (~1.5 %), notaría/CBR (~2 %), y otros (~1 %).
- La renovación media es 7-9 UF/m² en escenario estándar.
- ROI objetivo para operaciones competitivas: 18-22 %. Excelente: 25-30 %+.
- Las deudas ocultas (gastos comunes + contribuciones atrasadas) suelen ser 80-220 UF.

Instrucciones:
- Responde siempre en español.
- Usa las herramientas disponibles para obtener datos precisos antes de dar recomendaciones.
- Cuando el usuario pregunte por un activo sin especificar escenario, usa 'estandar' por defecto.
- Sé directo y cuantitativo: incluye UF, porcentajes y fechas cuando sea relevante.
- Advierte sobre riesgos cuando corresponda (poca confianza en comparables, urgencia de remate, etc.).
- No inventes datos; si no tienes la información, usa list_assets o get_asset_detail para obtenerla.
"""

# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

MAX_ITERATIONS = 12  # safety cap to avoid infinite loops


def run_agent(client: anthropic.Anthropic, messages: list[dict]) -> str:
    """Execute one full agentic turn (may involve multiple tool calls).

    Returns the final text response.
    """
    for iteration in range(MAX_ITERATIONS):
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=4096,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )

        # Collect text blocks to show the user
        text_blocks = [b for b in response.content if b.type == "text"]
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        if response.stop_reason == "end_turn" or not tool_use_blocks:
            # Done — return the final text
            return "\n".join(b.text for b in text_blocks).strip()

        # Append assistant message (must include tool_use blocks)
        messages.append({"role": "assistant", "content": response.content})

        # Stream any partial text while tools execute
        for tb in text_blocks:
            if tb.text.strip():
                print(dim(f"  [{tb.text.strip()}]"), flush=True)

        # Execute tools and build tool_result blocks
        tool_results = []
        for tu in tool_use_blocks:
            print(dim(f"  ⚙  {tu.name}({json.dumps(tu.input, ensure_ascii=False)[:120]}…)"), flush=True)
            result_str = dispatch(tu.name, tu.input)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": result_str,
            })

        messages.append({"role": "user", "content": tool_results})

    return "(Se alcanzó el límite de iteraciones del agente.)"


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------

WELCOME = """
╔══════════════════════════════════════════════════════════════╗
║   Agente de Remates Inmobiliarios — Portafolio Tier-A       ║
║   Escribe 'salir' o presiona Ctrl+C para terminar.          ║
╚══════════════════════════════════════════════════════════════╝
"""

EXAMPLES = [
    "¿Qué activos hay disponibles?",
    "Analiza el activo 77948 en escenario estándar",
    "¿Cuánto puedo ofrecer por el #77833 para obtener 25% ROI?",
    "Compara los activos en La Serena",
    "Corre Monte Carlo para 77947",
]


def main() -> None:
    global _USE_COLOR

    parser = argparse.ArgumentParser(description="Agente conversacional de remates inmobiliarios")
    parser.add_argument("--no-color", action="store_true", help="Desactivar colores ANSI")
    parser.add_argument("--debug", action="store_true", help="Mostrar mensajes de debug")
    args = parser.parse_args()

    if args.no_color:
        _USE_COLOR = False

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: la variable de entorno ANTHROPIC_API_KEY no está definida.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    print(bold(WELCOME))
    print(cyan("Ejemplos de preguntas:"))
    for ex in EXAMPLES:
        print(f"  • {ex}")
    print()

    conversation: list[dict] = []

    while True:
        try:
            user_input = input(bold(green("Tú: "))).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n" + dim("Hasta luego."))
            break

        if not user_input:
            continue
        if user_input.lower() in {"salir", "exit", "quit", "q"}:
            print(dim("Hasta luego."))
            break

        conversation.append({"role": "user", "content": user_input})

        print(bold(yellow("Agente: ")), end="", flush=True)
        try:
            reply = run_agent(client, conversation)
        except anthropic.APIError as exc:
            reply = f"[Error de API: {exc}]"

        print(reply)
        print()

        # Append assistant reply to history if not already added by the tool loop.
        # run_agent appends assistant + tool_result pairs inside the loop, so the
        # final assistant text response may not yet be in conversation.
        last = conversation[-1] if conversation else None
        if last is None or last.get("role") != "assistant":
            conversation.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    main()
