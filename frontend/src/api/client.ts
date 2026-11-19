import type {
  AbbreviationItem,
  AnnexesConfig,
  AuditResult,
  BudgetItem,
  CanvasSectionSpec,
  CoverMetadata,
  CRFFieldSpec,
  DiagramConnection,
  DiagramNode,
  DraftResult,
  EntryMode,
  FormattingRules,
  GlobalAuditReport,
  ImportReport,
  LiteratureSynthesisItem,
  ModelsStatus,
  PatientRecord,
  ProtocolProject,
  ResearchReport,
  SectionOrchestrationTrace,
  SegmentReport,
  StepStatus,
  SyncStructuredReport,
  TimelineGrid,
  VariableItem,
} from "../types/protocol";

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, {
      headers: { "Content-Type": "application/json", ...(options?.headers || {}) },
      ...options,
    });
  } catch (err) {
    if (err instanceof TypeError) {
      throw new Error(
        "Connexion au serveur backend impossible (port 8001). Vérifiez que l'API FastAPI est démarrée."
      );
    }
    throw err;
  }
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail || `Erreur HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  getCanvas: () =>
    request<{ version: string; total_steps: number; sections: CanvasSectionSpec[] }>(
      "/api/canvas"
    ),

  getModelsStatus: () => request<ModelsStatus>("/api/models"),

  listProjects: () => request<ProtocolProject[]>("/api/projects"),

  createProject: (payload: {
    name: string;
    entry_mode: EntryMode;
    research_idea: string;
    study_type: string;
  }) =>
    request<ProtocolProject>("/api/projects", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  updateCover: (projectId: string, cover: CoverMetadata) =>
    request<ProtocolProject>(`/api/projects/${projectId}/cover`, {
      method: "PATCH",
      body: JSON.stringify(cover),
    }),

  updateSection: (
    projectId: string,
    sectionId: string,
    payload: {
      content_markdown?: string;
      content_html?: string;
      status?: StepStatus;
      user_notes?: string;
    }
  ) =>
    request<ProtocolProject>(`/api/projects/${projectId}/sections/${sectionId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  updateFormatting: (projectId: string, formatting: FormattingRules) =>
    request<ProtocolProject>(`/api/projects/${projectId}/formatting`, {
      method: "PATCH",
      body: JSON.stringify({ formatting }),
    }),

  updateStructuredData: (
    projectId: string,
    payload: {
      abbreviations?: AbbreviationItem[];
      variables?: VariableItem[];
      literature_synthesis?: LiteratureSynthesisItem[];
      timeline_grid?: TimelineGrid;
      budget?: BudgetItem[];
      annexes_config?: AnnexesConfig;
    }
  ) =>
    request<ProtocolProject>(`/api/projects/${projectId}/structured`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  previewAnnexes: (projectId: string) =>
    request<{ annexes: Array<Record<string, any>> }>(
      `/api/projects/${projectId}/annexes/preview`
    ),

  runResearch: (
    projectId: string,
    payload: {
      custom_query?: string;
      max_per_source?: number;
      include_web_grounding?: boolean;
    }
  ) =>
    request<{ project: ProtocolProject; report: ResearchReport }>(
      `/api/projects/${projectId}/research`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    ),

  segmentArticles: (projectId: string, limit = 8) =>
    request<{ project: ProtocolProject; report: SegmentReport }>(
      `/api/projects/${projectId}/segment`,
      {
        method: "POST",
        body: JSON.stringify({ limit, only_unsegmented: true }),
      }
    ),

  toggleSegment: (projectId: string, segmentId: string, approved: boolean) =>
    request<ProtocolProject>(`/api/projects/${projectId}/segments/${segmentId}`, {
      method: "PATCH",
      body: JSON.stringify({ approved }),
    }),

  toggleImage: (
    projectId: string,
    assetId: string,
    payload: {
      approved?: boolean;
      target_section_id?: string;
      caption?: string;
      title?: string;
      anchor_hint?: string;
    }
  ) =>
    request<ProtocolProject>(`/api/projects/${projectId}/images/${assetId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  uploadImage: async (
    projectId: string,
    params: {
      file: File;
      target_section_id: string;
      title: string;
      caption: string;
      anchor_hint: string;
    }
  ): Promise<ProtocolProject> => {
    const form = new FormData();
    form.append("file", params.file);
    form.append("target_section_id", params.target_section_id);
    form.append("title", params.title);
    form.append("caption", params.caption);
    form.append("anchor_hint", params.anchor_hint);
    const res = await fetch(`/api/projects/${projectId}/images/upload`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(detail || `Erreur HTTP ${res.status}`);
    }
    return res.json();
  },

  deleteImage: (projectId: string, assetId: string) =>
    request<ProtocolProject>(`/api/projects/${projectId}/images/${assetId}`, {
      method: "DELETE",
    }),

  draftSection: (projectId: string, sectionId: string, instruction?: string) =>
    request<{ project: ProtocolProject; result: DraftResult }>(
      `/api/projects/${projectId}/sections/${sectionId}/draft`,
      {
        method: "POST",
        body: JSON.stringify({ instruction: instruction || null }),
      }
    ),

  auditSection: (projectId: string, sectionId: string) =>
    request<{ project: ProtocolProject; result: AuditResult }>(
      `/api/projects/${projectId}/sections/${sectionId}/audit`,
      {
        method: "POST",
      }
    ),

  fixSection: (projectId: string, sectionId: string) =>
    request<{ project: ProtocolProject; result: DraftResult }>(
      `/api/projects/${projectId}/sections/${sectionId}/fix`,
      {
        method: "POST",
      }
    ),

  runSectionAntigravity: (
    projectId: string,
    sectionId: string,
    instruction?: string,
    captureImages = true
  ) =>
    request<{
      project: ProtocolProject;
      orchestration_trace: SectionOrchestrationTrace;
      antigravity_report: Record<string, unknown>;
    }>(`/api/projects/${projectId}/sections/${sectionId}/antigravity`, {
      method: "POST",
      body: JSON.stringify({
        instruction: instruction || null,
        capture_images: captureImages,
      }),
    }),

  getWorkingMemory: (projectId: string) =>
    request<{
      project_id: string;
      file_path: string;
      sections_count: number;
      memory_map: Record<string, string>;
      markdown: string;
    }>(`/api/projects/${projectId}/working-memory`),

  refreshWorkingMemory: (projectId: string) =>
    request<{
      updated_sections: string[];
      updated_count: number;
      live_model: string;
      file_path: string;
      markdown: string;
    }>(`/api/projects/${projectId}/working-memory/refresh`, {
      method: "POST",
    }),

  suggestSectionTable: (
    projectId: string,
    sectionId: string,
    payload: { title: string; goal: string; anchor_hint?: string }
  ) =>
    request<{
      suggestion: {
        model_used: string;
        title: string;
        headers: string[];
        rows: string[][];
        rationale: string;
      };
    }>(`/api/projects/${projectId}/sections/${sectionId}/suggest-table`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  addSectionTable: (
    projectId: string,
    sectionId: string,
    payload: {
      title: string;
      anchor_hint: string;
      headers: string[];
      rows: string[][];
    }
  ) =>
    request<ProtocolProject>(`/api/projects/${projectId}/sections/${sectionId}/tables`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  deleteSectionTable: (projectId: string, sectionId: string, tableId: string) =>
    request<ProtocolProject>(
      `/api/projects/${projectId}/sections/${sectionId}/tables/${tableId}`,
      {
        method: "DELETE",
      }
    ),

  syncMediaTransport: (projectId: string) =>
    request<{
      project: ProtocolProject;
      image_report: Record<string, unknown>;
      table_report: Record<string, unknown>;
    }>(`/api/projects/${projectId}/media/transport-sync`, {
      method: "POST",
    }),

  suggestSectionDiagram: (
    projectId: string,
    sectionId: string,
    payload: { title: string; goal: string; anchor_hint?: string }
  ) =>
    request<{
      suggestion: {
        model_used: string;
        title: string;
        caption: string;
        layout: "vertical" | "horizontal";
        nodes: DiagramNode[];
        connections: DiagramConnection[];
      };
    }>(`/api/projects/${projectId}/sections/${sectionId}/suggest-diagram`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  addSectionDiagram: (
    projectId: string,
    sectionId: string,
    payload: {
      title: string;
      caption: string;
      anchor_hint: string;
      layout: "vertical" | "horizontal";
      nodes: DiagramNode[];
      connections: DiagramConnection[];
    }
  ) =>
    request<ProtocolProject>(
      `/api/projects/${projectId}/sections/${sectionId}/diagrams`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    ),

  deleteSectionDiagram: (
    projectId: string,
    sectionId: string,
    diagramId: string
  ) =>
    request<ProtocolProject>(
      `/api/projects/${projectId}/sections/${sectionId}/diagrams/${diagramId}`,
      {
        method: "DELETE",
      }
    ),

  diagramSvgUrl: (projectId: string, sectionId: string, diagramId: string) =>
    `/api/projects/${projectId}/sections/${sectionId}/diagrams/${diagramId}/svg`,

  importProtocol: async (
    projectId: string,
    file: File
  ): Promise<{ project: ProtocolProject; report: ImportReport }> => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`/api/projects/${projectId}/import`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) {
      const detail = await res.text();
      throw new Error(detail || `Erreur HTTP ${res.status}`);
    }
    return res.json();
  },

  exportDocxUrl: (projectId: string) => `/api/projects/${projectId}/export/docx`,

  exportPdfUrl: (projectId: string) => `/api/projects/${projectId}/export/pdf`,

  runGlobalAudit: (projectId: string) =>
    request<GlobalAuditReport>(`/api/projects/${projectId}/global-audit`, {
      method: "POST",
    }),

  syncStructured: (projectId: string) =>
    request<{ project: ProtocolProject; report: SyncStructuredReport }>(
      `/api/projects/${projectId}/sync-structured`,
      { method: "POST" }
    ),

  generateToc: (projectId: string) =>
    request<{
      project: ProtocolProject;
      entries_count: number;
      abbreviations_count?: number;
      tables_count?: number;
      figures_count?: number;
      pagination_summary?: {
        model_used: string;
        decision_notes: string;
        total_roman_pages: number;
        total_arabic_pages: number;
        usable_width_cm: number;
        usable_height_cm: number;
        figures_checked: number;
      };
    }>(
      `/api/projects/${projectId}/generate-toc`,
      { method: "POST" }
    ),

  validateVancouverReferences: (projectId: string) =>
    request<{
      project: ProtocolProject;
      references_count: number;
      corrected_count: number;
      decision_model: string;
      decision_summary: string;
    }>(
      `/api/projects/${projectId}/validate-references`,
      { method: "POST" }
    ),

  translateDepartment: (payload: {
    text: string;
    source_lang: "fr" | "en";
    target_lang: "fr" | "en";
  }) =>
    request<{ translated_text: string; model_used: string }>(
      "/api/translate-department",
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    ),

  syncProjectCrf: (projectId: string, forceExtract = false) =>
    request<{
      project: ProtocolProject;
      crf_fields: CRFFieldSpec[];
      annexe1_html: string;
    }>(`/api/projects/${projectId}/crf/sync?force_extract=${forceExtract}`, {
      method: "POST",
    }),

  updateProjectCrfSchema: (
    projectId: string,
    crf_fields: CRFFieldSpec[],
    sync_annexe1 = true
  ) =>
    request<{
      project: ProtocolProject;
      crf_fields: CRFFieldSpec[];
      annexe1_html: string;
    }>(`/api/projects/${projectId}/crf/schema`, {
      method: "PUT",
      body: JSON.stringify({ crf_fields, sync_annexe1 }),
    }),

  getCrfPreviewHtml: (projectId: string) =>
    request<{ annexe1_html: string; crf_fields: CRFFieldSpec[] }>(
      `/api/projects/${projectId}/crf/preview-html`
    ),

  upsertPatientRecord: (
    projectId: string,
    payload: {
      record_id?: string | null;
      patient_code?: string;
      inclusion_date?: string;
      hospital_site?: string;
      investigator_name?: string;
      values: Record<string, string | string[] | number | boolean | null>;
      notes?: string;
    }
  ) =>
    request<{ project: ProtocolProject; record: PatientRecord }>(
      `/api/projects/${projectId}/patients`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    ),

  deletePatientRecord: (projectId: string, recordId: string) =>
    request<ProtocolProject>(
      `/api/projects/${projectId}/patients/${recordId}`,
      { method: "DELETE" }
    ),

  exportPatientsCsvUrl: (projectId: string) =>
    `/api/projects/${projectId}/patients/export.csv`,

  // Module 4: Diaporama & Soutenance (PowerPoint CAMES)
  getPresentation: (projectId: string) =>
    request<import("../types/presentation").PresentationDeck>(
      `/api/projects/${projectId}/presentation`
    ),

  updatePresentation: (
    projectId: string,
    deck: import("../types/presentation").PresentationDeck
  ) =>
    request<import("../types/presentation").PresentationDeck>(
      `/api/projects/${projectId}/presentation`,
      {
        method: "PUT",
        body: JSON.stringify(deck),
      }
    ),

  resetPresentationFromProtocol: (projectId: string) =>
    request<import("../types/presentation").PresentationDeck>(
      `/api/projects/${projectId}/presentation/reset-from-protocol`,
      { method: "POST" }
    ),

  generateFullPresentationAi: (projectId: string) =>
    request<{
      deck: import("../types/presentation").PresentationDeck;
      models_used: Record<string, string>;
      decision_notes: string;
    }>(`/api/projects/${projectId}/presentation/generate-all`, {
      method: "POST",
    }),

  generateSlideAi: (
    projectId: string,
    slideIndex: number,
    payload?: { instruction?: string; layout_mode?: string }
  ) =>
    request<{
      status: string;
      message?: string;
      slide: import("../types/presentation").Slide;
      deck?: import("../types/presentation").PresentationDeck;
      model_used?: string;
      models_used?: Record<string, string>;
    }>(`/api/projects/${projectId}/presentation/generate-slide/${slideIndex}`, {
      method: "POST",
      body: payload ? JSON.stringify(payload) : undefined,
    }),

  auditPresentation: (projectId: string) =>
    request<import("../types/presentation").PresentationAuditReport>(
      `/api/projects/${projectId}/presentation/audit`,
      { method: "POST" }
    ),

  renderPresentationPreviews: (projectId: string) =>
    request<{ count: number; preview_urls: string[] }>(
      `/api/projects/${projectId}/presentation/render-previews`,
      { method: "POST" }
    ),

  listPresentationTemplates: () =>
    request<{
      templates: Array<{
        id: string;
        label: string;
        slide_type: string;
        path: string;
      }>;
    }>(`/api/presentation/templates`),

  exportPresentationPptxUrl: (projectId: string) =>
    `/api/projects/${projectId}/presentation/export/pptx`,

  exportPresentationPdfUrl: (projectId: string) =>
    `/api/projects/${projectId}/presentation/export/pdf`,
};
