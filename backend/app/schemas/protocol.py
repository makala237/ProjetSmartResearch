from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


class EntryMode(str, Enum):
    NEW_TOPIC = "new_topic"
    EXISTING_PROTOCOL = "existing_protocol"


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    NEEDS_REVISION = "needs_revision"
    VALIDATED = "validated"


class CanvasConstraint(BaseModel):
    min_pages: Optional[float] = None
    max_pages: Optional[float] = None
    min_references: Optional[int] = None
    max_references: Optional[int] = None
    min_words: Optional[int] = None
    max_words: Optional[int] = None
    table_policy: str = "forbidden"  # "forbidden" | "optional_if_justified" | "required"
    table_guidance: str = ""
    figure_policy: str = "none"  # "none" | "recommended" | "required"
    figure_guidance: str = ""
    required: bool = True
    notes: Optional[str] = None


class CanvasSectionSpec(BaseModel):
    id: str
    chapter_id: str
    chapter_title: str
    title: str
    order: int
    description: str
    guidance_points: List[str] = Field(default_factory=list)
    cames_concepts_required: List[str] = Field(default_factory=list)
    concepts_to_avoid: List[str] = Field(default_factory=list)
    constraints: CanvasConstraint = Field(default_factory=CanvasConstraint)
    supports_images: bool = False
    supports_tables: bool = False
    table_type: Optional[str] = None  # "variables" | "timeline" | "budget" | "abbreviations" | "synthesis" | "annexes"


class SupervisorInfo(BaseModel):
    """Structured supervisor identity rendered on the FMSB cover page."""

    name: str = ""
    rank: str = ""
    specialty: str = ""

    def to_display_lines(self) -> List[str]:
        return [val.strip() for val in (self.name, self.rank, self.specialty) if val and val.strip()]

    def has_content(self) -> bool:
        return bool(self.to_display_lines())


def _parse_legacy_supervisor(raw: str) -> SupervisorInfo:
    parts = [p.strip() for p in raw.splitlines() if p.strip()]
    if len(parts) <= 1 and "," in raw:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
    return SupervisorInfo(
        name=parts[0] if len(parts) > 0 else "",
        rank=parts[1] if len(parts) > 1 else "",
        specialty=" / ".join(parts[2:]) if len(parts) > 2 else "",
    )


class CoverMetadata(BaseModel):
    title: str = "Titre du protocole de recherche"
    degree_label: str = "Doctorat en Médecine Générale"
    university_fr: str = "UNIVERSITÉ DE YAOUNDÉ I"
    university_en: str = "THE UNIVERSITY OF YAOUNDÉ I"
    university_po_box: str = ""
    university_email: str = ""
    faculty_fr: str = "FACULTÉ DE MÉDECINE ET DES SCIENCES BIOMÉDICALES"
    faculty_en: str = "FACULTY OF MEDICINE AND BIOMEDICAL SCIENCES"
    faculty_po_box: str = ""
    faculty_email: str = ""
    university_logo_path: str = ""
    university_logo_url: str = ""
    faculty_logo_path: str = ""
    faculty_logo_url: str = ""
    department_fr: str = "SANTE PUBLIQUE"
    department_en: str = "PUBLIC HEALTH"
    author_name: str = ""
    matricule: str = ""
    student_level: str = "Étudiant(e) en 6ème année de médecine générale"
    student_phone: str = ""
    student_email: str = ""
    director: str = ""
    director_info: SupervisorInfo = Field(default_factory=SupervisorInfo)
    co_directors: List[str] = Field(default_factory=list)
    co_directors_info: List[SupervisorInfo] = Field(default_factory=list)
    academic_year: str = "2025-2026"
    study_period_label: str = ""
    host_institutions: List[str] = Field(default_factory=list)
    ethics_committees: List[str] = Field(
        default_factory=lambda: [
            "Comité Institutionnel d'Éthique et de la Recherche de la FMSB (CIER-FMSB)"
        ]
    )

    @model_validator(mode="after")
    def _sync_supervisors(self) -> "CoverMetadata":
        if self.director_info.has_content():
            self.director = ", ".join(self.director_info.to_display_lines())
        elif self.director.strip():
            self.director_info = _parse_legacy_supervisor(self.director)

        cleaned_codirs = [c for c in self.co_directors_info if c.has_content()]
        if cleaned_codirs:
            self.co_directors_info = cleaned_codirs
            self.co_directors = [", ".join(c.to_display_lines()) for c in cleaned_codirs]
        elif self.co_directors:
            parsed = [_parse_legacy_supervisor(s) for s in self.co_directors if s.strip()]
            self.co_directors_info = [c for c in parsed if c.has_content()]
        return self


