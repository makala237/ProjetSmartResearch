export type EntryMode = "new_topic" | "existing_protocol";

export type StepStatus = "pending" | "in_progress" | "needs_revision" | "validated";

export interface CanvasConstraint {
  min_pages?: number | null;
  max_pages?: number | null;
  min_references?: number | null;
  max_references?: number | null;
  min_words?: number | null;
  max_words?: number | null;
  table_policy?: "forbidden" | "optional_if_justified" | "required" | string;
  table_guidance?: string;
  figure_policy?: "none" | "recommended" | "required" | string;
  figure_guidance?: string;
  required: boolean;
  notes?: string | null;
}

export interface CanvasSectionSpec {
  id: string;
  chapter_id: string;
  chapter_title: string;
  title: string;
  order: number;
  description: string;
  guidance_points: string[];
  cames_concepts_required?: string[];
  concepts_to_avoid?: string[];
  constraints: CanvasConstraint;
  supports_images: boolean;
  supports_tables: boolean;
  table_type?: string | null;
}

export interface SupervisorInfo {
  name: string;
  rank: string;
  specialty: string;
}

export interface CoverMetadata {
  title: string;
  degree_label: string;
  university_fr?: string;
  university_en?: string;
  university_po_box?: string;
  university_email?: string;
  faculty_fr?: string;
  faculty_en?: string;
  faculty_po_box?: string;
  faculty_email?: string;
  university_logo_path?: string;
  university_logo_url?: string;
  faculty_logo_path?: string;
  faculty_logo_url?: string;
  department_fr: string;
  department_en: string;
  author_name: string;
  matricule: string;
  student_level: string;
  student_phone: string;
  student_email: string;
  director: string;
  director_info?: SupervisorInfo;
  co_directors: string[];
  co_directors_info?: SupervisorInfo[];
  academic_year: string;
  study_period_label: string;
  host_institutions: string[];
  ethics_committees: string[];
}

export type ResearchScope = "global" | "hybrid_africa" | "africa_only";

export interface ReferenceItem {
  ref_id: string;
  order_index?: number | null;
  pmid?: string | null;
  pmcid?: string | null;
  doi?: string | null;
  source_platform: string;
  regional_scope?: "cameroun_fmsb" | "afrique_cames" | "international" | null;
  african_source_label?: string | null;
  authors: string[];
  title: string;
  journal: string;
  year?: number | null;
  volume: string;
  issue: string;
  pages: string;
  url: string;
  abstract: string;
  vancouver_citation: string;
  verified: boolean;
}

export interface ArticleSegment {
  segment_id: string;
  ref_id: string;
  target_section_ids: string[];
  summary: string;
  key_quote_or_data: string;
  epidemiology_scope?: string | null;
  relevance_note: string;
  approved: boolean;
}

export interface ImageAsset {
  asset_id: string;
  target_section_id: string;
  anchor_hint?: string;
  title: string;
  caption: string;
  source_url: string;
  local_path?: string | null;
  source_ref_id?: string | null;
  license_info: string;
  width_cm: number;
  approved: boolean;
}

export interface SectionTable {
  table_id: string;
  title: string;
  anchor_hint: string;
  headers: string[];
  rows: string[][];
}

export type DiagramLayoutType =
  | "horizontal_flow"
  | "prisma_exclusion"
  | "decision_flowchart"
  | "branching_tree"
  | "parallel_converge"
  | "vertical"
  | "horizontal";

export interface DiagramNode {
  id: string;
  shape: "rectangle" | "rounded" | "pill" | "oval" | "circle" | "diamond" | "cylinder";
  title: string;
  items: string[];
  width_px?: number;
  color?: string;
}

export interface DiagramConnection {
  from_id: string;
  to_id: string;
  label?: string;
  style?: "solid" | "dashed";
}

export interface SectionDiagram {
  diagram_id: string;
  title: string;
  caption: string;
  anchor_hint: string;
  layout: DiagramLayoutType;
  color_theme?: string;
  nodes: DiagramNode[];
  connections: DiagramConnection[];
}

export interface VariableItem {
  name: string;
  category: string;
  role: string;
  definition: string;
  measurement_scale: string;
  modalities: string;
  collection_tool: string;
}

export interface LiteratureSynthesisItem {
  authors_country_year: string;
  study_design: string;
  population: string;
  main_results: string;
  limits: string;
}

