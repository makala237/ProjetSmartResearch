export interface SlideElement {
  id: string;
  element_type:
    | "title"
    | "breadcrumb"
    | "bullet_main"
    | "bullet_sub"
    | "stat_callout"
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
  title: string;
  subtitle?: string;
  breadcrumb?: string;
  elements: SlideElement[];
  presenter_notes: string;
  layout_template_id: string;
  background_image_path?: string;
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

export interface PresentationDeck {
  project_id: string;
  title: string;
  max_slides?: number | null;
  slides: Slide[];
  current_slide_index: number;
  queue_items: QueueDeckItem[];
  theme_palette: string;
  version: string;
}
