import pytest

from backend.app.scrapers.base import AssistedCaptureRequired, fixture_mode
from backend.app.scrapers.conservador import ConservadorScraper
from backend.app.scrapers.diario_oficial import DiarioOficialScraper
from backend.app.scrapers.ide_chile import (
    ComunaNoMapeada,
    IDEChileScraper,
    normalizar_comuna,
)
from backend.app.scrapers.registro_civil import RegistroCivilScraper
from backend.app.scrapers.sii import SIIScraper


def test_fixture_mode_default_activo(monkeypatch):
    monkeypatch.delenv("SCRAPER_FIXTURE_MODE", raising=False)
    assert fixture_mode() is True


def test_fixture_mode_se_apaga(monkeypatch):
    monkeypatch.setenv("SCRAPER_FIXTURE_MODE", "0")
    assert fixture_mode() is False


def test_sii_fixture():
    data = SIIScraper().fetch(rol="1234-56", comuna="Las Condes")
    assert data["avaluo_fiscal_uf"] == 8200
    assert data["destino"] == "habitacional"


def test_conservador_fixture():
    data = ConservadorScraper().fetch(rol="5678-90")
    assert len(data["embargos"]) == 1
    assert data["hipotecas"][0]["acreedor"] == "Banco X"


def test_registro_civil_fixture():
    assert RegistroCivilScraper().fetch(rut="76.123.456-7")["propietario_vivo"] is True


def test_ide_chile_normaliza_geojson():
    cip = IDEChileScraper().fetch(lat=-33.4096, lon=-70.5681)
    assert cip["zona"] == "ZU-3"
    assert cip["altura_maxima_m"] == 21
    assert cip["coef_constructibilidad"] == 1.8
    # 'usos' (string separado por comas) se normaliza a lista.
    assert cip["uso_permitido"] == ["habitacional", "equipamiento"]


def test_normalizar_comuna():
    assert normalizar_comuna("Maipú") == "maipu"
    assert normalizar_comuna("San Bernardo") == "san_bernardo"
    assert normalizar_comuna("LAS CONDES") == "las_condes"


def test_ide_chile_typename_por_comuna():
    scraper = IDEChileScraper()
    assert scraper.typename_para("Las Condes") == "geonode:prc_las_condes"
    assert scraper.typename_para("Maipú") == "geonode:prc_maipu"


def test_ide_chile_comuna_no_mapeada_falla_explicito():
    with pytest.raises(ComunaNoMapeada):
        IDEChileScraper().typename_para("Punta Arenas")


def test_diario_oficial_sin_resultados():
    assert DiarioOficialScraper().fetch(termino="1234-56")["publicaciones"] == []


def test_diario_oficial_detecta_remate():
    pubs = DiarioOficialScraper().fetch(termino="5678-90")["publicaciones"]
    assert len(pubs) == 1
    assert pubs[0]["tipo"] == "aviso_remate"
    assert pubs[0]["fecha"] == "02-05-2026"


@pytest.mark.parametrize(
    "scraper,kwargs",
    [
        (SIIScraper(), {"rol": "1234-56", "comuna": "Las Condes"}),
        (RegistroCivilScraper(), {"rut": "76.123.456-7"}),
        (ConservadorScraper(), {"rol": "1234-56"}),
    ],
)
def test_fuentes_asistidas_levantan_en_modo_real(monkeypatch, scraper, kwargs):
    monkeypatch.setenv("SCRAPER_FIXTURE_MODE", "0")
    with pytest.raises(AssistedCaptureRequired):
        scraper.fetch(**kwargs)


def test_fixture_inexistente_levanta_filenotfound():
    with pytest.raises(FileNotFoundError):
        SIIScraper().fetch(rol="0000-00")
