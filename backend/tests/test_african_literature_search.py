import sys
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.main import app
from app.schemas.protocol import ProtocolProject, ReferenceItem
from app.services.literature import (
    AFRICAN_OPENALEX_COUNTRIES,
    SearchPlan,
    classify_reference_regional_scope,
    normalize_search_scope,
    run_research_pipeline,
    search_europepmc_african,
    search_openalex,
    search_pubmed_african,
)
from tests._auth_helper import login_as_admin


def test_classify_reference_regional_scope() -> None:
    assert normalize_search_scope(None) == "hybrid_africa"
    assert normalize_search_scope("") == "hybrid_africa"
    assert normalize_search_scope("invalid") == "hybrid_africa"
    assert normalize_search_scope("GLOBAL") == "global"
    assert normalize_search_scope("africa_only") == "africa_only"
    assert normalize_search_scope("hybrid_africa") == "hybrid_africa"

    hsd_ref = ReferenceItem(
        title="Hypertension artérielle à l'Hôpital Central de Yaoundé",
        journal="Health Sciences and Disease",
        url="https://www.hsd-fmsb.org/index.php/hsd/article/view/1234",
        year=2025,
    )
    assert classify_reference_regional_scope(hsd_ref) == ("cameroun_fmsb", "Cameroun / FMSB")

    pamj_ref = ReferenceItem(
        title="Epidemiology of diabetes in Dakar",
        journal="Pan Afr Med J",
        url="https://www.panafrican-med-journal.com/content/article/45/12/full",
        year=2024,
    )
    assert classify_reference_regional_scope(pamj_ref) == ("afrique_cames", "Afrique / CAMES")

    ajol_ref = ReferenceItem(
        title="Prise en charge du paludisme grave au CHU de Bamako",
        journal="Mali Med",
        url="https://www.ajol.info/index.php/mm/article/view/99",
        year=2023,
    )
    assert classify_reference_regional_scope(ajol_ref) == ("afrique_cames", "Afrique / CAMES")

    who_afro_ref = ReferenceItem(
        title="Atlas sanitaire régional OMS",
        url="https://www.afro.who.int/countries/senegal",
    )
    assert classify_reference_regional_scope(who_afro_ref) == ("afrique_cames", "Afrique / CAMES")

    intl_ref = ReferenceItem(
        title="Global burden of cardiovascular diseases in Europe",
        journal="The Lancet",
        url="https://doi.org/10.1016/S0140-6736(24)00001-0",
        year=2025,
    )
    assert classify_reference_regional_scope(intl_ref) == ("international", None)
    assert classify_reference_regional_scope(intl_ref, force_min_africa=True) == (
        "afrique_cames",
        "Afrique / CAMES",
    )