class ReferenceItem(BaseModel):
    ref_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    order_index: Optional[int] = None
    pmid: Optional[str] = None
    pmcid: Optional[str] = None
    doi: Optional[str] = None
    source_platform: str = "pubmed"  # pubmed | europepmc | openalex | web
    regional_scope: Optional[str] = None  # "cameroun_fmsb" | "afrique_cames" | "international"
    african_source_label: Optional[str] = None  # ex. "Cameroun / FMSB" | "Afrique / CAMES"
    authors: List[str] = Field(default_factory=list)
    title: str
    journal: str = ""
    year: Optional[int] = None
    volume: str = ""
    issue: str = ""
    pages: str = ""
    url: str = ""
    abstract: str = ""
    vancouver_citation: str = ""
    verified: bool = False


class ArticleSegment(BaseModel):
    segment_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    ref_id: str
    target_section_ids: List[str] = Field(default_factory=list)
    summary: str
    key_quote_or_data: str = ""
    epidemiology_scope: Optional[str] = None  # monde | afrique | cameroun | other
    relevance_note: str = ""
    approved: bool = True


class ImageAsset(BaseModel):
    asset_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    target_section_id: str
    title: str
    caption: str = ""
    anchor_hint: str = ""
    source_url: str = ""
    source_ref_id: Optional[str] = None
    license_info: str = ""
    local_path: Optional[str] = None
    width_cm: float = 14.0
    approved: bool = False


class SectionTable(BaseModel):
    table_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    title: str
    anchor_hint: str = ""
    headers: List[str] = Field(default_factory=list)
    rows: List[List[str]] = Field(default_factory=list)


class VariableItem(BaseModel):
    name: str
    category: str = "Données sociodémographiques"
    role: str = "Indépendante"  # Dépendante | Indépendante | Confusion
    definition: str = ""
    measurement_scale: str = ""  # Qualitative nominale | Ordinale | Quantitative
    modalities: str = ""
    collection_tool: str = "Fiche de collecte"


class LiteratureSynthesisItem(BaseModel):
    authors_country_year: str
    study_design: str
    population: str
    main_results: str
    limits: str = ""


class TimelineItem(BaseModel):
    phase: str
    activity: str
    period: str
    responsible: str = "Investigateur principal"


class TimelineGridRow(BaseModel):
    activity: str
    active_periods: List[str] = Field(default_factory=list)


def default_timeline_periods() -> List[str]:
    return [
        "Mai 2026",
        "Juil 2026",
        "Oct 2026",
        "Mars 2027",
        "Avril 2027",
        "Mai 2027",
        "Juin 2027",
    ]


def default_timeline_rows() -> List[TimelineGridRow]:
    return [
        TimelineGridRow(activity="Rédaction du protocole", active_periods=["Mai 2026"]),
        TimelineGridRow(activity="Obtention des autorisations & clairance éthique", active_periods=["Juil 2026"]),
        TimelineGridRow(activity="Collecte des données", active_periods=["Oct 2026", "Mars 2027"]),
        TimelineGridRow(activity="Analyse des données", active_periods=["Avril 2027"]),
        TimelineGridRow(activity="Rédaction de thèse et corrections", active_periods=["Mai 2027"]),
        TimelineGridRow(activity="Dépôt, simulation et soutenance", active_periods=["Juin 2027"]),
    ]