export interface TimelineGridRow {
  activity: string;
  active_periods: string[];
}

export interface TimelineGrid {
  periods: string[];
  rows: TimelineGridRow[];
}

export interface BudgetItem {
  category: string;
  designation: string;
  quantity: number;
  unit_cost_fcfa: number;
  total_fcfa: number;
}

export interface AnnexesConfig {
  study_goal_short: string;
  include_data_collection_form: boolean;
  include_info_notice: boolean;
  include_consent_form: boolean;
  include_ethics_clearance_request: boolean;
  include_research_auth_requests: boolean;
  include_confidentiality_declaration: boolean;
  include_commitment_letter: boolean;
}

export interface AbbreviationItem {
  acronym: string;
  full_form: string;
}

export interface FormattingRules {
  page_size: string;
  margin_cm: number;
  font_family: string;
  body_font_size_pt: number;
  cover_title_min_pt: number;
  cover_title_max_pt: number;
  line_spacing: number;
  text_align: "justify" | "left" | "center" | "right";
  auto_toc: boolean;
  toc_depth?: number;
  auto_list_of_tables: boolean;
  auto_list_of_figures: boolean;
  auto_abbreviations_table: boolean;
  citation_style: string;
  page_numbering: string;
  chapter_box_style?: "rounded" | "square" | "minimal";
  cover_border_style?: "double" | "single" | "none";
  border_color?: string;
  box_border_width_pt?: number;
  box_corner_radius_pct?: number;
  header_footer_rule_color?: string;
  header_footer_rule_width_pt?: number;
  header_font_size_pt?: number;
  header_font_weight?: "normal" | "bold";
  header_italic?: boolean;
  header_align?: "left" | "center" | "right" | "justify";
  header_show_full_title?: boolean;
  cover_department_layout?: "header_columns" | "above_title";
}

export type ImportMode = "skeleton" | "internal_source";

export interface InternalLibraryEntry {
  entry_id: string;
  source_id: string;
  source_filename: string;
  source_kind: "thesis" | "article" | "institutional_report" | "unreferenced_draft" | string;
  citable: boolean;
  ref_id?: string | null;
  vancouver_citation: string;
  content_markdown: string;
  tables_markdown: string[];
  image_asset_ids: string[];
  diagnostic_note: string;
  created_at: string;
}

export interface InternalSourceDocument {
  source_id: string;
  filename: string;
  source_kind: "thesis" | "article" | "institutional_report" | "unreferenced_draft" | string;
  citable: boolean;
  ref_id?: string | null;
  vancouver_citation: string;
  authors: string[];
  title: string;
  year?: number | null;
  institution_or_journal: string;
  mapped_sections: string[];
  extracted_images_count: number;
  created_at: string;
}

export interface SectionVersionEntry {
  version_id: string;
  created_at: string;
  source: string;
  note: string;
  content_markdown: string;
  cited_ref_ids: string[];
  word_count: number;
}

export interface SupervisorDirectiveItem {
  directive_id: string;
  text: string;
  source: string;
  section_id?: string | null;
  media_filename?: string;
  timestamp_hint?: string;
  active: boolean;
  created_at: string;
}

export interface TextPatchOperation {
  target_text: string;
  replacement_text: string;
  reason: string;
}

export interface AuditWaiverEntry {
  waiver_id: string;
  issue_text: string;
  justification: string;
  created_by_user_id?: string | null;
  created_at: string;
}

export interface CopilotProposal {
  section_id: string;
  proposed_markdown: string;
  summary: string;
  patches?: TextPatchOperation[];
  applied: boolean;
}

export interface CopilotAppAction {
  action_id: string;
  action_type: string;
  label: string;
  summary: string;
  params: Record<string, unknown>;
  applied: boolean;
  result_summary?: string;
}

export interface CopilotMessage {
  message_id: string;
  owner_user_id?: string | null;
  role: "user" | "assistant" | string;
  content: string;
  intent: string;
  model_used: string;
  active_module: string;
  active_section_id?: string | null;
  proposal?: CopilotProposal | null;
  app_action?: CopilotAppAction | null;
  suggested_directive?: string | null;
  restore_version_id?: string | null;
  target_module?: string | null;
  tools_used?: string[];
  created_at: string;
}

