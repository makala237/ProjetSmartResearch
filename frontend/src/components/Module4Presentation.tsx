import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Airplay,
  AlertTriangle,
  ArrowDown,
  ArrowRight,
  ArrowUp,
  CheckCircle2,
  Clock,
  Copy,
  Download,
  Eye,
  FileText,
  Image as ImageIcon,
  Lock,
  Mic,
  PenLine,
  Plus,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Trash2,
} from "lucide-react";
import { api } from "../api/client";
import type {
  LayoutBox,
  PresentationAuditReport,
  PresentationDeck,
  Slide,
  SlideElement,
} from "../types/presentation";
import type { ImageAsset, ProtocolProject } from "../types/protocol";

interface Module4PresentationProps {
  project: ProtocolProject;
  onError?: (msg: string) => void;
}

const SLIDE_TYPE_BADGE: Record<string, string> = {
  welcome: "Accueil",
  cover: "Garde",
  plan: "Plan",
  introduction: "Intro",
  objective_general: "Obj. Gén.",
  objective_specific: "Obj. Spéc.",
  methodology: "Méthodo",
  chronogram: "Chrono",
  budget: "Budget",
  acknowledgements: "Fin",
};

const LAYOUT_MODE_OPTIONS = [
  { id: "standard", label: "Standard (Puces CAMES)" },
  { id: "split_media", label: "Split + Figure validée" },
  { id: "procedure_flow", label: "Schéma Procédure (Étapes)" },
  { id: "stat_grid", label: "Grille Chiffres Clés" },
];

const FALLBACK_TEMPLATES = [
  { id: "tpl-intro", label: "Introduction (Ampoule)", slide_type: "introduction", path: "tamplates/tamplate diapositive introduction.png" },
  { id: "tpl-obj-gen", label: "Objectif général (Cible)", slide_type: "objective_general", path: "tamplates/tamplate diapositive objectif générale.png" },
  { id: "tpl-obj-spec", label: "Objectifs spécifiques (Multi-cibles)", slide_type: "objective_specific", path: "tamplates/tamplate objectifs spécifiques.png" },
  { id: "tpl-methodology", label: "Méthodologie (Engrenage)", slide_type: "methodology", path: "tamplates/tamplate diapositive  méthodologie.png" },
];

function encodePublicAsset(relPath?: string): string {
  if (!relPath) return "";
  const cleaned = relPath.replace(/^\/+/, "");
  return encodeURI("/" + cleaned);
}

