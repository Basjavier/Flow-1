import json
from pathlib import Path

import pytest

from backend.app.scoring.engine import evaluar_propiedad

EJEMPLOS = Path(__file__).resolve().parents[2] / "standalone-tools" / "ejemplos"

CASOS = [
    ("las_condes_verde.json", "VERDE", set()),
    ("maipu_rojo_remate.json", "ROJO", {"remate_activo", "embargo_vigente"}),
    ("san_bernardo_rojo_defuncion.json", "ROJO", {"propietario_fallecido"}),
]


@pytest.mark.parametrize("archivo,score_esperado,obs_esperadas", CASOS)
def test_ejemplo(archivo, score_esperado, obs_esperadas):
    data = json.loads((EJEMPLOS / archivo).read_text(encoding="utf-8"))
    resultado = evaluar_propiedad(data)
    assert resultado.score == score_esperado
    ids = {o.id for o in resultado.observaciones}
    assert obs_esperadas.issubset(ids), f"esperaba {obs_esperadas}, obtuve {ids}"


def test_verde_no_tiene_observaciones():
    data = json.loads((EJEMPLOS / "las_condes_verde.json").read_text(encoding="utf-8"))
    assert evaluar_propiedad(data).observaciones == []
