from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class LayoutBox(BaseModel):
    """Unified normalized 16:9 layout geometry (shared between Web UI and python-pptx)."""

    box_id: str
    role: str
    element_id: Optional[str] = None
    text: str = ""
    sub_text: str = ""
    x_pct: float
    y_pct: float
    w_pct: float
    h_pct: float
    font_size_pt: float = 24.0
    bold: bool = False
    italic: bool = False
    color_hex: str = "#1E293B"
    align: str = "left"
    valign: str = "middle"
    bg_hex: Optional[str] = None
    border_hex: Optional[str] = None
    image_path: Optional[str] = None
    step_index: Optional[int] = None


class SlideElement(BaseModel):
    """An individual visual element on a slide (title, bullet, stat, image, table, procedure_step)."""

    id: str = Field(..., description="Unique identifier for this element")
    element_type: str = Field(
        ...,
        description="Type: title, breadcrumb, bullet_main, bullet_sub, stat_callout, procedure_step, media, table_mini, container_box, author, department, degree, directors, year",
    )
    title: Optional[str] = Field(None, description="Short title or label")
    content: str = Field("", description="Main text content (editable, without border)")
    sub_content: Optional[str] = Field(None, description="Secondary descriptor or metric unit")
    template_id: str = Field("tpl-bullet-main", description="Tailwind/PPTX shape template style id")
    icon_or_badge: Optional[str] = Field(None, description="Optional badge tag or symbol")
    image_path: Optional[str] = Field(None, description="Relative or absolute path to figure/image")
    order: int = Field(0, description="Display order on the slide")


class Slide(BaseModel):
    """A single slide within the unconstrained presentation deck."""

    id: str = Field(..., description="Unique slide id (e.g., slide-1, slide-2)")
    slide_index: int = Field(..., ge=1, description="Slide index (1, 2, ...)")
    slide_type: str = Field(
        "content",
        description="welcome, cover, plan, introduction, objective_general, objective_specific, methodology, chronogram, budget, acknowledgements, transition, content",
    )
    section_key: Optional[str] = Field(None, description="Associated protocol section or chapter id")
    source_section_ids: List[str] = Field(
        default_factory=list,
        description="Mapped FMSB canvas section IDs used as source content",
    )
    title: str = Field("", description="Slide main headline (>= 26pt)")
    subtitle: Optional[str] = Field(None, description="Optional sub-headline")
    breadcrumb: Optional[str] = Field(None, description="Category indicator (e.g. MÉTHODOLOGIE > POPULATION)")
    elements: List[SlideElement] = Field(default_factory=list, description="Visual elements on this slide")
    presenter_notes: str = Field("", description="Speaker oral guidance notes (45-60s speech)")
    layout_template_id: str = Field("tpl-standard-card", description="Card / Bento layout arrangement")
    layout_mode: str = Field(
        "standard",
        description="standard | split_media | procedure_flow | stat_grid | radial_budget | plan_6pills | cover_fmsb | welcome | full_image_asis | acknowledgements",
    )
    background_image_path: Optional[str] = Field(None, description="Optional background image template path")
    render_boxes: List[LayoutBox] = Field(default_factory=list, description="Computed normalized layout boxes")
    estimated_lines: int = Field(0, description="Estimated visual body lines at target font size")
    cames_warnings: List[str] = Field(default_factory=list, description="Detected CAMES/overflow alerts")
    locked_as_is: bool = Field(False, description="True for slides deposited verbatim without text overlay")
    is_validated: bool = Field(False, description="Whether the user approved this slide")


class QueueDeckItem(BaseModel):
    """An element extracted from protocol sections waiting in the drafting queue."""

    id: str
    section_id: str
    section_title: str
    element_type: str
    text_content: str
    suggested_template: str
    suggested_slide_index: Optional[int] = None
    presenter_cue: str = ""


class PresentationAuditIssue(BaseModel):
    """Single audit finding from deterministic CAMES checks or AI reviewer."""

    slide_index: int
    severity: str  # error | warning | info
    category: str  # overflow | font_size | duration | fidelity | missing_content | budget
    message: str
    suggestion: str = ""


class PresentationAuditReport(BaseModel):
    """Global CAMES / FMSB presentation compliance and quality report."""

    score: int
    total_slides: int
    estimated_duration_sec: int
    cames_compliant: bool
    model_used: str
    summary: str
    issues: List[PresentationAuditIssue] = Field(default_factory=list)


class PresentationDeck(BaseModel):
    """Presentation Deck with full granular slides and physical geometry plans."""

    project_id: str
    title: str = ""
    max_slides: Optional[int] = None
    slides: List[Slide] = Field(default_factory=list)
    current_slide_index: int = 1
    queue_items: List[QueueDeckItem] = Field(default_factory=list)
    theme_palette: str = "fmsb_burgundy"
    version: str = "3.0.0"
    decision_notes: str = ""
    models_used: Dict[str, str] = Field(default_factory=dict)
    last_audit: Optional[PresentationAuditReport] = None