def test_search_openalex_and_pubmed_epmc_african_queries() -> None:
    captured_oa_params = []

    class FakeResponse:
        def __init__(self, status_code=200, json_data=None, text=""):
            self.status_code = status_code
            self._json = json_data or {}
            self.text = text

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def json(self):
            return self._json

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, params=None):
            captured_oa_params.append((url, dict(params or {})))
            if "openalex.org" in url:
                return FakeResponse(
                    200,
                    {
                        "results": [
                            {
                                "id": "https://openalex.org/W1",
                                "title": "Prévalence de l'HTA chez l'adulte à Dakar",
                                "doi": "https://doi.org/10.1234/dakar.2025",
                                "publication_year": 2025,
                                "primary_location": {"source": {"display_name": "Dakar Médical"}},
                                "authorships": [{"author": {"display_name": "Diop A"}}],
                                "biblio": {"volume": "68", "issue": "2", "first_page": "10", "last_page": "16"},
                            }
                        ]
                    },
                )
            return FakeResponse(200, {})

    with patch("app.services.literature.httpx.Client", FakeClient):
        refs_global = search_openalex("hypertension", max_results=3, africa_only=False)
        refs_africa = search_openalex("hypertension", max_results=3, africa_only=True)

    assert len(captured_oa_params) == 2
    assert "filter" not in captured_oa_params[0][1]
    assert captured_oa_params[1][1].get("filter") == f"institutions.country_code:{AFRICAN_OPENALEX_COUNTRIES}"
    assert len(refs_global) == 1
    assert len(refs_africa) == 1
    assert refs_africa[0].regional_scope == "afrique_cames"
    assert refs_africa[0].african_source_label == "Afrique / CAMES"

    # Verify search_pubmed_african primary + fallback retry & tagging
    pubmed_calls = []

    def fake_search_pubmed(query: str, max_results: int = 5):
        pubmed_calls.append(query)
        if len(pubmed_calls) == 1:
            return []  # trigger fallback clause
        return [
            ReferenceItem(
                pmid="999001",
                source_platform="pubmed",
                title="Hypertension management in Cameroon teaching hospitals",
                journal="Pan Afr Med J",
                year=2025,
                authors=["Ngassa P"],
            )
        ]

    epmc_calls = []

    def fake_search_epmc(query: str, max_results: int = 5):
        epmc_calls.append(query)
        return [
            ReferenceItem(
                pmid="999002",
                source_platform="europepmc",
                title="Prevalence study in West African cohort",
                journal="Afr Health Sci",
                year=2024,
                authors=["Kouame K"],
            )
        ]

    with (
        patch("app.services.literature.search_pubmed", side_effect=fake_search_pubmed),
        patch("app.services.literature.search_europepmc", side_effect=fake_search_epmc),
    ):
        pm_af = search_pubmed_african('("hypertension"[Mesh]) AND adults', max_results=3)
        epmc_af = search_europepmc_african("hypertension adults", max_results=3)

    assert len(pubmed_calls) == 2
    assert '"Pan Afr Med J"[Journal]' in pubmed_calls[0]
    assert '"Health Sci Dis"[Journal]' in pubmed_calls[0]
    assert '"Cameroon"[MeSH]' in pubmed_calls[0]
    assert "Africa[Title/Abstract]" in pubmed_calls[1]
    assert len(pm_af) == 1
    assert pm_af[0].regional_scope == "cameroun_fmsb"
    assert pm_af[0].african_source_label == "Cameroun / FMSB"

    assert len(epmc_calls) == 1
    assert 'JOURNAL:"Pan African Medical Journal"' in epmc_calls[0]
    assert 'JOURNAL:"Health Sciences and Disease"' in epmc_calls[0]
    assert len(epmc_af) == 1
    assert epmc_af[0].regional_scope == "afrique_cames"
    assert epmc_af[0].african_source_label == "Afrique / CAMES"