export interface CopilotStatusResponse {
  text_models: string[];
  voice_models: string[];
  directives_count: number;
  messages: CopilotMessage[];
}

export interface CopilotChatResponse {
  assistant_message: CopilotMessage;
  project: ProtocolProject;
  model_used: string;
  active_text_models: string[];
  active_voice_models: string[];
}

export interface SectionState {
  section_id: string;
  status: StepStatus;
  content_markdown: string;
  content_html: string;
  cited_ref_ids: string[];
  audit_notes: string[];
  audit_waivers?: AuditWaiverEntry[];
  custom_tables?: SectionTable[];
  custom_diagrams?: SectionDiagram[];
  internal_library?: InternalLibraryEntry[];
  versions?: SectionVersionEntry[];
  suppress_structured_table?: boolean;
  user_notes: string;
  updated_at: string;
}

export type CRFFieldType =
  | "dichotomous"
  | "single_choice"
  | "multi_choice"
  | "number"
  | "date"
  | "text";

export interface CRFFieldSpec {
  field_id: string;
  code: string;
  section_title: string;
  label: string;
  field_type: CRFFieldType | string;
  options: string[];
  unit: string;
  placeholder: string;
  sub_field_label: string;
  required: boolean;
}

export interface PatientRecord {
  record_id: string;
  patient_code: string;
  inclusion_date: string;
  hospital_site: string;
  investigator_name: string;
  values: Record<string, string | string[] | number | boolean | null>;
  notes: string;
  created_at: string;
  updated_at: string;
}

export interface ProtocolProject {
  project_id: string;
  owner_user_id?: string | null;
  topic_locked?: boolean;
  locked_title?: string;
  locked_author_name?: string;
  locked_matricule?: string;
  name: string;
  language?: "fr" | "en";
  entry_mode: EntryMode;
  research_idea: string;
  study_type: string;
  cover: CoverMetadata;
  sections: Record<string, SectionState>;
  coherence_alerts?: CoherenceAlert[];
  references: ReferenceItem[];
  internal_sources?: InternalSourceDocument[];
  article_segments: ArticleSegment[];
  images: ImageAsset[];
  abbreviations: AbbreviationItem[];
  variables: VariableItem[];
  crf_fields?: CRFFieldSpec[];
  patient_records?: PatientRecord[];
  literature_synthesis: LiteratureSynthesisItem[];
  timeline_grid: TimelineGrid;
  budget: BudgetItem[];
  annexes_config: AnnexesConfig;
  formatting: FormattingRules;
  supervisor_directives?: SupervisorDirectiveItem[];
  copilot_messages?: CopilotMessage[];
  created_at: string;
  updated_at: string;
}

export interface PipelineProgressStep {
  key: string;
  label: string;
  detail: string;
  status: "running" | "done" | "error";
  timestamp: string;
}

export interface PipelineProgressSnapshot {
  task_id: string;
  project_id: string;
  operation: string;
  lang: "fr" | "en";
  title: string;
  status: "running" | "done" | "error";
  progress_pct: number;
  active_model: string;
  fallback_triggered: boolean;
  error_message: string;
  elapsed_sec: number;
  steps: PipelineProgressStep[];
}

export interface ModelsStatus {
  connected: boolean;
  error?: string;
  configured_chains: Record<string, string[]>;
  available_models: Array<{
    id: string;
    display_name: string;
    description: string;
  }>;
}

export interface ResearchReport {
  planner_model: string;
  plan: {
    pubmed_queries: string[];
    local_epidemiology_query: string;
    target_sites: string[];
  };
  search_scope?: ResearchScope;
  web_summary: string;
  added_references_count: number;
  african_references_count?: number;
  total_references_count: number;
  added_images_count: number;
  source_errors: string[];
}

export interface SegmentReport {
  processed_articles: number;
  added_segments: number;
  total_segments: number;
  models_used: string[];
  errors: string[];
}

export interface SectionOrchestrationTrace {
  action: string;
  initial_text_present: boolean;
  memory_sections_count: number;
  gap_model?: string | null;
  gap_needs_external_info: boolean;
  decision_model: string;
  antigravity_called: boolean;
  added_references_count: number;
  added_images_count: number;
  routing_summary: string;
}

