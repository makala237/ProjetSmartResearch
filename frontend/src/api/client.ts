import type {
  AdminAdjustEntitlementRequest,
  AdminAuditLogOut,
  AdminCreateUserRequest,
  AdminCreateUserResponse,
  AdminDashboardStatsOut,
  AdminGrantPlanRequest,
  AdminRechargeAIRequest,
  AdminResetPasswordResponse,
  AdminStudentStatsRow,
  AdminUpdateProjectIdentityRequest,
  AdminUpdateUserCoverRequest,
  AdminUpdateUserRequest,
  AdminUserDetailOut,
  LicensePlanCreateRequest,
  LicensePlanOut,
  LicensePlanUpdateRequest,
  UsageEventOut,
} from "../types/admin";
import type { TokenResponse, UserMeOut } from "../types/auth";
import type {
  AbbreviationItem,
  AnnexesConfig,
  AuditResult,
  BudgetItem,
  CanvasSectionSpec,
  CoverMetadata,
  CRFBoundaryDetection,
  CRFExtractAndSyncResult,
  CRFFieldSpec,
  CRFSyncReport,
  DiagramConnection,
  DiagramNode,
  DraftResult,
  EntryMode,
  FormattingRules,
  GlobalAuditReport,
  HarmonizeSectionResult,
  HumanizeProtocolReport,
  HumanizeSectionResult,
  ImportMode,
  ImportReport,
  LiteratureSynthesisItem,
  ModelsStatus,
  PatientRecord,
  ProtocolProject,
  ResearchReport,
  ResearchScope,
  SectionOrchestrationTrace,
  SegmentReport,
  StepStatus,
  SyncStructuredReport,
  TimelineGrid,
  VariableItem,
} from "../types/protocol";

const ACCESS_TOKEN_KEY = "sr_fmsb_access_token";
const REFRESH_TOKEN_KEY = "sr_fmsb_refresh_token";