class TimelineGrid(BaseModel):
    periods: List[str] = Field(default_factory=default_timeline_periods)
    rows: List[TimelineGridRow] = Field(default_factory=default_timeline_rows)


class BudgetItem(BaseModel):
    category: str = "Matériel & Logistique"
    designation: str
    quantity: float = 1.0
    unit_cost_fcfa: float = 0.0
    total_fcfa: float = 0.0


class AnnexesConfig(BaseModel):
    study_goal_short: str = ""
    include_data_collection_form: bool = True
    include_info_notice: bool = True
    include_consent_form: bool = True
    include_ethics_clearance_request: bool = True
    include_research_auth_requests: bool = True
    include_confidentiality_declaration: bool = True
    include_commitment_letter: bool = True


class FormattingRules(BaseModel):
    """Deterministic formatting rules enforced by Module 2 (never directly by LLM)."""

    page_size: str = "A4"
    margin_cm: float = 2.5
    font_family: str = "Times New Roman"
    body_font_size_pt: int = 12
    cover_title_min_pt: int = 18
    cover_title_max_pt: int = 18
    line_spacing: float = 1.5
    text_align: str = "justify"
    auto_toc: bool = True
    toc_depth: int = 2  # 2 = Sommaire CAMES (1 page, Niveaux 1 et 2) | 3 = Table analytique détaillée
    auto_list_of_tables: bool = True
    auto_list_of_figures: bool = True
    auto_abbreviations_table: bool = True
    citation_style: str = "Vancouver"
    page_numbering: str = "bottom-center"
    chapter_box_style: str = "rounded"  # "square" | "rounded" | "minimal"
    cover_border_style: str = "double"  # "double" | "single" | "none"
    cover_department_layout: str = "header_columns"  # "header_columns" | "above_title"
    border_color: str = "#6C2826"
    box_border_width_pt: float = 2.5
    box_corner_radius_pct: int = 18
    header_footer_rule_color: str = "#CBD5E1"
    header_footer_rule_width_pt: float = 0.5

    @model_validator(mode="after")
    def _sync_border_geometry(self) -> "FormattingRules":
        raw_dept_layout = (self.cover_department_layout or "header_columns").strip().lower()
        if raw_dept_layout not in {"header_columns", "above_title"}:
            raw_dept_layout = "header_columns"
        self.cover_department_layout = raw_dept_layout
        self.box_border_width_pt = max(0.75, min(8.0, float(self.box_border_width_pt or 2.5)))
        self.header_footer_rule_width_pt = max(
            0.25, min(6.0, float(self.header_footer_rule_width_pt or 0.5))
        )
        raw_hf_color = (self.header_footer_rule_color or "#CBD5E1").strip()
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", raw_hf_color):
            raw_hf_color = "#CBD5E1"
        self.header_footer_rule_color = raw_hf_color.upper()
        raw_corner = self.box_corner_radius_pct
        self.box_corner_radius_pct = max(
            0, min(50, int(18 if raw_corner is None else raw_corner))
        )
        if self.box_corner_radius_pct > 0 and self.chapter_box_style == "square":
            self.chapter_box_style = "rounded"
        elif self.box_corner_radius_pct == 0 and self.chapter_box_style == "rounded":
            self.chapter_box_style = "square"
        return self


class AbbreviationItem(BaseModel):
    acronym: str
    full_form: str


class DiagramNode(BaseModel):
    id: str
    shape: str = "rounded"  # "rectangle" | "rounded" | "circle" | "diamond"
    title: str
    items: List[str] = Field(default_factory=list)
    width_px: int = 380
    color: str = "#6C2826"


class DiagramConnection(BaseModel):
    from_id: str
    to_id: str
    label: str = ""
    style: str = "solid"  # "solid" | "dashed"


