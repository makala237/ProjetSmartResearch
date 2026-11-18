from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class SlideElement(BaseModel):
    """An individual visual element on a slide (title, bullet, stat, image, table)."""

    id: str = Field(..., description="Unique identifier for this element")
    element_type: str = Field(
        ...,
        description="Type: title, breadcrumb, bullet_main, bullet_sub, stat_callout, media, table_mini, container_box",
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
        description="cover, plan, transition, content, acknowledgements",
    )
    section_key: Optional[str] = Field(None, description="Associated protocol section or chapter id")
    title: str = Field("", description="Slide main headline (>= 30pt)")
    subtitle: Optional[str] = Field(None, description="Optional sub-headline")
    breadcrumb: Optional[str] = Field(None, description="Category indicator (e.g. MÉTHODOLOGIE > POPULATION)")
    elements: List[SlideElement] = Field(default_factory=list, description="Visual elements on this slide")
    presenter_notes: str = Field("", description="Speaker oral guidance notes (45-60s speech)")
    layout_template_id: str = Field("tpl-standard-card", description="Card / Bento layout arrangement")
    background_image_path: Optional[str] = Field(None, description="Optional background image template path")
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


class PresentationDeck(BaseModel):
    """Presentation Deck with full granular slides for each major heading."""

    project_id: str
    title: str = ""
    max_slides: Optional[int] = None
    slides: List[Slide] = Field(default_factory=list)
    current_slide_index: int = 1
    queue_items: List[QueueDeckItem] = Field(default_factory=list)
    theme_palette: str = "fmsb_burgundy"
    version: str = "2.0.0"
