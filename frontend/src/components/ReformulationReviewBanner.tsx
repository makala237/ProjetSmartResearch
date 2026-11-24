import { useState } from "react";
import {
  CheckCircle2,
  Loader2,
  RefreshCw,
  Sparkles,
  X,
} from "lucide-react";
import { useLanguage } from "../i18n/LanguageContext";

export interface ReformulationPatch {
  target_text: string;
  replacement_text: string;
  reason?: string;
}

export interface PendingReformulationPreview {
  kind: "smart_reformulate" | "audit_fix" | "selection_transform";
  strategy: "selective_patch" | "append_complement" | "full_rewrite" | string;
  summary: string;
  original_markdown: string;
  proposed_markdown: string;
  proposed_html: string;
  patches: ReformulationPatch[];
  model_used?: string;
  // Retry context so user can re-run or refine the instruction without losing original text
  retryPayload: {
    instruction: string;
    mode?: "auto" | "audit_fix" | "complement";
    audit_issues?: string[];
    selected_text?: string;
    action?: "reformulate" | "modify" | "complement" | "cite" | "shorten" | "expand";
  };
}

interface ReformulationReviewBannerProps {
  preview: PendingReformulationPreview;
  busy?: boolean;
  applying?: boolean;
  onAccept: () => void;
  onRetry: (refinedInstruction?: string) => void;
  onCancel: () => void;
}

type DiffSegment =
  | { type: "unchanged"; text: string }
  | { type: "removed"; text: string }
  | { type: "added"; text: string; reason?: string };

/**
 * Build an inline highlighted document view showing unchanged context alongside
 * color-coded removed (red strikethrough) and reformulated/added (green) zones.
 */
function buildColoredSegments(
  original: string,
  proposed: string,
  patches: ReformulationPatch[],
  strategy: string
): DiffSegment[] {
  if (!patches || patches.length === 0) {
    if (original.trim() === proposed.trim()) {
      return [{ type: "unchanged", text: proposed }];
    }
    const segs: DiffSegment[] = [];
    if (original.trim()) segs.push({ type: "removed", text: original });
    if (proposed.trim()) segs.push({ type: "added", text: proposed });
    return segs;
  }

  if (strategy === "append_complement") {
    const segs: DiffSegment[] = [];
    if (original.trim()) {
      segs.push({ type: "unchanged", text: original });
    }
    for (const p of patches) {
      if (p.replacement_text.trim()) {
        segs.push({
          type: "added",
          text: p.replacement_text,
          reason: p.reason,
        });
      }
    }
    return segs;
  }

  // Locate each patch sequentially in `original`
  const segs: DiffSegment[] = [];
  let cursor = 0;
  for (const p of patches) {
    const target = p.target_text || "";
    if (!target.trim()) {
      if (p.replacement_text.trim()) {
        segs.push({ type: "added", text: p.replacement_text, reason: p.reason });
      }
      continue;
    }
    const foundIdx = original.indexOf(target, cursor);
    if (foundIdx < 0) continue;
    if (foundIdx > cursor) {
      segs.push({ type: "unchanged", text: original.slice(cursor, foundIdx) });
    }
    segs.push({ type: "removed", text: target });
    if (p.replacement_text) {
      segs.push({
        type: "added",
        text: p.replacement_text,
        reason: p.reason,
      });
    }
    cursor = foundIdx + target.length;
  }
  if (cursor < original.length) {
    segs.push({ type: "unchanged", text: original.slice(cursor) });
  }
  if (segs.length === 0) {
    return [
      { type: "removed", text: original },
      { type: "added", text: proposed },
    ];
  }
  return segs;
}

