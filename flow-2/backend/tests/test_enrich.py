import json
from pathlib import Path

import pytest

from backend.app.enrich import enriquecer_propiedad
from backend.app.scoring.engine import evaluar_propiedad

EJEMPLOS = Path(__file__).resolve().parents[2] / "standalone-tools" / "ejemplos"


def _seed(nombre):
    return json.loads((EJEMPLOS / nombre).read_text(encoding="utf-8"))


def test_enrich_las_condes_verde():
    prop = enriquecer_propiedad(_seed("seed_las_condes.json"))
    # Todas las fuentes resuelven OK en modo fixture.
    assert set(prop["_fuentes"]) == {
        "sii", "conservador", "diario_oficial", "registro_civil", "cip"
    }
    assert all(estado == "ok" for estado in prop["_fuentes"].values())
    assert prop["sii"]["avaluo_fiscal_uf"] == 8200
    assert prop["cip"]["zona"] == "ZU-3"
    assert evaluar_propiedad(prop).score == "VERDE"


def test_enrich_maipu_rojo_por_remate_y_embargo():
    prop = enriquecer_propiedad(_seed("seed_maipu.json"))
    # El aviso de remate del Diario Oficial deriva remate.en_remate = True.
    assert prop["remate"]["en_remate"] is True
    assert len(prop["conservador"]["embargos"]) == 1
    resultado = evaluar_propiedad(prop)
    assert resultado.score == "ROJO"
    ids = {o.id for o in resultado.observaciones}
    assert {"remate_activo", "embargo_vigente"} <= ids


def test_enrich_san_bernardo_rojo_por_defuncion():
    prop = enriquecer_propiedad(_seed("seed_san_bernardo.json"))
    assert all(estado == "ok" for estado in prop["_fuentes"].values())
    assert prop["registro_civil"]["propietario_vivo"] is False
    resultado = evaluar_propiedad(prop)
    assert resultado.score == "ROJO"
    assert "propietario_fallecido" in {o.id for o in resultado.observaciones}


def test_enrich_aisla_fuente_pendiente(monkeypatch):
    # En modo real las fuentes asistidas quedan 'pendiente' sin romper la DD.
    monkeypatch.setenv("SCRAPER_FIXTURE_MODE", "0")
    prop = enriquecer_propiedad(_seed("seed_las_condes.json"))
    assert prop["_fuentes"]["sii"].startswith("pendiente")
    assert prop["_fuentes"]["registro_civil"].startswith("pendiente")
    # No explota: sigue devolviendo una propiedad evaluable.
    assert evaluar_propiedad(prop).score in {"VERDE", "AMARILLO", "ROJO"}
