"""Motor de scoring de Due Diligence inmobiliaria.

Evalúa una propiedad (dict) contra reglas declarativas definidas en
``rules.yml`` y devuelve un veredicto VERDE / AMARILLO / ROJO junto con las
observaciones que lo gatillan.

El score final es:
  - ROJO     si alguna regla roja se cumple,
  - AMARILLO si alguna regla amarilla se cumple y ninguna roja,
  - VERDE    si no se cumple ninguna regla.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

RULES_PATH = Path(__file__).parent / "rules.yml"


@dataclass
class Observacion:
    id: str
    severidad: str
    mensaje: str


@dataclass
class Resultado:
    score: str
    observaciones: list[Observacion] = field(default_factory=list)


def cargar_reglas(path: str | Path = RULES_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("reglas", [])


def _get(data: Any, path: str) -> Any:
    """Lee un campo por ruta con puntos (ej: ``conservador.embargos``)."""
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _evaluar_condicion(operador: str, actual: Any, valor: Any) -> bool:
    if operador == "is_true":
        return actual is True
    if operador == "is_false":
        return actual is False
    if operador == "equals":
        return actual == valor
    if operador == "not_equals":
        return actual is not None and actual != valor
    if operador == "exists_nonempty":
        return bool(actual)
    if operador == "is_empty":
        return not actual
    if operador == "gt":
        return actual is not None and actual > valor
    if operador == "lt":
        return actual is not None and actual < valor
    if operador == "gte":
        return actual is not None and actual >= valor
    if operador == "lte":
        return actual is not None and actual <= valor
    if operador == "in":
        return actual in (valor or [])
    if operador == "contains":
        return valor in (actual or [])
    raise ValueError(f"Operador desconocido: {operador!r}")


def _agregar_score(observaciones: list[Observacion]) -> str:
    severidades = {o.severidad for o in observaciones}
    if "rojo" in severidades:
        return "ROJO"
    if "amarillo" in severidades:
        return "AMARILLO"
    return "VERDE"


def evaluar_propiedad(
    propiedad: dict, reglas: list[dict] | None = None
) -> Resultado:
    if reglas is None:
        reglas = cargar_reglas()

    observaciones: list[Observacion] = []
    for regla in reglas:
        actual = _get(propiedad, regla["campo"])
        if _evaluar_condicion(regla["operador"], actual, regla.get("valor")):
            observaciones.append(
                Observacion(
                    id=regla["id"],
                    severidad=regla["severidad"],
                    mensaje=regla["mensaje"],
                )
            )
    return Resultado(score=_agregar_score(observaciones), observaciones=observaciones)
