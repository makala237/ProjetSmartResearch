export interface LayoutBox {
  box_id: string;
  role: string;
  element_id?: string | null;
  text: string;
  sub_text?: string;
  x_pct: number;
  y_pct: number;
  w_pct: number;
  h_pct: number;
  font_size_pt: number;
  bold: boolean;
  italic: boolean;
  color_hex: string;
  align: "left" | "center" | "right" | string;
  valign: "top" | "middle" | "bottom" | string;
  bg_hex?: string | null;
  border_hex?: string | null;
  image_path?: string | null;
  step_index?: number | null;
}

export interface SlideElement {
  id: string;
  element_type:
    | "title"
    | "breadcrumb"
    | "bullet_main"
    | "bullet_sub"
    | "pill"
    | "stat_callout"
    | "procedure_step"
    | "media"
    | "table_mini"
    | "container_box"
    | "author"
    | "department"
    | "degree"
    | "directors"
    | "year";
  title?: string;
  content: string;
  sub_content?: string;
  template_id?: string;
  icon_or_badge?: string;
  image_path?: string;
  order: number;
}

export type SlideLayoutMode =
  | "standard"
  | "split_media"
  | "procedure_flow"
  | "stat_grid"
  | "radial_budget"
  | "plan_6pills"
  | "cover_fmsb"
  | "welcome"
  | "full_image_asis"
  | "acknowledgements";

export interface Slide {
  id: string;
  slide_index: number;
  slide_type:
    | "welcome"
    | "cover"
    | "plan"
    | "introduction"
    | "objective_general"
    | "objective_specific"
    | "methodology"
    | "chronogram"
    | "budget"
    | "transition"
    | "content"
    | "acknowledgements";
  section_key?: string;
  source_section_ids?: string[];
  title: string;
  subtitle?: string;
  breadcrumb?: string;
  elements: SlideElement[];
  presenter_notes: string;
  layout_template_id: string;
  layout_mode?: SlideLayoutMode | string;
  background_image_path?: string;
  render_boxes?: LayoutBox[];
  estimated_lines?: number;
  cames_warnings?: string[];
  locked_as_is?: boolean;
  is_validated: boolean;
}

export interface QueueDeckItem {
  id: string;
  section_id: string;
  section_title: string;
  element_type: string;
  text_content: string;
  suggested_template: string;
  suggested_slide_index?: number;
  presenter_cue: string;
}

export interface PresentationAuditIssue {
  slide_index: number;
  severity: "error" | "warning" | "info" | string;
  category: string;
  message: string;
  suggestion: string;
}

export interface PresentationAuditReport {
  score: number;
  total_slides: number;
  estimated_duration_sec: number;
  cames_compliant: boolean;
  model_used: string;
  summary: string;
  issues: PresentationAuditIssue[];
}

export interface PresentationDeck {
  project_id: string;
  title: string;
  max_slides?: number | null;
  slides: Slide[];
  current_slide_index: number;
  queue_items: QueueDeckItem[];
  theme_palette: string;
  version: string;
  decision_notes?: string;
  models_used?: Record<string, string>;
  last_audit?: PresentationAuditReport | null;
}