export interface DraftResult {
  model_used: string;
  content_markdown: string;
  content_html?: string;
  cited_ref_ids: string[];
  assistant_commentary: string;
  deterministic_notes: string[];
  orchestration_trace?: SectionOrchestrationTrace;
  used_internal_library_only?: boolean;
  internal_library_entries_used?: number;
  can_enrich_externally?: boolean;
}

export interface AuditResult {
  model_used?: string | null;
  compliant: boolean;
  issues: string[];
  recommendations: string[];
  orchestration_trace?: SectionOrchestrationTrace;
}

export interface ExtractedImportImageSummary {
  asset_id: string;
  title: string;
  target_section_id: string;
  source_url: string;
  caption?: string;
  anchor_hint?: string;
}

export interface ImportReport {
  model_used: string;
  filename: string;
  import_mode?: ImportMode | string;
  extracted_chars: number;
  total_blocks: number;
  global_summary: string;
  mapped_sections_count: number;
  extracted_references_count?: number;
  extracted_images_count?: number;
  extracted_images?: ExtractedImportImageSummary[];
  source_document?: InternalSourceDocument | null;
  internal_entries_added?: number;
  citable?: boolean;
  vancouver_citation?: string;
  crf_detected?: boolean;
  crf_fields_count?: number;
  crf_detection?: CRFBoundaryDetection | null;
  detected_sub_annexes?: string[];
}

export interface GlobalAuditCheck {
  id: string;
  label: string;
  status: "ok" | "warning" | "error";
  summary: string;
  details: string[];
}

export interface GlobalAuditReport {
  ready_for_print: boolean;
  score_percent: number;
  validated_sections_count: number;
  drafted_sections_count: number;
  total_sections_count: number;
  checks: GlobalAuditCheck[];
  missing_acronyms: string[];
}

export interface SyncStructuredReport {
  added_abbreviations: number;
  added_variables: number;
  added_literature_rows: number;
  added_budget_items: number;
}

export interface HumanizeMetrics {
  sentence_count: number;
  avg_sentence_words: number;
  burstiness_sd: number;
  remaining_cliches: number;
  em_dash_count: number;
  invisible_unicode_count: number;
  human_style_score: number;
}

export interface HumanizeSectionResult {
  section_id: string;
  skipped: boolean;
  reason?: string;
  model_used: string;
  content_markdown: string;
  content_html?: string;
  before_metrics: HumanizeMetrics;
  after_metrics: HumanizeMetrics;
  cliches_removed: number;
  citations_preserved: boolean;
  style_adjustments_summary?: string;
  remaining_notes?: string[];
}

export interface HumanizeProtocolReport {
  sections_humanized: number;
  total_cliches_removed: number;
  average_before_score: number;
  average_human_score: number;
  citations_preserved: boolean;
  models_used: string[];
  model_used: string;
  author_signature: string;
  details: Array<{
    section_id: string;
    title: string;
    model_used: string;
    before_score: number;
    after_score: number;
    burstiness_sd: number;
    cliches_removed: number;
    citations_preserved: boolean;
  }>;
}

export interface CoherenceAlert {
  alert_id: string;
  axis: string;
  source_section_id: string;
  source_section_title: string;
  target_section_id: string;
  target_section_title: string;
  title: string;
  message: string;
  suggested_instruction: string;
  suggested_study_type?: string | null;
}

export interface HarmonizeSectionResult {
  harmonized: boolean;
  section_id: string;
  resolved_axis?: string;
  model_used: string;
  content_markdown: string;
  content_html?: string;
  study_type?: string;
  remaining_alerts_count: number;
  message: string;
}

export interface CRFBoundaryDetection {
  found: boolean;
  start_block_id?: number | null;
  end_block_id?: number | null;
  start_excerpt: string;
  end_excerpt: string;
  confidence: number;
  detected_title: string;
  method: string;
  excluded_neighbor_annexes: string[];
  notes: string;
}

export interface CRFSyncReport {
  preserved_count: number;
  modified_count: number;
  added_count: number;
  removed_count: number;
  total_fields: number;
  sections_count: number;
  types_breakdown: Record<string, number>;
  cames_warnings: string[];
  source_origin?: string;
}

export interface CRFExtractAndSyncResult {
  project: ProtocolProject;
  crf_fields: CRFFieldSpec[];
  annexe1_html: string;
  detection: CRFBoundaryDetection;
  sync_report: CRFSyncReport;
  model_used: string;
}
