"""
Unit tests for scraper/base.py — pure utility functions, no network.
"""
import pytest

from scraper.base import normalize_commune, parse_m2, parse_price_clp


# ---------------------------------------------------------------------------
# normalize_commune
# ---------------------------------------------------------------------------


class TestNormalizeCommune:
    def test_known_communes_lowercase(self):
        assert normalize_commune("las condes") == "Las Condes"
        assert normalize_commune("vitacura") == "Vitacura"
        assert normalize_commune("lo barnechea") == "Lo Barnechea"
        assert normalize_commune("providencia") == "Providencia"
        assert normalize_commune("la florida") == "La Florida"
        assert normalize_commune("puente alto") == "Puente Alto"
        assert normalize_commune("santiago centro") == "Santiago"

    def test_nunoa_variants(self):
        assert normalize_commune("ñuñoa") == "Ñuñoa"
        assert normalize_commune("nunoa") == "Ñuñoa"

    def test_penalolen_variants(self):
        assert normalize_commune("peñalolén") == "Peñalolén"
        assert normalize_commune("penalolen") == "Peñalolén"

    def test_already_canonical(self):
        assert normalize_commune("Las Condes") == "Las Condes"

    def test_unknown_commune_title_cased(self):
        result = normalize_commune("buin")
        assert result == "Buin"

    def test_strips_whitespace(self):
        assert normalize_commune("  las condes  ") == "Las Condes"


# ---------------------------------------------------------------------------
# parse_price_clp
# ---------------------------------------------------------------------------


class TestParsePriceClp:
    def test_chilean_peso_format_periods(self):
        # "$85.000.000" → 85000000
        assert parse_price_clp("85.000.000") == 85000000

    def test_simple_integer(self):
        assert parse_price_clp("1000000") == 1000000

    def test_with_dollar_sign(self):
        assert parse_price_clp("$1.500.000") == 1500000

    def test_with_spaces(self):
        assert parse_price_clp("2 300 000") == 2300000

    def test_strips_letters(self):
        assert parse_price_clp("650 UF") == 650

    def test_empty_string_returns_none(self):
        assert parse_price_clp("") is None

    def test_only_text_returns_none(self):
        assert parse_price_clp("precio a convenir") is None

    def test_commas_as_thousands(self):
        assert parse_price_clp("85,000,000") == 85000000


# ---------------------------------------------------------------------------
# parse_m2
# ---------------------------------------------------------------------------


class TestParseM2:
    def test_integer_m2(self):
        assert parse_m2("65 m²") == pytest.approx(65.0)

    def test_decimal_m2(self):
        assert parse_m2("82.5m²") == pytest.approx(82.5)

    def test_comma_decimal(self):
        assert parse_m2("72,3 m2") == pytest.approx(72.3)

    def test_lowercase_m2(self):
        assert parse_m2("100 m2") == pytest.approx(100.0)

    def test_with_extra_text(self):
        assert parse_m2("superficie: 55 m²") == pytest.approx(55.0)

    def test_no_m2_returns_none(self):
        assert parse_m2("3 dormitorios") is None

    def test_empty_string_returns_none(self):
        assert parse_m2("") is None


# ---------------------------------------------------------------------------
# Corridor stats helper (run.py in-memory)
# ---------------------------------------------------------------------------


class TestInMemoryCorridorStats:
    """Tests for the in-memory corridor median helpers in run.py."""

    def _make_listing(self, tipo, comuna, m2, precio_m2):
        return {
            "tipo_propiedad": tipo,
            "comuna": comuna,
            "m2": m2,
            "precio_m2": float(precio_m2),
            "precio": int(precio_m2 * m2),
            "external_id": f"{tipo}-{comuna}-{m2}-{precio_m2}",
            "source": "portal_inmobiliario",
            "url": f"https://example.com/{tipo}",
            "fecha_publicacion": None,
        }

    def test_median_computed_for_group_with_3plus(self):
        from run import _compute_medians

        listings = [
            self._make_listing("departamento", "Las Condes", 60, 3_000_000),
            self._make_listing("departamento", "Las Condes", 65, 3_200_000),
            self._make_listing("departamento", "Las Condes", 70, 2_800_000),
        ]
        medians = _compute_medians(listings)
        # All fall in 50-80 bucket
        key = ("departamento", "Las Condes", 50.0, 80.0)
        assert key in medians
        assert medians[key] == pytest.approx(3_000_000.0)

    def test_group_with_fewer_than_3_excluded(self):
        from run import _compute_medians

        listings = [
            self._make_listing("departamento", "Vitacura", 60, 4_000_000),
            self._make_listing("departamento", "Vitacura", 65, 4_200_000),
        ]
        medians = _compute_medians(listings)
        key = ("departamento", "Vitacura", 50.0, 80.0)
        assert key not in medians

    def test_different_buckets_separate_medians(self):
        from run import _compute_medians

        small = [self._make_listing("departamento", "Providencia", 40 + i, 3_000_000) for i in range(3)]
        large = [self._make_listing("departamento", "Providencia", 90 + i, 2_000_000) for i in range(3)]
        medians = _compute_medians(small + large)

        key_small = ("departamento", "Providencia", 0.0, 50.0)
        key_large = ("departamento", "Providencia", 80.0, 120.0)
        assert key_small in medians
        assert key_large in medians
        assert medians[key_small] != medians[key_large]

    def test_deduplication(self):
        from run import _deduplicate

        listings = [
            {**self._make_listing("departamento", "Ñuñoa", 60, 2_000_000), "external_id": "123"},
            {**self._make_listing("departamento", "Ñuñoa", 60, 2_000_000), "external_id": "123"},
            {**self._make_listing("departamento", "Ñuñoa", 60, 2_000_000), "external_id": "456"},
        ]
        unique = _deduplicate(listings)
        assert len(unique) == 2


# ---------------------------------------------------------------------------
# Portal commune extraction
# ---------------------------------------------------------------------------


class TestPortalCommuneExtraction:
    def test_extract_las_condes(self):
        from scraper.portal_inmobiliario import _extract_commune_from_location

        result = _extract_commune_from_location("Las Condes, Región Metropolitana")
        assert result == "Las Condes"

    def test_extract_nunoa(self):
        from scraper.portal_inmobiliario import _extract_commune_from_location

        result = _extract_commune_from_location("Ñuñoa, Región Metropolitana de Santiago")
        assert result == "Ñuñoa"

    def test_extract_puente_alto(self):
        from scraper.portal_inmobiliario import _extract_commune_from_location

        result = _extract_commune_from_location("Departamento en Puente Alto")
        assert result == "Puente Alto"

    def test_unknown_location_returns_first_segment(self):
        from scraper.portal_inmobiliario import _extract_commune_from_location

        result = _extract_commune_from_location("Macul, Santiago")
        # Falls through to first-segment fallback
        assert result is not None and len(result) > 0