function formatDuration(totalSec: number): string {
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m} min ${s < 10 ? "0" + s : s} s`;
}

export function Module4Presentation({ project, onError }: Module4PresentationProps) {
  const [deck, setDeck] = useState<PresentationDeck | null>(null);
  const [selectedSlideIndex, setSelectedSlideIndex] = useState<number>(1);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [aiGeneratingAll, setAiGeneratingAll] = useState(false);
  const [aiGeneratingSlide, setAiGeneratingSlide] = useState(false);
  const [auditing, setAuditing] = useState(false);
  const [renderingPreviews, setRenderingPreviews] = useState(false);

  const [viewMode, setViewMode] = useState<"editor" | "pptx_preview">("editor");
  const [previewUrls, setPreviewUrls] = useState<string[]>([]);
  const [slideInstruction, setSlideInstruction] = useState("");
  const [auditReport, setAuditReport] = useState<PresentationAuditReport | null>(null);
  const [showAuditPanel, setShowAuditPanel] = useState(false);
  const [templatesCatalog, setTemplatesCatalog] = useState(FALLBACK_TEMPLATES);

  const saveTimerRef = useRef<number | null>(null);
  const pendingDeckRef = useRef<PresentationDeck | null>(null);

  useEffect(() => {
    loadDeck();
    api
      .listPresentationTemplates()
      .then((res) => {
        if (res.templates?.length) setTemplatesCatalog(res.templates);
      })
      .catch(() => {});
    return () => {
      if (saveTimerRef.current) {
        window.clearTimeout(saveTimerRef.current);
      }
    };
  }, [project.project_id]);

  async function loadDeck() {
    setLoading(true);
    setPreviewUrls([]);
    try {
      const data = await api.getPresentation(project.project_id);
      setDeck(data);
      setAuditReport(data.last_audit || null);
      if (data.slides.length > 0) {
        setSelectedSlideIndex(data.current_slide_index || 1);
      }
    } catch (err) {
      if (onError) onError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function persistDeckImmediate(newDeck: PresentationDeck) {
    if (saveTimerRef.current) {
      window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
    pendingDeckRef.current = null;
    setSaving(true);
    try {
      const saved = await api.updatePresentation(project.project_id, newDeck);
      setDeck(saved);
    } catch (err) {
      if (onError) onError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  function scheduleDeckSave(newDeck: PresentationDeck, immediate = false) {
    setDeck(newDeck);
    pendingDeckRef.current = newDeck;
    if (immediate) {
      void persistDeckImmediate(newDeck);
      return;
    }
    if (saveTimerRef.current) {
      window.clearTimeout(saveTimerRef.current);
    }
    saveTimerRef.current = window.setTimeout(() => {
      if (pendingDeckRef.current) {
        void persistDeckImmediate(pendingDeckRef.current);
      }
    }, 600);
  }

  function flushPendingSave() {
    if (pendingDeckRef.current) {
      void persistDeckImmediate(pendingDeckRef.current);
    }
  }

  async function handleResetFromProtocol() {
    if (
      !window.confirm(
        "Reconstruire tout le diaporama à partir des sections, objectifs, variables, figures et budget réels du protocole ?"
      )
    ) {
      return;
    }
    setLoading(true);
    setPreviewUrls([]);
    try {
      const fresh = await api.resetPresentationFromProtocol(project.project_id);
      setDeck(fresh);
      setAuditReport(null);
      setSelectedSlideIndex(1);
    } catch (err) {
      if (onError) onError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function handleGenerateAllAi() {
    setAiGeneratingAll(true);
    setPreviewUrls([]);
    try {
      const res = await api.generateFullPresentationAi(project.project_id);
      setDeck(res.deck);
      setAuditReport(res.deck.last_audit || null);
      setSelectedSlideIndex(1);
    } catch (err) {
      if (onError) onError(err instanceof Error ? err.message : String(err));
    } finally {
      setAiGeneratingAll(false);
    }
  }

  async function handleAiGenerateSlide(slideIdx: number) {
    flushPendingSave();
    setAiGeneratingSlide(true);
    try {
      const res = await api.generateSlideAi(project.project_id, slideIdx, {
        instruction: slideInstruction.trim(),
        layout_mode: activeSlide?.layout_mode,
      });
      if (res.status === "missing_source" || res.status === "locked") {
        if (onError) {
          onError(
            res.message ||
              "Cette diapositive ne dispose pas encore de contenu source dans le Module 1."
          );
        }
        return;
      }
      if (res.deck) {
        setDeck(res.deck);
      } else if (res.slide && deck) {
        const updatedSlides = deck.slides.map((s) =>
          s.slide_index === slideIdx ? res.slide : s
        );
        setDeck({ ...deck, slides: updatedSlides });
      }
      setSlideInstruction("");
    } catch (err) {
      if (onError) onError(err instanceof Error ? err.message : String(err));
    } finally {
      setAiGeneratingSlide(false);
    }
  }

  async function handleRunAudit() {
    flushPendingSave();
    setAuditing(true);
    try {
      const report = await api.auditPresentation(project.project_id);
      setAuditReport(report);
      setShowAuditPanel(true);
    } catch (err) {
      if (onError) onError(err instanceof Error ? err.message : String(err));
    } finally {
      setAuditing(false);
    }
  }

  async function handleRenderPptxPreviews(switchToPreview = true) {
    flushPendingSave();
    setRenderingPreviews(true);
    if (switchToPreview) setViewMode("pptx_preview");
    try {
      const res = await api.renderPresentationPreviews(project.project_id);
      setPreviewUrls(res.preview_urls || []);
    } catch (err) {
      if (onError) onError(err instanceof Error ? err.message : String(err));
    } finally {
      setRenderingPreviews(false);
    }
  }

  const activeSlide: Slide | undefined = useMemo(
    () => deck?.slides.find((s) => s.slide_index === selectedSlideIndex) || deck?.slides[0],
    [deck, selectedSlideIndex]
  );

  const approvedImages = useMemo(
    () => (project.images || []).filter((img) => img.approved && img.local_path),
    [project.images]
  );

  const totalOralSeconds = useMemo(() => {
    if (!deck) return 0;
    const totalWords = deck.slides.reduce((acc, s) => {
      const n = (s.presenter_notes || "").trim();
      return acc + (n ? n.split(/\s+/).length : 0);
    }, 0);
    return Math.round((totalWords / 130) * 60);
  }, [deck]);

  function updateActiveSlide(updates: Partial<Slide>, immediate = false) {
    if (!deck || !activeSlide) return;
    const updatedSlides = deck.slides.map((s) =>
      s.slide_index === activeSlide.slide_index ? { ...s, ...updates } : s
    );
    scheduleDeckSave({ ...deck, slides: updatedSlides }, immediate);
  }

  function handleUpdateElement(elId: string, content: string, title?: string) {
    if (!activeSlide) return;
    const newElements = activeSlide.elements.map((el) =>
      el.id === elId ? { ...el, content, ...(title !== undefined ? { title } : {}) } : el
    );
    updateActiveSlide({ elements: newElements }, false);
  }

  function handleAddBullet() {
    if (!activeSlide) return;
    const isProc = activeSlide.layout_mode === "procedure_flow";
    const newEl: SlideElement = {
      id: `el-${activeSlide.slide_index}-${Date.now()}`,
      element_type: isProc ? "procedure_step" : "bullet_main",
      title: isProc ? `Étape ${activeSlide.elements.length + 1}` : undefined,
      content: "Nouveau point clé à présenter au jury...",
      order: activeSlide.elements.length + 1,
    };
    updateActiveSlide({ elements: [...activeSlide.elements, newEl] }, true);
  }

  function handleRemoveElement(elId: string) {
    if (!activeSlide) return;
    const newElements = activeSlide.elements.filter((el) => el.id !== elId);
    updateActiveSlide({ elements: newElements }, true);
  }

  function handleAttachApprovedFigure(asset: ImageAsset) {
    if (!activeSlide) return;
    const nonMedia = activeSlide.elements.filter((e) => e.element_type !== "media");
    const mediaEl: SlideElement = {
      id: `el-${activeSlide.slide_index}-fig-${Date.now()}`,
      element_type: "media",
      title: asset.caption || asset.title,
      content: asset.title,
      icon_or_badge: asset.asset_id,
      image_path: asset.local_path || "",
      order: nonMedia.length + 1,
    };
    updateActiveSlide(
      { elements: [...nonMedia, mediaEl], layout_mode: "split_media" },
      true
    );
  }

  function handleAddSlide() {
    if (!deck) return;
    const insertAt = selectedSlideIndex;
    const newSlide: Slide = {
      id: `slide-custom-${Date.now()}`,
      slide_index: insertAt + 1,
      slide_type: "methodology",
      title: "Nouvelle diapositive",
      breadcrumb: "CONTENU COMPLÉMENTAIRE",
      layout_template_id: "tpl-methodology",
      layout_mode: "standard",
      background_image_path: "tamplates/tamplate diapositive  méthodologie.png",
      elements: [
        {
          id: `el-custom-${Date.now()}`,
          element_type: "bullet_main",
          content: "Premier argument clé de cette diapositive...",
          order: 1,
        },
      ],
      presenter_notes: "Notes d'élocution orale pour cette nouvelle diapositive.",
      is_validated: false,
    };
    const nextSlides = [...deck.slides];
    nextSlides.splice(insertAt, 0, newSlide);
    const reindexed = nextSlides.map((s, idx) => ({ ...s, slide_index: idx + 1 }));
    scheduleDeckSave({ ...deck, slides: reindexed }, true);
    setSelectedSlideIndex(insertAt + 1);
  }

  function handleDuplicateSlide(slide: Slide, e: React.MouseEvent) {
    e.stopPropagation();
    if (!deck) return;
    const idx = deck.slides.findIndex((s) => s.slide_index === slide.slide_index);
    if (idx < 0) return;
    const copySlide: Slide = {
      ...slide,
      id: `slide-copy-${Date.now()}`,
      title: `${slide.title} (Copie)`,
      locked_as_is: false,
      elements: slide.elements.map((el, i) => ({
        ...el,
        id: `${el.id}-copy-${Date.now()}-${i}`,
      })),
    };
    const nextSlides = [...deck.slides];
    nextSlides.splice(idx + 1, 0, copySlide);
    const reindexed = nextSlides.map((s, i) => ({ ...s, slide_index: i + 1 }));
    scheduleDeckSave({ ...deck, slides: reindexed }, true);
    setSelectedSlideIndex(idx + 2);
  }

  function handleMoveSlide(slideIndex: number, dir: -1 | 1, e: React.MouseEvent) {
    e.stopPropagation();
    if (!deck) return;
    const idx = deck.slides.findIndex((s) => s.slide_index === slideIndex);
    const targetIdx = idx + dir;
    if (idx < 0 || targetIdx < 0 || targetIdx >= deck.slides.length) return;
    const currentSlide = deck.slides[idx];
    const targetSlide = deck.slides[targetIdx];
    if (
      ["welcome", "cover", "plan"].includes(currentSlide.slide_type) ||
      ["welcome", "cover", "plan"].includes(targetSlide.slide_type)
    ) {
      return;
    }
    const nextSlides = [...deck.slides];
    const [item] = nextSlides.splice(idx, 1);
    nextSlides.splice(targetIdx, 0, item);
    const reindexed = nextSlides.map((s, i) => ({ ...s, slide_index: i + 1 }));
    scheduleDeckSave({ ...deck, slides: reindexed }, true);
    setSelectedSlideIndex(targetIdx + 1);
  }

  function handleDeleteSlide(slideIndex: number, e: React.MouseEvent) {
    e.stopPropagation();
    if (!deck || deck.slides.length <= 1) return;
    const target = deck.slides.find((s) => s.slide_index === slideIndex);
    if (target && ["welcome", "cover"].includes(target.slide_type)) return;
    const nextSlides = deck.slides
      .filter((s) => s.slide_index !== slideIndex)
      .map((s, i) => ({ ...s, slide_index: i + 1 }));
    scheduleDeckSave({ ...deck, slides: nextSlides }, true);
    setSelectedSlideIndex(Math.min(slideIndex, nextSlides.length));
  }

  function resolveFigureImgUrl(boxOrEl: { image_path?: string | null; element_id?: string | null }): string {
    const matchedEl = activeSlide?.elements.find(
      (e) => e.id === boxOrEl.element_id || (boxOrEl.image_path && e.image_path === boxOrEl.image_path)
    );
    const assetByBadge = matchedEl?.icon_or_badge
      ? project.images.find((im) => im.asset_id === matchedEl.icon_or_badge)
      : undefined;
    const assetByPath = boxOrEl.image_path
      ? project.images.find((im) => im.local_path === boxOrEl.image_path)
      : undefined;
    const resolved = assetByBadge || assetByPath;
    if (resolved) {
      return `/api/projects/${project.project_id}/images/${resolved.asset_id}/file`;
    }
    return "";
  }

  if (loading) {
    return (
      <div className="flex h-96 items-center justify-center">
        <div className="flex items-center gap-3 text-slate-500">
          <RefreshCw className="h-5 w-5 animate-spin text-[#6C2826]" />
          <span>Chargement du studio PowerPoint CAMES / FMSB...</span>
        </div>
      </div>
    );
  }

  if (!deck || deck.slides.length === 0) {
    return (
      <div className="rounded-2xl border border-slate-200 bg-white p-8 text-center shadow-sm">
        <Airplay className="mx-auto h-12 w-12 text-[#6C2826]" />
        <h3 className="mt-3 text-lg font-bold text-slate-900">
          Studio Diaporama PowerPoint (CAMES / FMSB)
        </h3>
        <p className="mx-auto mt-2 max-w-md text-sm text-slate-600">
          Générez un diaporama structuré directement à partir des sections réelles, objectifs,
          variables, figures validées et budget de votre protocole.
        </p>
        <div className="mt-6 flex flex-wrap justify-center gap-3">
          <button
            onClick={handleGenerateAllAi}
            className="inline-flex items-center gap-2 rounded-xl bg-[#004B87] px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-[#003a6b]"
          >
            <Sparkles className="h-4 w-4" />
            Générer tout par IA (3 rôles)
          </button>
          <button
            onClick={handleResetFromProtocol}
            className="inline-flex items-center gap-2 rounded-xl bg-[#6C2826] px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-[#58201e]"
          >
            <RefreshCw className="h-4 w-4" />
            Générer depuis le protocole
          </button>
        </div>
      </div>
    );
  }

  const notesText = activeSlide?.presenter_notes || "";
  const wordCount = notesText.trim() ? notesText.trim().split(/\s+/).length : 0;
  const estimatedSeconds = Math.round((wordCount / 130) * 60);
  const inCamesWindow = totalOralSeconds >= 600 && totalOralSeconds <= 900;

  const findBox = (role: string): LayoutBox | undefined =>
    activeSlide?.render_boxes?.find((b) => b.role === role);

  return (
    <div className="flex flex-col gap-5">
      {/* Top Studio Control Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-[#6C2826]/10 text-[#6C2826]">
            <Airplay className="h-6 w-6" />
          </div>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-base font-bold text-slate-900">
                Module 4 : Studio PowerPoint (CAMES / FMSB)
              </h2>
              <span className="rounded-full bg-[#6C2826]/10 px-2.5 py-0.5 text-xs font-bold text-[#6C2826]">
                {deck.slides.length} Diapositives
              </span>
              <span
                className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-bold ${
                  inCamesWindow
                    ? "bg-emerald-50 text-emerald-700"
                    : "bg-amber-50 text-amber-700"
                }`}
                title="Durée totale estimée des notes orales (cible CAMES : 10 à 15 min)"
              >
                <Clock className="h-3 w-3" />
                Soutenance : {formatDuration(totalOralSeconds)}
              </span>
              {saving && (
                <span className="text-[11px] font-medium text-slate-400">
                  Sauvegarde…
                </span>
              )}
            </div>
            <p className="text-xs text-slate-500">
              16:9 Widescreen • Données réelles du protocole • Police CAMES ≥ 24pt • {deck.decision_notes || "Prêt pour soutenance"}
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={handleGenerateAllAi}
            disabled={aiGeneratingAll}
            className="inline-flex items-center gap-1.5 rounded-lg bg-[#004B87] px-3.5 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-[#003a6b] disabled:opacity-50"
            title="Synthétise toutes les sections avec Gemini 3.5 Flash Lite, audite avec 3.1 Flash Lite et met en page avec Decision 1"
          >
            <Sparkles className={`h-3.5 w-3.5 ${aiGeneratingAll ? "animate-spin" : ""}`} />
            {aiGeneratingAll ? "Orchestration 3 IA…" : "Générer tout par IA"}
          </button>

          <button
            onClick={handleRunAudit}
            disabled={auditing}
            className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-800 hover:bg-emerald-100 disabled:opacity-50"
          >
            <ShieldCheck className={`h-3.5 w-3.5 ${auditing ? "animate-pulse" : ""}`} />
            {auditing ? "Audit…" : auditReport ? `Audit CAMES (${auditReport.score}/100)` : "Auditer (CAMES)"}
          </button>

          <div className="flex items-center rounded-lg border border-slate-200 bg-slate-100 p-0.5 text-xs font-semibold">
            <button
              type="button"
              onClick={() => setViewMode("editor")}
              className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 transition ${
                viewMode === "editor"
                  ? "bg-white text-slate-900 shadow-sm"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              <PenLine className="h-3 w-3" />
              Éditeur
            </button>
            <button
              type="button"
              onClick={() => {
                if (previewUrls.length === 0) {
                  void handleRenderPptxPreviews(true);
                } else {
                  setViewMode("pptx_preview");
                }
              }}
              className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 transition ${
                viewMode === "pptx_preview"
                  ? "bg-white text-[#004B87] shadow-sm"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              <Eye className="h-3 w-3" />
              Vrai Rendu PPTX
            </button>
          </div>

          <button
            onClick={handleResetFromProtocol}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-100"
            title="Reconstruire depuis les sections du Module 1"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Réinitialiser
          </button>

          <a
            href={api.exportPresentationPptxUrl(project.project_id)}
            download={`Soutenance_Protocole_${project.project_id}.pptx`}
            onClick={() => flushPendingSave()}
            className="inline-flex items-center gap-1.5 rounded-lg bg-[#6C2826] px-3.5 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-[#58201e]"
          >
            <Download className="h-3.5 w-3.5" />
            .pptx
          </a>

          <a
            href={api.exportPresentationPdfUrl(project.project_id)}
            download={`Soutenance_Protocole_${project.project_id}.pdf`}
            onClick={() => flushPendingSave()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-[#6C2826] bg-white px-3.5 py-1.5 text-xs font-semibold text-[#6C2826] shadow-sm hover:bg-[#6C2826]/5"
          >
            <FileText className="h-3.5 w-3.5" />
            .pdf
          </a>
        </div>
      </div>

      {/* Collapsible CAMES Audit Panel */}
      {showAuditPanel && auditReport && (
        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-2">
            <div className="flex items-center gap-2">
              <ShieldCheck
                className={`h-5 w-5 ${
                  auditReport.cames_compliant ? "text-emerald-600" : "text-amber-600"
                }`}
              />
              <span className="text-sm font-bold text-slate-900">
                Rapport d'Audit CAMES — Score : {auditReport.score} / 100
              </span>
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600">
                {auditReport.model_used}
              </span>
            </div>
            <button
              onClick={() => setShowAuditPanel(false)}
              className="text-xs font-semibold text-slate-500 hover:text-slate-800"
            >
              Masquer
            </button>
          </div>
          <p className="mt-2 text-xs leading-relaxed text-slate-700">{auditReport.summary}</p>
          {auditReport.issues.length > 0 && (
            <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2">
              {auditReport.issues.map((iss, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => setSelectedSlideIndex(iss.slide_index)}
                  className={`flex items-start gap-2 rounded-xl border p-2.5 text-left text-xs transition hover:shadow-sm ${
                    iss.severity === "error"
                      ? "border-rose-200 bg-rose-50/70 text-rose-900"
                      : iss.severity === "warning"
                      ? "border-amber-200 bg-amber-50/70 text-amber-900"
                      : "border-slate-200 bg-slate-50 text-slate-700"
                  }`}
                >
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  <div>
                    <div className="font-bold">
                      Slide {iss.slide_index} · {iss.category}
                    </div>
                    <div>{iss.message}</div>
                    {iss.suggestion && (
                      <div className="mt-0.5 text-[11px] opacity-80">{iss.suggestion}</div>
                    )}
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Main Studio Grid */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Left Sidebar: Slide Navigator & Management (3 Cols) */}
        <div className="flex flex-col gap-2 rounded-2xl border border-slate-200 bg-white p-3 shadow-sm lg:col-span-3">
          <div className="mb-1 flex items-center justify-between px-1 text-xs font-bold text-slate-500">
            <span>PLAN · {deck.slides.length} SLIDES</span>
            <button
              type="button"
              onClick={handleAddSlide}
              className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-1 text-[11px] font-semibold text-slate-700 hover:bg-[#6C2826] hover:text-white"
            >
              <Plus className="h-3 w-3" />
              Ajouter
            </button>
          </div>

          <div className="flex max-h-[760px] flex-col gap-1.5 overflow-y-auto pr-1">
            {deck.slides.map((s) => {
              const isSelected = s.slide_index === selectedSlideIndex;
              const hasWarn = (s.cames_warnings?.length || 0) > 0;
              return (
                <div
                  key={s.id}
                  onClick={() => {
                    flushPendingSave();
                    setSelectedSlideIndex(s.slide_index);
                  }}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") setSelectedSlideIndex(s.slide_index);
                  }}
                  className={`group relative flex cursor-pointer items-start gap-2 rounded-xl border p-2 text-left transition ${
                    isSelected
                      ? "border-[#6C2826] bg-[#6C2826]/5 shadow-sm ring-1 ring-[#6C2826]"
                      : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50"
                  }`}
                >
                  <div
                    className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-xs font-bold ${
                      isSelected
                        ? "bg-[#6C2826] text-white"
                        : "bg-slate-100 text-slate-600 group-hover:bg-slate-200"
                    }`}
                  >
                    {s.slide_index}
                  </div>

                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
                        {SLIDE_TYPE_BADGE[s.slide_type] || `Slide ${s.slide_index}`}
                      </span>
                      {s.elements.some((el) => el.element_type === "media") && (
                        <ImageIcon className="h-3 w-3 text-indigo-500" />
                      )}
                      {s.locked_as_is && <Lock className="h-3 w-3 text-amber-600" />}
                      {hasWarn && <AlertTriangle className="h-3 w-3 text-amber-500" />}
                    </div>
                    <p className="truncate text-xs font-semibold text-slate-800">
                      {s.title || "Sans titre"}
                    </p>
                  </div>

                  <div className="flex flex-col items-end gap-1">
                    {s.is_validated && (
                      <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-600" />
                    )}
                    {!["welcome", "cover", "plan"].includes(s.slide_type) && (
                      <div className="hidden items-center gap-0.5 group-hover:flex">
                        <button
                          type="button"
                          onClick={(e) => handleMoveSlide(s.slide_index, -1, e)}
                          className="rounded p-0.5 text-slate-400 hover:bg-slate-200 hover:text-slate-700"
                          title="Monter"
                        >
                          <ArrowUp className="h-3 w-3" />
                        </button>
                        <button
                          type="button"
                          onClick={(e) => handleMoveSlide(s.slide_index, 1, e)}
                          className="rounded p-0.5 text-slate-400 hover:bg-slate-200 hover:text-slate-700"
                          title="Descendre"
                        >
                          <ArrowDown className="h-3 w-3" />
                        </button>
                        <button
                          type="button"
                          onClick={(e) => handleDuplicateSlide(s, e)}
                          className="rounded p-0.5 text-slate-400 hover:bg-slate-200 hover:text-slate-700"
                          title="Dupliquer"
                        >
                          <Copy className="h-3 w-3" />
                        </button>
                        {deck.slides.length > 1 && (
                          <button
                            type="button"
                            onClick={(e) => handleDeleteSlide(s.slide_index, e)}
                            className="rounded p-0.5 text-slate-400 hover:bg-rose-100 hover:text-rose-600"
                            title="Supprimer"
                          >
                            <Trash2 className="h-3 w-3" />
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Center Workspace: 16:9 Canvas + Controls + Speaker Notes (9 Cols) */}
        <div className="flex flex-col gap-5 lg:col-span-9">
          {/* 16:9 Slide Screen Display */}
          <div className="relative overflow-hidden rounded-2xl border-2 border-slate-300 bg-white p-5 shadow-md">
            {/* View Mode: Genuine PowerPoint COM PNG Preview */}
            {viewMode === "pptx_preview" ? (
              <div className="relative aspect-[16/9] w-full overflow-hidden rounded-xl border border-slate-200 bg-slate-100 flex items-center justify-center">
                {renderingPreviews ? (
                  <div className="flex flex-col items-center gap-2 text-sm text-slate-600">
                    <RefreshCw className="h-7 w-7 animate-spin text-[#004B87]" />
                    <span>Rendu natif en cours via Microsoft PowerPoint…</span>
                  </div>
                ) : previewUrls[selectedSlideIndex - 1] ? (
                  <>
                    <img
                      src={previewUrls[selectedSlideIndex - 1]}
                      alt={`Rendu PowerPoint Slide ${selectedSlideIndex}`}
                      className="h-full w-full object-contain"
                    />
                    <button
                      type="button"
                      onClick={() => handleRenderPptxPreviews(true)}
                      className="absolute right-3 top-3 inline-flex items-center gap-1 rounded-lg bg-slate-900/75 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-slate-900"
                    >
                      <RefreshCw className="h-3 w-3" />
                      Actualiser le rendu
                    </button>
                  </>
                ) : (
                  <div className="text-center text-sm text-slate-600">
                    <p>Aucun aperçu généré pour cette diapositive.</p>
                    <button
                      type="button"
                      onClick={() => handleRenderPptxPreviews(true)}
                      className="mt-3 rounded-lg bg-[#004B87] px-4 py-1.5 text-xs font-semibold text-white"
                    >
                      Lancer le rendu PowerPoint
                    </button>
                  </div>
                )}
              </div>
            ) : (
              /* View Mode: Interactive 16:9 Editor using unified render_boxes */
              <div className="relative aspect-[16/9] w-full overflow-hidden rounded-xl border border-slate-100 bg-white text-slate-800 shadow-inner">
                {/* SLIDE TYPE: WELCOME */}
                {activeSlide?.slide_type === "welcome" && (
                  <div className="relative h-full w-full overflow-hidden">
                    <img
                      src={encodePublicAsset(activeSlide.background_image_path)}
                      alt="Fond Bienvenue"
                      className="absolute inset-0 h-full w-full object-cover"
                    />
                    <div className="absolute inset-0 flex items-center justify-center p-8">
                      <div className="w-[82%] rounded-3xl border-2 border-[#004B87] bg-white/95 p-7 text-center shadow-2xl">
                        <input
                          type="text"
                          value={activeSlide.title}
                          onChange={(e) => updateActiveSlide({ title: e.target.value })}
                          onBlur={flushPendingSave}
                          className="w-full border-none bg-transparent text-center text-lg sm:text-xl font-bold uppercase tracking-wider text-[#004B87] focus:outline-none"
                        />
                        <div className="mx-auto my-3 h-1 w-24 rounded-full bg-[#6C2826]" />
                        <input
                          type="text"
                          value={activeSlide.subtitle || ""}
                          onChange={(e) => updateActiveSlide({ subtitle: e.target.value })}
                          onBlur={flushPendingSave}
                          className="w-full border-none bg-transparent text-center text-xl sm:text-2xl font-black uppercase text-[#6C2826] focus:outline-none"
                        />
                      </div>
                    </div>
                  </div>
                )}

                {/* SLIDE TYPE: STRICT OFFICIAL FMSB COVER */}
                {activeSlide?.slide_type === "cover" && (
                  <div className="relative h-full w-full overflow-hidden bg-white">
                    <img
                      src={encodePublicAsset(activeSlide.background_image_path)}
                      alt="Fond Couverture"
                      className="absolute bottom-0 left-0 right-0 h-[74.5%] w-full object-cover"
                    />
                    {(() => {
                      const hFr = findBox("cover_header_fr");
                      const hEn = findBox("cover_header_en");
                      const titleBox = findBox("cover_title");
                      const candBox = findBox("cover_candidate");
                      const dirBox = findBox("cover_director");
                      const codirBox = findBox("cover_codirector");
                      const yrBox = findBox("cover_year");

                      return (
                        <>
                          {/* Top Bilingual Headers + UY1 Emblem */}
                          <div
                            style={{
                              left: `${(hFr?.x_pct ?? 0.03) * 100}%`,
                              top: `${(hFr?.y_pct ?? 0.015) * 100}%`,
                              width: `${(hFr?.w_pct ?? 0.38) * 100}%`,
                              height: `${(hFr?.h_pct ?? 0.235) * 100}%`,
                            }}
                            className="absolute whitespace-pre-line text-center font-serif text-[8.5px] sm:text-[9.5px] font-bold leading-[1.15] text-slate-900"
                          >
                            {hFr?.text}
                          </div>

                          <div
                            style={{ left: "44.8%", top: "1.5%", width: "10.4%", height: "22.8%" }}
                            className="absolute flex items-center justify-center"
                          >
                            <img
                              src={encodePublicAsset("tamplates/fmsb_uy1_logo.png")}
                              alt="Emblème UY1"
                              className="max-h-full max-w-full object-contain"
                            />
                          </div>

                          <div
                            style={{
                              left: `${(hEn?.x_pct ?? 0.59) * 100}%`,
                              top: `${(hEn?.y_pct ?? 0.015) * 100}%`,
                              width: `${(hEn?.w_pct ?? 0.38) * 100}%`,
                              height: `${(hEn?.h_pct ?? 0.235) * 100}%`,
                            }}
                            className="absolute whitespace-pre-line text-center font-serif text-[8.5px] sm:text-[9.5px] font-bold leading-[1.15] text-slate-900"
                          >
                            {hEn?.text}
                          </div>

                          {/* Dynamic Title Box */}
                          <div
                            style={{
                              left: `${(titleBox?.x_pct ?? 0.07) * 100}%`,
                              top: `${(titleBox?.y_pct ?? 0.27) * 100}%`,
                              width: `${(titleBox?.w_pct ?? 0.86) * 100}%`,
                              height: `${(titleBox?.h_pct ?? 0.20) * 100}%`,
                            }}
                            className="absolute flex items-center justify-center rounded-2xl border-2 border-[#6C2826] bg-white px-4 shadow-sm"
                          >
                            <textarea
                              rows={2}
                              value={activeSlide.title}
                              onChange={(e) => updateActiveSlide({ title: e.target.value })}
                              onBlur={flushPendingSave}
                              className="w-full resize-none border-none bg-transparent text-center font-serif text-sm sm:text-base font-black text-[#004B87] leading-snug focus:outline-none"
                            />
                          </div>

                          {/* Degree + Student + Matricule */}
                          <div
                            style={{
                              left: `${(candBox?.x_pct ?? 0.1) * 100}%`,
                              top: `${(candBox?.y_pct ?? 0.49) * 100}%`,
                              width: `${(candBox?.w_pct ?? 0.8) * 100}%`,
                              height: `${(candBox?.h_pct ?? 0.13) * 100}%`,
                            }}
                            className="absolute flex flex-col items-center justify-center text-center font-serif text-xs"
                          >
                            {(candBox?.text || "").split("\n").map((ln, idx) => (
                              <div
                                key={idx}
                                className={
                                  idx === 0
                                    ? "text-[11px] italic text-[#6C2826]"
                                    : idx === 1
                                    ? "text-xs sm:text-sm font-black text-slate-900"
                                    : "text-[11px] font-medium text-slate-800"
                                }
                              >
                                {ln}
                              </div>
                            ))}
                          </div>

                          {/* Supervision 2 Columns */}
                          <div
                            style={{
                              left: `${(dirBox?.x_pct ?? 0.05) * 100}%`,
                              top: `${(dirBox?.y_pct ?? 0.63) * 100}%`,
                              width: `${(dirBox?.w_pct ?? 0.43) * 100}%`,
                              height: `${(dirBox?.h_pct ?? 0.23) * 100}%`,
                            }}
                            className="absolute overflow-hidden rounded-xl border border-slate-300 bg-white/90 px-3 py-1.5 text-center font-serif"
                          >
                            <div className="text-[11px] font-bold text-[#004B87]">{dirBox?.text}</div>
                            <div className="mt-0.5 whitespace-pre-line text-[10px] leading-tight text-slate-900">
                              {dirBox?.sub_text}
                            </div>
                          </div>

                          <div
                            style={{
                              left: `${(codirBox?.x_pct ?? 0.52) * 100}%`,
                              top: `${(codirBox?.y_pct ?? 0.63) * 100}%`,
                              width: `${(codirBox?.w_pct ?? 0.43) * 100}%`,
                              height: `${(codirBox?.h_pct ?? 0.23) * 100}%`,
                            }}
                            className="absolute overflow-hidden rounded-xl border border-slate-300 bg-white/90 px-3 py-1.5 text-center font-serif"
                          >
                            <div className="text-[11px] font-bold text-[#004B87]">
                              {codirBox?.text}
                            </div>
                            <div className="mt-0.5 whitespace-pre-line text-[9.5px] leading-tight text-slate-900">
                              {codirBox?.sub_text}
                            </div>
                          </div>

                          {/* Academic Year */}
                          <div
                            style={{
                              left: `${(yrBox?.x_pct ?? 0.34) * 100}%`,
                              top: `${(yrBox?.y_pct ?? 0.915) * 100}%`,
                              width: `${(yrBox?.w_pct ?? 0.32) * 100}%`,
                              height: `${(yrBox?.h_pct ?? 0.062) * 100}%`,
                            }}
                            className="absolute flex items-center justify-center rounded-lg border border-[#6C2826] bg-white font-serif text-[11px] font-bold text-slate-900"
                          >
                            {yrBox?.text}
                          </div>
                        </>
                      );
                    })()}
                  </div>
                )}

                {/* SLIDE TYPE: PLAN (6 PILLS CALIBRATED TO PIXEL BOXES) */}
                {activeSlide?.slide_type === "plan" && (
                  <div className="relative h-full w-full overflow-hidden">
                    <img
                      src={encodePublicAsset(activeSlide.background_image_path)}
                      alt="Plan Infographique"
                      className="absolute inset-0 h-full w-full object-cover"
                    />
                    <div
                      style={{ left: "3.2%", top: "40.5%", width: "13.0%", height: "16.5%" }}
                      className="absolute flex flex-col items-center justify-center text-center"
                    >
                      <span className="text-xs sm:text-sm font-black text-[#004B87] leading-tight">
                        PLAN
                      </span>
                      <span className="text-[10px] sm:text-xs font-bold text-[#004B87]">
                        du protocole
                      </span>
                    </div>
                    {(activeSlide.render_boxes || [])
                      .filter((b) => b.role === "plan_pill")
                      .map((b, idx) => {
                        const el = activeSlide.elements[idx];
                        return (
                          <div
                            key={b.box_id}
                            style={{
                              left: `${b.x_pct * 100}%`,
                              top: `${b.y_pct * 100}%`,
                              width: `${b.w_pct * 100}%`,
                              height: `${b.h_pct * 100}%`,
                            }}
                            className="absolute flex items-center"
                          >
                            <input
                              type="text"
                              value={el ? el.content : b.text}
                              onChange={(e) =>
                                el && handleUpdateElement(el.id, e.target.value)
                              }
                              onBlur={flushPendingSave}
                              className="w-full border-none bg-transparent px-1 text-xs sm:text-sm font-black text-slate-900 focus:bg-white/70 focus:outline-none rounded"
                            />
                          </div>
                        );
                      })}
                  </div>
                )}

                {/* SLIDE TYPE: CHRONOGRAM (DEPOSITED AS-IS, FIT-CONTAIN) */}
                {activeSlide?.slide_type === "chronogram" && (
                  <div className="relative h-full w-full overflow-hidden bg-white">
                    <img
                      src={encodePublicAsset(activeSlide.background_image_path)}
                      alt="Chronogramme des activités"
                      className="absolute inset-0 h-full w-full object-contain"
                    />
                    <div className="absolute right-3 top-3 inline-flex items-center gap-1.5 rounded-full bg-slate-900/75 px-3 py-1 text-[11px] font-semibold text-white">
                      <Lock className="h-3 w-3 text-amber-300" />
                      Diapositive déposée telle quelle
                    </div>
                  </div>
                )}

                {/* SLIDE TYPE: BUDGET (RADIAL 10 PILLS + CENTRAL TOTAL EDITABLE) */}
                {activeSlide?.slide_type === "budget" && (
                  <div className="relative h-full w-full overflow-hidden">
                    <img
                      src={encodePublicAsset(activeSlide.background_image_path)}
                      alt="Budget Prévisionnel Radial"
                      className="absolute inset-0 h-full w-full object-cover"
                    />
                    {(() => {
                      const totalBox = findBox("budget_total");
                      const pillBoxes = (activeSlide.render_boxes || []).filter(
                        (b) => b.role === "budget_pill"
                      );
                      return (
                        <>
                          {totalBox && (
                            <div
                              style={{
                                left: `${totalBox.x_pct * 100}%`,
                                top: `${totalBox.y_pct * 100}%`,
                                width: `${totalBox.w_pct * 100}%`,
                                height: `${totalBox.h_pct * 100}%`,
                              }}
                              className="absolute flex items-center justify-center"
                            >
                              <input
                                type="text"
                                value={activeSlide.subtitle || ""}
                                onChange={(e) =>
                                  updateActiveSlide({ subtitle: e.target.value })
                                }
                                onBlur={flushPendingSave}
                                className="w-full border-none bg-transparent text-center text-[11px] sm:text-xs font-black text-[#004B87] focus:bg-white/80 focus:outline-none rounded"
                              />
                            </div>
                          )}
                          {pillBoxes.map((b, idx) => {
                            const el = activeSlide.elements[idx];
                            if (!el) return null;
                            return (
                              <div
                                key={b.box_id}
                                style={{
                                  left: `${b.x_pct * 100}%`,
                                  top: `${b.y_pct * 100}%`,
                                  width: `${b.w_pct * 100}%`,
                                  height: `${b.h_pct * 100}%`,
                                }}
                                className="absolute flex items-center justify-center"
                              >
                                <textarea
                                  rows={2}
                                  value={el.content}
                                  onChange={(e) => handleUpdateElement(el.id, e.target.value)}
                                  onBlur={flushPendingSave}
                                  className="w-full resize-none border-none bg-transparent text-center text-[9px] sm:text-[10px] font-bold leading-tight text-slate-900 focus:bg-white/80 focus:outline-none rounded"
                                />
                              </div>
                            );
                          })}
                        </>
                      );
                    })()}
                  </div>
                )}

                {/* SLIDE TYPE: ACKNOWLEDGEMENTS */}
                {activeSlide?.slide_type === "acknowledgements" && (
                  <div className="relative h-full w-full overflow-hidden">
                    <img
                      src={encodePublicAsset(activeSlide.background_image_path)}
                      alt="Remerciements"
                      className="absolute inset-0 h-full w-full object-cover"
                    />
                    <div
                      style={{ left: "5.5%", top: "70.0%", width: "45.5%", height: "8.5%" }}
                      className="absolute flex items-center justify-center"
                    >
                      <input
                        type="text"
                        value={activeSlide.subtitle || "Thank you for your kind attention!"}
                        onChange={(e) => updateActiveSlide({ subtitle: e.target.value })}
                        onBlur={flushPendingSave}
                        className="w-full border-none bg-transparent text-center text-sm sm:text-base italic font-medium text-[#004B87] focus:bg-white/70 focus:outline-none rounded"
                      />
                    </div>
                    <div
                      style={{ left: "62.5%", top: "28.5%", width: "25.5%", height: "37.0%" }}
                      className="absolute flex items-center justify-center"
                    >
                      <textarea
                        rows={6}
                        value={activeSlide.elements[0]?.content || ""}
                        onChange={(e) => {
                          if (activeSlide.elements[0]) {
                            handleUpdateElement(activeSlide.elements[0].id, e.target.value);
                          }
                        }}
                        onBlur={flushPendingSave}
                        className="w-full resize-none border-none bg-transparent text-center text-[11px] sm:text-xs font-bold leading-snug text-[#004B87] focus:bg-white/70 focus:outline-none rounded p-1"
                      />
                    </div>
                  </div>
                )}

                {/* CONTENT SLIDES (INTRODUCTION, OBJECTIVES, METHODOLOGY, CONTENT) */}
                {activeSlide &&
                  !["welcome", "cover", "plan", "chronogram", "budget", "acknowledgements"].includes(
                    activeSlide.slide_type
                  ) && (
                    <div className="relative h-full w-full overflow-hidden">
                      <img
                        src={encodePublicAsset(activeSlide.background_image_path)}
                        alt={activeSlide.title}
                        className="absolute inset-0 h-full w-full object-cover"
                      />

                      {/* Title strictly inside white pill right of icon */}
                      <div
                        style={{ left: "30.0%", top: "3.8%", width: "49.5%", height: "17.0%" }}
                        className="absolute flex items-center"
                      >
                        <input
                          type="text"
                          value={activeSlide.title}
                          onChange={(e) => updateActiveSlide({ title: e.target.value })}
                          onBlur={flushPendingSave}
                          className="w-full border-none bg-transparent text-sm sm:text-lg font-black text-[#004B87] focus:bg-white/70 focus:outline-none rounded px-1"
                        />
                      </div>

                      {/* Body Mode A: Procedure Flow (3-4 vector steps + arrows) */}
                      {activeSlide.layout_mode === "procedure_flow" ? (
                        <div
                          style={{ left: "5.5%", top: "29.5%", width: "85.5%", height: "48.0%" }}
                          className="absolute flex items-stretch gap-2"
                        >
                          {activeSlide.elements
                            .filter((e) => e.element_type !== "media")
                            .slice(0, 4)
                            .map((el, idx, arr) => (
                              <React.Fragment key={el.id}>
                                <div className="group relative flex flex-1 flex-col overflow-hidden rounded-2xl border-2 border-[#004B87] bg-slate-50 shadow-sm">
                                  <div className="bg-[#004B87] px-2 py-1.5 text-center">
                                    <input
                                      type="text"
                                      value={el.title || `Étape ${idx + 1}`}
                                      onChange={(e) =>
                                        handleUpdateElement(el.id, el.content, e.target.value)
                                      }
                                      onBlur={flushPendingSave}
                                      className="w-full border-none bg-transparent text-center text-[11px] font-bold text-white focus:outline-none"
                                    />
                                  </div>
                                  <textarea
                                    value={el.content}
                                    onChange={(e) => handleUpdateElement(el.id, e.target.value)}
                                    onBlur={flushPendingSave}
                                    className="flex-1 resize-none border-none bg-transparent p-2 text-center text-[11px] leading-snug text-slate-800 focus:outline-none"
                                  />
                                  <button
                                    type="button"
                                    onClick={() => handleRemoveElement(el.id)}
                                    className="absolute right-1 top-1 hidden rounded bg-white/90 p-0.5 text-rose-600 group-hover:block"
                                  >
                                    <Trash2 className="h-3 w-3" />
                                  </button>
                                </div>
                                {idx < arr.length - 1 && (
                                  <div className="flex items-center">
                                    <ArrowRight className="h-5 w-5 text-[#6C2826]" />
                                  </div>
                                )}
                              </React.Fragment>
                            ))}
                        </div>
                      ) : activeSlide.layout_mode === "stat_grid" ? (
                        /* Body Mode B: Stat Grid Bento Cards */
                        <div
                          style={{ left: "6.5%", top: "26.5%", width: "82.0%", height: "61.0%" }}
                          className="absolute grid grid-cols-2 gap-3"
                        >
                          {activeSlide.elements
                            .filter((e) => e.element_type !== "media")
                            .slice(0, 4)
                            .map((el) => (
                              <div
                                key={el.id}
                                className="group relative flex flex-col justify-center rounded-2xl border-2 border-[#6C2826]/70 bg-slate-50 p-3 text-center shadow-sm"
                              >
                                <input
                                  type="text"
                                  value={el.sub_content || el.title || "Indicateur"}
                                  onChange={(e) => {
                                    const next = activeSlide.elements.map((item) =>
                                      item.id === el.id
                                        ? { ...item, sub_content: e.target.value, title: e.target.value }
                                        : item
                                    );
                                    updateActiveSlide({ elements: next });
                                  }}
                                  onBlur={flushPendingSave}
                                  className="border-none bg-transparent text-center text-base font-black text-[#6C2826] focus:outline-none"
                                />
                                <textarea
                                  rows={2}
                                  value={el.content}
                                  onChange={(e) => handleUpdateElement(el.id, e.target.value)}
                                  onBlur={flushPendingSave}
                                  className="mt-1 resize-none border-none bg-transparent text-center text-xs font-medium text-slate-800 focus:outline-none"
                                />
                              </div>
                            ))}
                        </div>
                      ) : (
                        /* Body Mode C: Standard Stacked Cards OR Split Media */
                        <>
                          <div
                            style={{
                              left: activeSlide.layout_mode === "split_media" ? "5.5%" : "6.5%",
                              top: "25.5%",
                              width: activeSlide.layout_mode === "split_media" ? "49.5%" : "82.0%",
                              height: "62.5%",
                            }}
                            className="absolute flex flex-col justify-between gap-2"
                          >
                            {activeSlide.elements
                              .filter((e) => e.element_type !== "media")
                              .map((el) => (
                                <div
                                  key={el.id}
                                  className={`group flex flex-1 items-center gap-2 rounded-2xl border px-3 py-1.5 shadow-sm ${
                                    activeSlide.slide_type === "objective_general"
                                      ? "border-[#004B87] bg-slate-50/95 text-center"
                                      : "border-slate-200 bg-white/95"
                                  }`}
                                >
                                  {activeSlide.slide_type !== "objective_general" && (
                                    <span className="text-base font-black text-[#004B87]">•</span>
                                  )}
                                  <textarea
                                    rows={2}
                                    value={el.content}
                                    onChange={(e) => handleUpdateElement(el.id, e.target.value)}
                                    onBlur={flushPendingSave}
                                    className={`w-full resize-none border-none bg-transparent text-xs sm:text-sm leading-snug text-slate-800 focus:outline-none ${
                                      activeSlide.slide_type === "objective_general"
                                        ? "text-center font-bold text-slate-900"
                                        : "font-medium"
                                    }`}
                                  />
                                  <button
                                    type="button"
                                    onClick={() => handleRemoveElement(el.id)}
                                    className="opacity-0 transition group-hover:opacity-100 text-slate-400 hover:text-rose-600"
                                    title="Supprimer ce point"
                                  >
                                    <Trash2 className="h-3.5 w-3.5" />
                                  </button>
                                </div>
                              ))}
                          </div>

                          {activeSlide.layout_mode === "split_media" && (
                            <>
                              {(() => {
                                const mediaEl = activeSlide.elements.find(
                                  (e) => e.element_type === "media"
                                );
                                const imgUrl = mediaEl ? resolveFigureImgUrl(mediaEl) : "";
                                return (
                                  <>
                                    <div
                                      style={{
                                        left: "57.0%",
                                        top: "25.5%",
                                        width: "35.5%",
                                        height: "53.0%",
                                      }}
                                      className="absolute flex items-center justify-center overflow-hidden rounded-2xl border border-slate-300 bg-white p-2 shadow-sm"
                                    >
                                      {imgUrl ? (
                                        <img
                                          src={imgUrl}
                                          alt={mediaEl?.title || "Figure"}
                                          className="max-h-full max-w-full object-contain"
                                        />
                                      ) : (
                                        <div className="text-center text-xs text-slate-400">
                                          <ImageIcon className="mx-auto mb-1 h-8 w-8" />
                                          Sélectionnez une figure validée ci-dessous
                                        </div>
                                      )}
                                    </div>
                                    {mediaEl && (
                                      <div
                                        style={{
                                          left: "57.0%",
                                          top: "79.5%",
                                          width: "35.5%",
                                          height: "8.0%",
                                        }}
                                        className="absolute text-center text-[10px] italic text-slate-600 line-clamp-2"
                                      >
                                        {mediaEl.title || mediaEl.content}
                                      </div>
                                    )}
                                  </>
                                );
                              })()}
                            </>
                          )}
                        </>
                      )}

                      {/* Footer Breadcrumb + Progress Indexer */}
                      <div
                        style={{ left: "6.0%", top: "91.5%", width: "58.0%", height: "5.0%" }}
                        className="absolute flex items-center text-[10px] font-bold uppercase text-slate-600"
                      >
                        {activeSlide.breadcrumb}
                      </div>
                      <div
                        style={{ left: "66.0%", top: "91.5%", width: "14.0%", height: "5.0%" }}
                        className="absolute flex items-center justify-end text-xs font-black text-[#004B87]"
                      >
                        {activeSlide.slide_index < 10
                          ? `0${activeSlide.slide_index}`
                          : activeSlide.slide_index}{" "}
                        / {deck.slides.length < 10 ? `0${deck.slides.length}` : deck.slides.length}
                      </div>
                    </div>
                  )}
              </div>
            )}

            {/* Per-Slide Toolbar under 16:9 Canvas */}
            {activeSlide && (
              <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-3">
                <div className="flex flex-wrap items-center gap-2">
                  {!activeSlide.locked_as_is &&
                    !["welcome", "cover", "chronogram"].includes(activeSlide.slide_type) && (
                      <button
                        type="button"
                        onClick={handleAddBullet}
                        className="inline-flex items-center gap-1 rounded-lg border border-dashed border-slate-300 bg-slate-50 px-2.5 py-1.5 text-xs font-semibold text-slate-700 hover:border-[#6C2826] hover:text-[#6C2826]"
                      >
                        <Plus className="h-3.5 w-3.5" />
                        Ajouter un point
                      </button>
                    )}

                  {!activeSlide.locked_as_is &&
                    ["introduction", "objective_specific", "methodology"].includes(
                      activeSlide.slide_type
                    ) && (
                      <select
                        value={activeSlide.layout_mode || "standard"}
                        onChange={(e) =>
                          updateActiveSlide({ layout_mode: e.target.value }, true)
                        }
                        className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700"
                        title="Disposition de la diapositive"
                      >
                        {LAYOUT_MODE_OPTIONS.map((opt) => (
                          <option key={opt.id} value={opt.id}>
                            {opt.label}
                          </option>
                        ))}
                      </select>
                    )}

                  {activeSlide.layout_mode === "split_media" && approvedImages.length > 0 && (
                    <select
                      value={
                        activeSlide.elements.find((e) => e.element_type === "media")
                          ?.icon_or_badge || ""
                      }
                      onChange={(e) => {
                        const found = approvedImages.find(
                          (im) => im.asset_id === e.target.value
                        );
                        if (found) handleAttachApprovedFigure(found);
                      }}
                      className="max-w-[200px] rounded-lg border border-indigo-200 bg-indigo-50/50 px-2.5 py-1.5 text-xs font-medium text-indigo-900"
                    >
                      <option value="">Choisir une figure validée…</option>
                      {approvedImages.map((im) => (
                        <option key={im.asset_id} value={im.asset_id}>
                          {im.title}
                        </option>
                      ))}
                    </select>
                  )}

                  {!activeSlide.locked_as_is &&
                    !["welcome", "cover", "chronogram"].includes(activeSlide.slide_type) && (
                      <select
                        value={activeSlide.background_image_path || ""}
                        onChange={(e) =>
                          updateActiveSlide(
                            { background_image_path: e.target.value },
                            true
                          )
                        }
                        className="max-w-[190px] rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-600"
                        title="Fond graphique"
                      >
                        {templatesCatalog.map((t) => (
                          <option key={t.id} value={t.path}>
                            {t.label}
                          </option>
                        ))}
                      </select>
                    )}
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  {!activeSlide.locked_as_is && activeSlide.slide_type !== "cover" && (
                    <div className="flex items-center gap-1.5">
                      <input
                        type="text"
                        value={slideInstruction}
                        onChange={(e) => setSlideInstruction(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            void handleAiGenerateSlide(activeSlide.slide_index);
                          }
                        }}
                        placeholder="Consigne IA (ex: 3 puces, schéma)…"
                        className="w-48 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs focus:border-[#004B87] focus:bg-white focus:outline-none"
                      />
                      <button
                        type="button"
                        onClick={() => handleAiGenerateSlide(activeSlide.slide_index)}
                        disabled={aiGeneratingSlide}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-[#004B87]/30 bg-[#004B87]/5 px-3 py-1.5 text-xs font-semibold text-[#004B87] hover:bg-[#004B87]/10 disabled:opacity-50"
                      >
                        <Sparkles
                          className={`h-3.5 w-3.5 ${aiGeneratingSlide ? "animate-spin" : ""}`}
                        />
                        {aiGeneratingSlide ? "IA…" : "Régénérer IA"}
                      </button>
                    </div>
                  )}

                  <button
                    type="button"
                    onClick={() =>
                      updateActiveSlide({ is_validated: !activeSlide.is_validated }, true)
                    }
                    className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition ${
                      activeSlide.is_validated
                        ? "bg-emerald-100 text-emerald-800 hover:bg-emerald-200"
                        : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                    }`}
                  >
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    {activeSlide.is_validated ? "Validée" : "Valider"}
                  </button>
                </div>
              </div>
            )}

            {/* Per-Slide CAMES Warnings Ribbon */}
            {activeSlide && (activeSlide.cames_warnings?.length || 0) > 0 && (
              <div className="mt-3 flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
                <span>{activeSlide.cames_warnings?.join(" • ")}</span>
              </div>
            )}
          </div>

          {/* Bottom Area: Oral Presenter Notes */}
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-3">
              <div className="flex items-center gap-2">
                <Mic className="h-5 w-5 text-[#6C2826]" />
                <h3 className="text-sm font-bold text-slate-900">
                  Notes de Présentation Orale (Discours devant le Jury)
                </h3>
              </div>
              <div className="flex items-center gap-3 text-xs text-slate-500">
                <span className="inline-flex items-center gap-1 font-semibold text-slate-700">
                  <Clock className="h-3.5 w-3.5 text-amber-600" />
                  ~{estimatedSeconds} s sur cette slide
                </span>
                <span>•</span>
                <span>{wordCount} mots (130 mots/min)</span>
              </div>
            </div>

            <textarea
              rows={4}
              value={activeSlide?.presenter_notes || ""}
              onChange={(e) => updateActiveSlide({ presenter_notes: e.target.value })}
              onBlur={flushPendingSave}
              placeholder="Rédigez ici votre discours oral pour cette diapositive (exporté dans le volet Notes de PowerPoint)..."
              className="mt-3 w-full rounded-xl border border-slate-200 bg-slate-50/50 p-3 text-xs leading-relaxed text-slate-800 focus:border-[#6C2826] focus:bg-white focus:outline-none"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