export function ReformulationReviewBanner({
  preview,
  busy = false,
  applying = false,
  onAccept,
  onRetry,
  onCancel,
}: ReformulationReviewBannerProps) {
  const { lang } = useLanguage();
  const isEn = lang === "en";
  const [refineText, setRefineText] = useState(preview.retryPayload.instruction || "");

  const segments = buildColoredSegments(
    preview.original_markdown,
    preview.proposed_markdown,
    preview.patches,
    preview.strategy
  );

  const strategyLabel =
    preview.kind === "selection_transform"
      ? isEn
        ? "Selection transform"
        : "Transformation de la sélection"
      : preview.kind === "audit_fix"
      ? isEn
        ? "Surgical Audit Fix"
        : "Correction sélective d'audit"
      : preview.strategy === "append_complement"
      ? isEn
        ? "Complement added (original intact)"
        : "Complément ajouté (texte initial intact)"
      : preview.strategy === "selective_patch"
      ? isEn
        ? "Selective Reformulation"
        : "Reformulation sélective"
      : isEn
      ? "Full Rewrite Proposal"
      : "Proposition de réécriture";

  return (
    <div className="rounded-xl border-2 border-emerald-400 bg-white shadow-md overflow-hidden">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-emerald-200 bg-emerald-50/90 px-4 py-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-700 px-2.5 py-0.5 text-[11px] font-bold text-white">
            <Sparkles className="h-3 w-3" />
            {strategyLabel}
          </span>
          <span className="text-xs font-semibold text-emerald-950">
            {preview.summary}
          </span>
        </div>
        <button
          type="button"
          onClick={onCancel}
          className="rounded p-1 text-slate-500 hover:bg-emerald-100 hover:text-slate-800"
          title={isEn ? "Cancel preview" : "Annuler"}
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Legend */}
      <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 bg-slate-50 px-4 py-1.5 text-[11px] text-slate-600">
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-rose-200 ring-1 ring-rose-400" />
          {isEn ? "Replaced passage" : "Zone remplacée"}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-emerald-200 ring-1 ring-emerald-500" />
          {isEn ? "New reformulated / added zone" : "Zone reformulée / ajoutée"}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-white ring-1 ring-slate-300" />
          {isEn ? "Preserved verbatim" : "Conservé tel quel"}
        </span>
      </div>

      {/* Colored Diff Content */}
      <div className="max-h-80 overflow-y-auto px-4 py-3 text-[13.5px] leading-relaxed text-slate-800 whitespace-pre-wrap font-sans">
        {segments.map((seg, idx) => {
          if (seg.type === "unchanged") {
            return (
              <span key={idx} className="text-slate-700">
                {seg.text}
              </span>
            );
          }
          if (seg.type === "removed") {
            return (
              <span
                key={idx}
                className="mx-0.5 rounded bg-rose-100 px-1 py-0.5 text-rose-900 line-through decoration-rose-500"
              >
                {seg.text}
              </span>
            );
          }
          return (
            <span
              key={idx}
              title={seg.reason || undefined}
              className="mx-0.5 rounded bg-emerald-100 px-1.5 py-0.5 font-medium text-emerald-950 ring-1 ring-emerald-300"
            >
              {seg.text}
            </span>
          );
        })}
      </div>

      {/* Refine + Action Footer */}
      <div className="border-t border-slate-200 bg-slate-50 px-4 py-3 space-y-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="text"
            value={refineText}
            onChange={(e) => setRefineText(e.target.value)}
            placeholder={
              isEn
                ? "Optional: adjust instruction before retrying..."
                : "Optionnel : ajuster la consigne avant de relancer la reformulation..."
            }
            className="flex-1 min-w-[220px] rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs focus:border-indigo-500 focus:outline-none"
          />
          <button
            type="button"
            disabled={busy || applying}
            onClick={() => onRetry(refineText.trim())}
            className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-300 bg-white px-3 py-1.5 text-xs font-semibold text-indigo-800 hover:bg-indigo-50 disabled:opacity-50"
          >
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            {isEn ? "Retry reformulation" : "Relancer la reformulation"}
          </button>
          <button
            type="button"
            disabled={busy || applying}
            onClick={onCancel}
            className="inline-flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-600 hover:bg-slate-100 disabled:opacity-50"
          >
            {isEn ? "Cancel" : "Annuler"}
          </button>
          <button
            type="button"
            disabled={busy || applying}
            onClick={onAccept}
            className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-4 py-1.5 text-xs font-bold text-white shadow-sm hover:bg-emerald-700 disabled:opacity-50"
          >
            {applying ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <CheckCircle2 className="h-3.5 w-3.5" />
            )}
            {isEn ? "Validate & Apply" : "Valider et appliquer"}
          </button>
        </div>
      </div>
    </div>
  );
}