class SectionDiagram(BaseModel):
    diagram_id: str
    title: str
    caption: str = ""
    anchor_hint: str = ""
    layout: str = "vertical"  # "vertical" | "horizontal"
    nodes: List[DiagramNode] = Field(default_factory=list)
    connections: List[DiagramConnection] = Field(default_factory=list)


class InternalLibraryEntry(BaseModel):
    entry_id: str = Field(default_factory=lambda: str(uuid4())[:10])
    source_id: str
    source_filename: str = ""
    source_kind: str = "unreferenced_draft"  # thesis | article | institutional_report | unreferenced_draft
    citable: bool = False
    ref_id: Optional[str] = None
    vancouver_citation: str = ""
    content_markdown: str = ""
    tables_markdown: List[str] = Field(default_factory=list)
    image_asset_ids: List[str] = Field(default_factory=list)
    diagnostic_note: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class InternalSourceDocument(BaseModel):
    source_id: str = Field(default_factory=lambda: str(uuid4())[:10])
    filename: str
    source_kind: str = "unreferenced_draft"  # thesis | article | institutional_report | unreferenced_draft
    citable: bool = False
    ref_id: Optional[str] = None
    vancouver_citation: str = ""
    authors: List[str] = Field(default_factory=list)
    title: str = ""
    year: Optional[int] = None
    institution_or_journal: str = ""
    mapped_sections: List[str] = Field(default_factory=list)
    extracted_images_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SectionVersionEntry(BaseModel):
    version_id: str = Field(default_factory=lambda: str(uuid4())[:10])
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "generation"  # generation | fix | manual | copilot | restore
    note: str = ""
    content_markdown: str = ""
    cited_ref_ids: List[str] = Field(default_factory=list)
    word_count: int = 0


class SupervisorDirectiveItem(BaseModel):
    directive_id: str = Field(default_factory=lambda: str(uuid4())[:10])
    text: str
    source: str = "manual"
    active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CopilotProposal(BaseModel):
    section_id: str
    proposed_markdown: str
    summary: str = ""
    applied: bool = False


class CopilotMessage(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid4())[:10])
    role: str = "user"  # user | assistant
    content: str
    intent: str = "answer"
    model_used: str = ""
    active_module: str = ""
    active_section_id: Optional[str] = None
    proposal: Optional[CopilotProposal] = None
    suggested_directive: Optional[str] = None
    restore_version_id: Optional[str] = None
    target_module: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SectionState(BaseModel):
    section_id: str
    status: StepStatus = StepStatus.PENDING
    content_markdown: str = ""
    content_html: str = ""
    cited_ref_ids: List[str] = Field(default_factory=list)
    audit_notes: List[str] = Field(default_factory=list)
    user_notes: str = ""
    custom_tables: List[SectionTable] = Field(default_factory=list)
    custom_diagrams: List[SectionDiagram] = Field(default_factory=list)
    internal_library: List[InternalLibraryEntry] = Field(default_factory=list)
    versions: List[SectionVersionEntry] = Field(default_factory=list)
    suppress_structured_table: bool = False
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CRFFieldSpec(BaseModel):
    field_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    code: str = ""
    section_title: str = "SECTION A : IDENTIFICATION ET CARACTÉRISTIQUES SOCIODÉMOGRAPHIQUES"
    label: str
    field_type: str = "text"  # dichotomous | single_choice | multi_choice | number | date | text
    options: List[str] = Field(default_factory=list)
    unit: str = ""
    placeholder: str = ""
    sub_field_label: str = ""
    required: bool = False


class PatientRecord(BaseModel):
    record_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    patient_code: str = ""
    inclusion_date: str = ""
    hospital_site: str = ""
    investigator_name: str = ""
    values: Dict[str, Any] = Field(default_factory=dict)
    notes: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CoherenceAlert(BaseModel):
    alert_id: str
    axis: str
    source_section_id: str
    source_section_title: str
    target_section_id: str
    target_section_title: str
    title: str
    message: str
    suggested_instruction: str
    suggested_study_type: Optional[str] = None