export function getAccessToken(): string | null {
  try {
    return localStorage.getItem(ACCESS_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function getRefreshToken(): string | null {
  try {
    return localStorage.getItem(REFRESH_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setAuthTokens(accessToken: string, refreshToken: string): void {
  try {
    localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
    localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
  } catch {
    // Ignore storage errors in restricted contexts
  }
}

export function clearAuthTokens(): void {
  try {
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
  } catch {
    // Ignore
  }
}

export function authHeaders(): Record<string, string> {
  const tok = getAccessToken();
  return tok ? { Authorization: `Bearer ${tok}` } : {};
}

export function pipelineHeaders(taskId?: string): Record<string, string> {
  return taskId ? { "X-Pipeline-Task-Id": taskId } : {};
}

export function withAuthToken(url: string): string {
  if (
    !url ||
    url.startsWith("http://") ||
    url.startsWith("https://") ||
    url.startsWith("data:") ||
    url.startsWith("blob:")
  ) {
    return url;
  }
  const tok = getAccessToken();
  if (!tok || url.includes("access_token=")) {
    return url;
  }
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}access_token=${encodeURIComponent(tok)}`;
}

let _unauthorizedHandler: ((reason?: string) => void) | null = null;
export function setUnauthorizedHandler(cb: ((reason?: string) => void) | null): void {
  _unauthorizedHandler = cb;
}

async function parseApiError(res: Response): Promise<{ code: string; message: string }> {
  const raw = await res.text();
  if (!raw) {
    return { code: "", message: `Erreur HTTP ${res.status}` };
  }
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed.detail === "object" && parsed.detail !== null) {
      return {
        code: String(parsed.detail.code || ""),
        message: String(parsed.detail.message || JSON.stringify(parsed.detail)),
      };
    }
    if (parsed && typeof parsed.detail === "string") {
      return { code: "", message: parsed.detail };
    }
  } catch {
    // Raw plain text fallback
  }
  return { code: "", message: raw };
}

let _refreshInFlight: Promise<boolean> | null = null;

async function trySilentRefresh(): Promise<boolean> {
  if (_refreshInFlight) return _refreshInFlight;
  const rt = getRefreshToken();
  if (!rt) return false;

  _refreshInFlight = (async () => {
    try {
      const res = await fetch("/api/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: rt }),
      });
      if (!res.ok) return false;
      const data = (await res.json()) as TokenResponse;
      setAuthTokens(data.access_token, data.refresh_token);
      return true;
    } catch {
      return false;
    } finally {
      _refreshInFlight = null;
    }
  })();

  return _refreshInFlight;
}

interface RequestOptions extends RequestInit {
  skipAuth?: boolean;
  _retried?: boolean;
}

async function request<T>(url: string, options?: RequestOptions): Promise<T> {
  const { skipAuth = false, _retried = false, headers, ...fetchOptions } = options || {};
  let res: Response;
  try {
    res = await fetch(url, {
      ...fetchOptions,
      headers: {
        "Content-Type": "application/json",
        ...(skipAuth ? {} : authHeaders()),
        ...(headers || {}),
      },
    });
  } catch (err) {
    if (err instanceof TypeError) {
      throw new Error(
        "Connexion au serveur backend impossible (port 8001). Vérifiez que l'API FastAPI est démarrée."
      );
    }
    throw err;
  }

  if (res.status === 401 && !skipAuth) {
    const { code, message } = await parseApiError(res);
    if (!_retried && code === "TOKEN_EXPIRED" && getRefreshToken()) {
      const refreshed = await trySilentRefresh();
      if (refreshed) {
        return request<T>(url, { ...options, _retried: true });
      }
    }
    clearAuthTokens();
    _unauthorizedHandler?.(message);
    throw new Error(message || "Session expirée. Veuillez vous reconnecter.");
  }

  if (!res.ok) {
    const { message } = await parseApiError(res);
    throw new Error(message || `Erreur HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  getCanvas: (lang: "fr" | "en" = "fr") =>
    request<{ version: string; language?: string; total_steps: number; sections: CanvasSectionSpec[] }>(
      `/api/canvas?lang=${encodeURIComponent(lang)}`
    ),

  getModelsStatus: () => request<ModelsStatus>("/api/models"),

  listProjects: () => request<ProtocolProject[]>("/api/projects"),

  createProject: (payload: {
    name: string;
    entry_mode: EntryMode;
    research_idea: string;
    study_type: string;
    language?: "fr" | "en";
  }) =>
    request<ProtocolProject>("/api/projects", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  updateProjectLanguage: (projectId: string, language: "fr" | "en") =>
    request<ProtocolProject>(`/api/projects/${projectId}/language`, {
      method: "PATCH",
      body: JSON.stringify({ language }),
    }),

  getPipelineProgress: (taskId: string) =>
    request<import("../types/protocol").PipelineProgressSnapshot>(
      `/api/pipeline/progress/${encodeURIComponent(taskId)}`
    ),

  updateCover: (projectId: string, cover: CoverMetadata) =>
    request<ProtocolProject>(`/api/projects/${projectId}/cover`, {
      method: "PATCH",
      body: JSON.stringify(cover),
    }),

  uploadCoverLogo: async (
    projectId: string,
    slot: "university" | "faculty",
    file: File
  ): Promise<ProtocolProject> => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`/api/projects/${projectId}/cover/logo/${slot}`, {
      method: "POST",
      headers: authHeaders(),
      body: form,
    });
    if (!res.ok) {
      const { message } = await parseApiError(res);
      if (res.status === 401) {
        clearAuthTokens();
        _unauthorizedHandler?.(message);
      }
      throw new Error(message || `Erreur HTTP ${res.status}`);
    }
    return res.json();
  },

  deleteCoverLogo: (projectId: string, slot: "university" | "faculty") =>
    request<ProtocolProject>(`/api/projects/${projectId}/cover/logo/${slot}`, {
      method: "DELETE",
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
      search_scope?: ResearchScope;
    },
    taskId?: string
  ) =>
    request<{ project: ProtocolProject; report: ResearchReport }>(
      `/api/projects/${projectId}/research`,
      {
        method: "POST",
        headers: pipelineHeaders(taskId),
        body: JSON.stringify(payload),
      }
    ),

  segmentArticles: (projectId: string, limit = 8, taskId?: string) =>
    request<{ project: ProtocolProject; report: SegmentReport }>(
      `/api/projects/${projectId}/segment`,
      {
        method: "POST",
        headers: pipelineHeaders(taskId),
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
      headers: authHeaders(),
      body: form,
    });
    if (!res.ok) {
      const { message } = await parseApiError(res);
      if (res.status === 401) {
        clearAuthTokens();
        _unauthorizedHandler?.(message);
      }
      throw new Error(message || `Erreur HTTP ${res.status}`);
    }
    return res.json();
  },

  projectImageUrl: (projectId: string, assetId: string) =>
    withAuthToken(`/api/projects/${projectId}/images/${assetId}/file`),

  deleteImage: (projectId: string, assetId: string) =>
    request<ProtocolProject>(`/api/projects/${projectId}/images/${assetId}`, {
      method: "DELETE",
    }),

  draftSection: (
    projectId: string,
    sectionId: string,
    instruction?: string,
    taskId?: string,
    forceExternalResearch = false
  ) =>
    request<{ project: ProtocolProject; result: DraftResult }>(
      `/api/projects/${projectId}/sections/${sectionId}/draft`,
      {
        method: "POST",
        headers: pipelineHeaders(taskId),
        body: JSON.stringify({
          instruction: instruction || null,
          force_external_research: forceExternalResearch,
        }),
      }
    ),

  auditSection: (projectId: string, sectionId: string, taskId?: string) =>
    request<{ project: ProtocolProject; result: AuditResult }>(
      `/api/projects/${projectId}/sections/${sectionId}/audit`,
      {
        method: "POST",
        headers: pipelineHeaders(taskId),
      }
    ),

  fixSection: (projectId: string, sectionId: string, taskId?: string) =>
    request<{ project: ProtocolProject; result: DraftResult }>(
      `/api/projects/${projectId}/sections/${sectionId}/fix`,
      {
        method: "POST",
        headers: pipelineHeaders(taskId),
      }
    ),

  humanizeSection: (
    projectId: string,
    sectionId: string,
    useAi = true,
    taskId?: string
  ) =>
    request<{ project: ProtocolProject; result: HumanizeSectionResult }>(
      `/api/projects/${projectId}/sections/${sectionId}/humanize`,
      {
        method: "POST",
        headers: pipelineHeaders(taskId),
        body: JSON.stringify({ use_ai: useAi }),
      }
    ),

  harmonizeSection: (
    projectId: string,
    sectionId: string,
    alertId?: string,
    taskId?: string
  ) =>
    request<{ project: ProtocolProject; result: HarmonizeSectionResult }>(
      `/api/projects/${projectId}/sections/${sectionId}/harmonize`,
      {
        method: "POST",
        headers: pipelineHeaders(taskId),
        body: JSON.stringify({ alert_id: alertId || null }),
      }
    ),

  humanizeProtocol: (projectId: string, useAi = true) =>
    request<{ project: ProtocolProject; report: HumanizeProtocolReport }>(
      `/api/projects/${projectId}/humanize-all`,
      {
        method: "POST",
        body: JSON.stringify({ use_ai: useAi }),
      }
    ),

  runSectionAntigravity: (
    projectId: string,
    sectionId: string,
    instruction?: string,
    captureImages = true,
    taskId?: string
  ) =>
    request<{
      project: ProtocolProject;
      orchestration_trace: SectionOrchestrationTrace;
      antigravity_report: Record<string, unknown>;
    }>(`/api/projects/${projectId}/sections/${sectionId}/antigravity`, {
      method: "POST",
      headers: pipelineHeaders(taskId),
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

  updateSectionTable: (
    projectId: string,
    sectionId: string,
    tableId: string,
    payload: {
      title?: string;
      anchor_hint?: string;
      headers?: string[];
      rows?: string[][];
    }
  ) =>
    request<ProtocolProject>(
      `/api/projects/${projectId}/sections/${sectionId}/tables/${tableId}`,
      {
        method: "PATCH",
        body: JSON.stringify(payload),
      }
    ),

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

  updateSectionDiagram: (
    projectId: string,
    sectionId: string,
    diagramId: string,
    payload: {
      title?: string;
      caption?: string;
      anchor_hint?: string;
      layout?: "vertical" | "horizontal";
      nodes?: DiagramNode[];
      connections?: DiagramConnection[];
    }
  ) =>
    request<ProtocolProject>(
      `/api/projects/${projectId}/sections/${sectionId}/diagrams/${diagramId}`,
      {
        method: "PATCH",
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
    withAuthToken(`/api/projects/${projectId}/sections/${sectionId}/diagrams/${diagramId}/svg`),

  importProtocol: async (
    projectId: string,
    file: File,
    importMode: ImportMode = "skeleton",
    taskId?: string
  ): Promise<{ project: ProtocolProject; report: ImportReport }> => {
    const form = new FormData();
    form.append("file", file);
    form.append("import_mode", importMode);
    const res = await fetch(`/api/projects/${projectId}/import`, {
      method: "POST",
      headers: {
        ...authHeaders(),
        ...pipelineHeaders(taskId),
      },
      body: form,
    });
    if (!res.ok) {
      const { message } = await parseApiError(res);
      if (res.status === 401) {
        clearAuthTokens();
        _unauthorizedHandler?.(message);
      }
      throw new Error(message || `Erreur HTTP ${res.status}`);
    }
    return res.json();
  },

  deleteInternalSource: (projectId: string, sourceId: string) =>
    request<{ project: ProtocolProject; removed_source_id: string }>(
      `/api/projects/${projectId}/internal-sources/${sourceId}`,
      { method: "DELETE" }
    ),

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

  generateToc: (projectId: string, taskId?: string) =>
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
      {
        method: "POST",
        headers: pipelineHeaders(taskId),
      }
    ),

  validateVancouverReferences: (projectId: string, taskId?: string) =>
    request<{
      project: ProtocolProject;
      references_count: number;
      corrected_count: number;
      decision_model: string;
      decision_summary: string;
    }>(
      `/api/projects/${projectId}/validate-references`,
      {
        method: "POST",
        headers: pipelineHeaders(taskId),
      }
    ),

  translateDepartment: (payload: {
    text: string;
    source_lang: "fr" | "en";
    target_lang: "fr" | "en";
    field_type?: "university" | "faculty" | "department";
  }) =>
    request<{ translated_text: string; model_used: string }>(
      "/api/translate-department",
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    ),

  aiGenerateCrf: (
    projectId: string,
    payload: { mode?: "auto" | "generate" | "improve"; user_instruction?: string }
  ) =>
    request<{
      project: ProtocolProject;
      crf_fields: CRFFieldSpec[];
      annexe1_html: string;
      model_used: string;
      mode_applied: string;
      assistant_commentary: string;
      cames_compliance_checklist: string[];
    }>(`/api/projects/${projectId}/crf/ai-generate`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  extractAndSyncCrf: (
    projectId: string,
    payload: {
      source_mode?: "auto" | "section" | "internal_source";
      source_id?: string | null;
      use_ai?: boolean;
    } = {},
    taskId?: string
  ) =>
    request<CRFExtractAndSyncResult>(
      `/api/projects/${projectId}/crf/extract-and-sync`,
      {
        method: "POST",
        headers: pipelineHeaders(taskId),
        body: JSON.stringify({
          source_mode: payload.source_mode ?? "auto",
          source_id: payload.source_id ?? null,
          use_ai: payload.use_ai ?? true,
        }),
      }
    ),

  syncProjectCrf: (projectId: string, forceExtract = false) =>
    request<{
      project: ProtocolProject;
      crf_fields: CRFFieldSpec[];
      annexe1_html: string;
      detection?: CRFBoundaryDetection | null;
      sync_report?: CRFSyncReport | null;
      model_used?: string;
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
    withAuthToken(`/api/projects/${projectId}/patients/export.csv`),

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
    withAuthToken(`/api/projects/${projectId}/presentation/export/pptx`),

  exportPresentationPdfUrl: (projectId: string) =>
    withAuthToken(`/api/projects/${projectId}/presentation/export/pdf`),

  // Authentication endpoints
  login: (email: string, password: string) =>
    request<TokenResponse>("/api/auth/login", {
      method: "POST",
      skipAuth: true,
      body: JSON.stringify({ email, password }),
    }),

  refreshSession: (refreshToken: string) =>
    request<TokenResponse>("/api/auth/refresh", {
      method: "POST",
      skipAuth: true,
      body: JSON.stringify({ refresh_token: refreshToken }),
    }),

  getMe: () => request<UserMeOut>("/api/auth/me"),

  changePassword: (currentPassword: string, newPassword: string) =>
    request<TokenResponse>("/api/auth/change-password", {
      method: "POST",
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    }),

  logout: () =>
    request<{ status: string }>("/api/auth/logout", {
      method: "POST",
    }),

  // Admin Portal API
  adminGetDashboard: () =>
    request<AdminDashboardStatsOut>("/api/admin/dashboard"),

  adminListPlans: (includeInactive = true) =>
    request<LicensePlanOut[]>(`/api/admin/plans?include_inactive=${includeInactive}`),

  adminCreatePlan: (payload: LicensePlanCreateRequest) =>
    request<LicensePlanOut>("/api/admin/plans", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  adminUpdatePlan: (planId: string, payload: LicensePlanUpdateRequest) =>
    request<LicensePlanOut>(`/api/admin/plans/${planId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  adminDeletePlan: (planId: string) =>
    request<{ status: string; plan_id: string }>(`/api/admin/plans/${planId}`, {
      method: "DELETE",
    }),

  adminListUsers: (params?: { role?: string; search?: string; status?: string }) => {
    const qs = new URLSearchParams();
    if (params?.role) qs.set("role", params.role);
    if (params?.search) qs.set("search", params.search);
    if (params?.status) qs.set("status", params.status);
    const qStr = qs.toString();
    return request<AdminStudentStatsRow[]>(`/api/admin/users${qStr ? `?${qStr}` : ""}`);
  },

  adminCreateUser: (payload: AdminCreateUserRequest) =>
    request<AdminCreateUserResponse>("/api/admin/users", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  adminGetUserDetail: (userId: string) =>
    request<AdminUserDetailOut>(`/api/admin/users/${userId}`),

  adminUpdateUser: (userId: string, payload: AdminUpdateUserRequest) =>
    request<AdminStudentStatsRow>(`/api/admin/users/${userId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  adminResetPassword: (userId: string, newTemporaryPassword?: string) =>
    request<AdminResetPasswordResponse>(`/api/admin/users/${userId}/reset-password`, {
      method: "POST",
      body: JSON.stringify(
        newTemporaryPassword ? { new_temporary_password: newTemporaryPassword } : {}
      ),
    }),

  adminGrantPlan: (userId: string, payload: AdminGrantPlanRequest) =>
    request<AdminStudentStatsRow>(`/api/admin/users/${userId}/grant-plan`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  adminAdjustEntitlement: (
    userId: string,
    moduleKey: string,
    payload: AdminAdjustEntitlementRequest
  ) =>
    request<AdminStudentStatsRow>(`/api/admin/users/${userId}/entitlements/${moduleKey}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  adminRechargeAI: (userId: string, payload: AdminRechargeAIRequest) =>
    request<AdminStudentStatsRow>(`/api/admin/users/${userId}/recharge-ai`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  adminOverrideProjectIdentity: (
    userId: string,
    payload: AdminUpdateProjectIdentityRequest
  ) =>
    request<AdminStudentStatsRow>(`/api/admin/users/${userId}/project-identity`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  adminUpdateUserCover: (
    userId: string,
    payload: AdminUpdateUserCoverRequest
  ) =>
    request<AdminStudentStatsRow>(`/api/admin/users/${userId}/cover`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  adminDeleteUser: (userId: string) =>
    request<{ deleted: boolean; user_id: string }>(`/api/admin/users/${userId}`, {
      method: "DELETE",
    }),

  adminGetStudentStats: (params?: { search?: string; sort_by?: string }) => {
    const qs = new URLSearchParams();
    if (params?.search) qs.set("search", params.search);
    if (params?.sort_by) qs.set("sort_by", params.sort_by);
    const qStr = qs.toString();
    return request<AdminStudentStatsRow[]>(
      `/api/admin/stats/students${qStr ? `?${qStr}` : ""}`
    );
  },

  adminListUsageEvents: (params?: { user_id?: string; module_key?: string; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.user_id) qs.set("user_id", params.user_id);
    if (params?.module_key) qs.set("module_key", params.module_key);
    if (params?.limit) qs.set("limit", String(params.limit));
    const qStr = qs.toString();
    return request<UsageEventOut[]>(`/api/admin/usage-events${qStr ? `?${qStr}` : ""}`);
  },

  adminListAuditLogs: (limit = 100) =>
    request<AdminAuditLogOut[]>(`/api/admin/audit-logs?limit=${limit}`),
};