def test_run_research_pipeline_scopes() -> None:
    fake_plan = SearchPlan(
        pubmed_queries=["hypertension prevalence", "hypertension risk factors"],
        openalex_queries=["hypertension prevalence", "hypertension risk factors"],
        local_epidemiology_query="hypertension cameroun afrique subsaharienne",
        target_sites=["PubMed", "AJOL", "HSD"],
    )

    def _make_project():
        return ProtocolProject(
            project_id=f"test-{uuid4().hex[:6]}",
            name="Test Scope",
            research_idea="Hypertension artérielle à Yaoundé",
        )

    def _run_with_scope(scope: str):
        calls = {
            "pubmed": 0,
            "epmc": 0,
            "oa_global": 0,
            "oa_africa": 0,
            "pm_africa": 0,
            "epmc_africa": 0,
            "web_focus": [],
        }

        def fake_pm(q, max_results=4):
            calls["pubmed"] += 1
            return [
                ReferenceItem(
                    pmid=f"pm_{calls['pubmed']}",
                    source_platform="pubmed",
                    title="International hypertension cohort in Sweden",
                    journal="Eur Heart J",
                    year=2025,
                )
            ]

        def fake_epmc(q, max_results=4):
            calls["epmc"] += 1
            return []

        def fake_oa(q, max_results=4, africa_only=False):
            if africa_only:
                calls["oa_africa"] += 1
                return [
                    ReferenceItem(
                        doi=f"10.1000/afoa.{calls['oa_africa']}",
                        source_platform="openalex",
                        title="Facteurs de risque cardiovasculaire au Cameroun",
                        journal="Health Sciences and Disease",
                        year=2025,
                    )
                ]
            calls["oa_global"] += 1
            return [
                ReferenceItem(
                    doi=f"10.1000/glo.{calls['oa_global']}",
                    source_platform="openalex",
                    title="European Society of Cardiology Guidelines",
                    journal="European Heart Journal",
                    year=2024,
                )
            ]

        def fake_pm_af(q, max_results=4):
            calls["pm_africa"] += 1
            return [
                ReferenceItem(
                    pmid=f"pmaf_{calls['pm_africa']}",
                    source_platform="pubmed",
                    title="Profil épidémiologique de l'HTA en Afrique de l'Ouest",
                    journal="Pan Afr Med J",
                    year=2024,
                )
            ]

        def fake_epmc_af(q, max_results=4):
            calls["epmc_africa"] += 1
            return [
                ReferenceItem(
                    pmid=f"epmcaf_{calls['epmc_africa']}",
                    source_platform="europepmc",
                    title="HTA et diabète au Mali",
                    journal="Mali Med",
                    year=2023,
                )
            ]

        def fake_web(q, lang="fr", africa_focus=False):
            calls["web_focus"].append(africa_focus)
            ref = ReferenceItem(
                source_platform="web",
                title="Rapport MINSANTE Cameroun 2025" if africa_focus else "WHO Global Report 2025",
                url="https://www.minsante.gov.cm/hta" if africa_focus else "https://www.who.int/global-hta",
            )
            return "Synthèse web", [ref], "gemini-3.8-flash"

        proj = _make_project()
        with (
            patch("app.services.literature.plan_research_queries", return_value=(fake_plan, "gemini-3.8-flash")),
            patch("app.services.literature.search_pubmed", side_effect=fake_pm),
            patch("app.services.literature.search_europepmc", side_effect=fake_epmc),
            patch("app.services.literature.search_openalex", side_effect=fake_oa),
            patch("app.services.literature.search_pubmed_african", side_effect=fake_pm_af),
            patch("app.services.literature.search_europepmc_african", side_effect=fake_epmc_af),
            patch("app.services.literature.search_web_institutional", side_effect=fake_web),
            patch("app.services.literature.emit_pipeline_step"),
        ):
            report = run_research_pipeline(
                proj,
                max_per_source=2,
                include_web_grounding=True,
                search_scope=scope,
            )
        return proj, report, calls

    # 1. Global mode
    proj_g, rep_g, calls_g = _run_with_scope("global")
    assert rep_g["search_scope"] == "global"
    assert calls_g["pubmed"] == 2
    assert calls_g["oa_global"] == 2
    assert calls_g["pm_africa"] == 0
    assert calls_g["epmc_africa"] == 0
    assert calls_g["oa_africa"] == 0
    assert calls_g["web_focus"] == [False]
    assert rep_g["african_references_count"] == 0

    # 2. Hybrid mode
    proj_h, rep_h, calls_h = _run_with_scope("hybrid_africa")
    assert rep_h["search_scope"] == "hybrid_africa"
    assert calls_h["pubmed"] == 2
    assert calls_h["oa_global"] == 2
    assert calls_h["pm_africa"] == 1
    assert calls_h["epmc_africa"] == 1
    assert calls_h["oa_africa"] == 1
    assert calls_h["web_focus"] == [True]
    assert rep_h["african_references_count"] == 4  # oa_africa + pm_africa + epmc_africa + minsante web

    # 3. Africa-only mode
    proj_a, rep_a, calls_a = _run_with_scope("africa_only")
    assert rep_a["search_scope"] == "africa_only"
    assert calls_a["pubmed"] == 0
    assert calls_a["epmc"] == 0
    assert calls_a["oa_global"] == 0
    assert calls_a["pm_africa"] == 2
    assert calls_a["epmc_africa"] == 2
    assert calls_a["oa_africa"] == 2
    assert calls_a["web_focus"] == [True]
    assert rep_a["african_references_count"] == rep_a["added_references_count"]
    assert rep_a["african_references_count"] >= 4