class ProtocolProject(BaseModel):
    project_id: str = Field(default_factory=lambda: str(uuid4())[:12])
    owner_user_id: Optional[str] = None
    topic_locked: bool = False
    locked_title: str = ""
    locked_author_name: str = ""
    locked_matricule: str = ""
    language: str = "fr"
    name: str
    entry_mode: EntryMode = EntryMode.NEW_TOPIC
    research_idea: str = ""
    study_type: str = ""
    cover: CoverMetadata = Field(default_factory=CoverMetadata)
    sections: Dict[str, SectionState] = Field(default_factory=dict)
    coherence_alerts: List[CoherenceAlert] = Field(default_factory=list)
    references: List[ReferenceItem] = Field(default_factory=list)
    internal_sources: List[InternalSourceDocument] = Field(default_factory=list)
    article_segments: List[ArticleSegment] = Field(default_factory=list)
    images: List[ImageAsset] = Field(default_factory=list)
    abbreviations: List[AbbreviationItem] = Field(default_factory=list)
    variables: List[VariableItem] = Field(default_factory=list)
    crf_fields: List[CRFFieldSpec] = Field(default_factory=list)
    patient_records: List[PatientRecord] = Field(default_factory=list)
    literature_synthesis: List[LiteratureSynthesisItem] = Field(default_factory=list)
    timeline: List[TimelineItem] = Field(default_factory=list)
    timeline_grid: TimelineGrid = Field(default_factory=TimelineGrid)
    budget: List[BudgetItem] = Field(default_factory=list)
    annexes_config: AnnexesConfig = Field(default_factory=AnnexesConfig)
    formatting: FormattingRules = Field(default_factory=FormattingRules)
    supervisor_directives: List[SupervisorDirectiveItem] = Field(default_factory=list)
    copilot_messages: List[CopilotMessage] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CreateProjectRequest(BaseModel):
    name: str
    entry_mode: EntryMode = EntryMode.NEW_TOPIC
    research_idea: str = ""
    study_type: str = ""
    language: str = "fr"
    cover: Optional[CoverMetadata] = None


class UpdateLanguageRequest(BaseModel):
    language: str = "fr"


class UpdateSectionRequest(BaseModel):
    content_markdown: Optional[str] = None
    content_html: Optional[str] = None
    status: Optional[StepStatus] = None
    cited_ref_ids: Optional[List[str]] = None
    user_notes: Optional[str] = None


class UpdateFormattingRequest(BaseModel):
    formatting: FormattingRules


class UpdateStructuredDataRequest(BaseModel):
    abbreviations: Optional[List[AbbreviationItem]] = None
    variables: Optional[List[VariableItem]] = None
    literature_synthesis: Optional[List[LiteratureSynthesisItem]] = None
    timeline_grid: Optional[TimelineGrid] = None
    budget: Optional[List[BudgetItem]] = None
    annexes_config: Optional[AnnexesConfig] = None


class UpdateCRFSchemaRequest(BaseModel):
    crf_fields: List[CRFFieldSpec]
    sync_annexe1: bool = True


class GenerateCRFRequest(BaseModel):
    mode: str = "auto"  # "auto" | "generate" | "improve"
    user_instruction: str = ""


class GenerateSubAnnexRequest(BaseModel):
    user_instruction: str = ""
    bilingual: bool = False
    use_ai: bool = True


class ExtractCRFRequest(BaseModel):
    source_mode: Literal["auto", "section", "internal_source"] = "auto"
    source_id: Optional[str] = None
    use_ai: bool = True


class UpsertPatientRecordRequest(BaseModel):
    record_id: Optional[str] = None
    patient_code: str = ""
    inclusion_date: str = ""
    hospital_site: str = ""
    investigator_name: str = ""
    values: Dict[str, Any] = Field(default_factory=dict)
    notes: str = ""
