from backend.app.scoring.engine import (
    _agregar_score,
    _evaluar_condicion,
    _get,
    cargar_reglas,
    evaluar_propiedad,
    Observacion,
)


def test_get_ruta_con_puntos():
    data = {"a": {"b": {"c": 1}}}
    assert _get(data, "a.b.c") == 1
    assert _get(data, "a.x") is None
    assert _get(data, "a.b.c.d") is None
    assert _get(data, "noexiste") is None


def test_operador_is_true():
    assert _evaluar_condicion("is_true", True, None) is True
    assert _evaluar_condicion("is_true", False, None) is False
    assert _evaluar_condicion("is_true", None, None) is False


def test_operador_is_false():
    assert _evaluar_condicion("is_false", False, None) is True
    assert _evaluar_condicion("is_false", True, None) is False
    # Un campo ausente (None) no confirma "false".
    assert _evaluar_condicion("is_false", None, None) is False


def test_operador_exists_nonempty():
    assert _evaluar_condicion("exists_nonempty", [1], None) is True
    assert _evaluar_condicion("exists_nonempty", [], None) is False
    assert _evaluar_condicion("exists_nonempty", None, None) is False


def test_operador_is_empty():
    assert _evaluar_condicion("is_empty", "", None) is True
    assert _evaluar_condicion("is_empty", None, None) is True
    assert _evaluar_condicion("is_empty", "ZU-3", None) is False


def test_operador_not_equals_ignora_ausentes():
    assert _evaluar_condicion("not_equals", "comercial", "habitacional") is True
    assert _evaluar_condicion("not_equals", "habitacional", "habitacional") is False
    # Si el campo no viene, no debe gatillar la observación.
    assert _evaluar_condicion("not_equals", None, "habitacional") is False


def test_operador_lt():
    assert _evaluar_condicion("lt", 0.8, 1.0) is True
    assert _evaluar_condicion("lt", 1.0, 1.0) is False
    assert _evaluar_condicion("lt", None, 1.0) is False


def test_operador_desconocido_levanta_error():
    try:
        _evaluar_condicion("xor", 1, 2)
    except ValueError:
        return
    raise AssertionError("se esperaba ValueError")


def test_agregar_score_prioriza_rojo():
    obs = [Observacion("a", "amarillo", "m"), Observacion("b", "rojo", "m")]
    assert _agregar_score(obs) == "ROJO"


def test_agregar_score_amarillo():
    obs = [Observacion("a", "amarillo", "m")]
    assert _agregar_score(obs) == "AMARILLO"


def test_agregar_score_verde_sin_observaciones():
    assert _agregar_score([]) == "VERDE"


def test_evaluar_propiedad_con_reglas_inline():
    reglas = [
        {"id": "a", "severidad": "amarillo", "campo": "x", "operador": "is_true", "mensaje": "m"},
        {"id": "b", "severidad": "rojo", "campo": "y", "operador": "is_true", "mensaje": "m"},
    ]
    r = evaluar_propiedad({"x": True, "y": True}, reglas)
    assert r.score == "ROJO"
    assert {o.id for o in r.observaciones} == {"a", "b"}


def test_evaluar_propiedad_vacia_es_verde():
    r = evaluar_propiedad({}, [])
    assert r.score == "VERDE"
    assert r.observaciones == []


def test_rules_yml_carga_y_tiene_reglas():
    reglas = cargar_reglas()
    assert len(reglas) >= 1
    for regla in reglas:
        assert {"id", "severidad", "campo", "operador", "mensaje"} <= set(regla)
        assert regla["severidad"] in {"rojo", "amarillo"}