def test_api_research_endpoint_africa_only_persists_scope() -> None:
    client = TestClient(app)
    login_as_admin(client)

    created = client.post(
        "/api/projects",
        json={
            "name": f"Test Africa Research {uuid4().hex[:6]}",
            "entry_mode": "new_topic",
            "research_idea": "Paludisme grave chez l'enfant à Yaoundé",
            "study_type": "Étude transversale",
        },
    )
    assert created.status_code == 200, created.text
    project_id = created.json()["project_id"]

    fake_plan = SearchPlan(
        pubmed_queries=["severe malaria children"],
        openalex_queries=["severe malaria children"],
        local_epidemiology_query="paludisme grave enfant cameroun",
        target_sites=["HSD", "PAMJ", "AJOL"],
    )
    fake_ref_hsd = ReferenceItem(
        pmid="888001",
        source_platform="pubmed",
        title="Aspects cliniques du paludisme grave à l'Hôpital Gynéco-Obstétrique et Pédiatrique de Yaoundé",
        journal="Health Sciences and Disease",
        year=2025,
        authors=["Mbonda E"],
    )
    fake_ref_pamj = ReferenceItem(
        pmid="888002",
        source_platform="europepmc",
        title="Malaria severe anemia outcomes in African pediatric wards",
        journal="Pan Afr Med J",
        year=2024,
        authors=["Traore M"],
    )

    try:
        with (
            patch("app.services.literature.plan_research_queries", return_value=(fake_plan, "gemini-3.8-flash")),
            patch("app.services.literature.search_pubmed_african", return_value=[fake_ref_hsd]),
            patch("app.services.literature.search_europepmc_african", return_value=[fake_ref_pamj]),
            patch("app.services.literature.search_openalex", return_value=[]),
            patch("app.services.literature.search_web_institutional", return_value=("", [], "gemini-3.8-flash")),
        ):
            res = client.post(
                f"/api/projects/{project_id}/research",
                json={
                    "max_per_source": 2,
                    "include_web_grounding": True,
                    "search_scope": "africa_only",
                },
            )
        assert res.status_code == 200, res.text
        data = res.json()
        report = data["report"]
        assert report["search_scope"] == "africa_only"
        assert report["african_references_count"] == 2

        fetched = client.get(f"/api/projects/{project_id}")
        assert fetched.status_code == 200, fetched.text
        refs = fetched.json()["references"]
        assert len(refs) == 2
        scopes = {r["title"]: (r["regional_scope"], r["african_source_label"]) for r in refs}
        assert scopes[fake_ref_hsd.title] == ("cameroun_fmsb", "Cameroun / FMSB")
        assert scopes[fake_ref_pamj.title] == ("afrique_cames", "Afrique / CAMES")
    finally:
        client.delete(f"/api/projects/{project_id}")


if __name__ == "__main__":
    test_classify_reference_regional_scope()
    print("[OK] test_classify_reference_regional_scope")
    test_search_openalex_and_pubmed_epmc_african_queries()
    print("[OK] test_search_openalex_and_pubmed_epmc_african_queries")
    test_run_research_pipeline_scopes()
    print("[OK] test_run_research_pipeline_scopes")
    test_api_research_endpoint_africa_only_persists_scope()
    print("[OK] test_api_research_endpoint_africa_only_persists_scope")
    print("ALL AFRICAN LITERATURE SEARCH TESTS PASSED")
