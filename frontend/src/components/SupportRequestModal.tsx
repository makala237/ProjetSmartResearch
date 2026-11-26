import { useEffect, useState } from "react";
import {
  ArrowLeft,
  CheckCircle2,
  ChevronRight,
  Clock,
  LifeBuoy,
  Loader2,
  Send,
  Sparkles,
  X,
} from "lucide-react";
import { api } from "../api/client";

export const SEEN_SUPPORT_STORAGE_KEY = "smartresearch_seen_support_ids";

export function getSeenSupportIds(): Set<string> {
  try {
    const raw = localStorage.getItem(SEEN_SUPPORT_STORAGE_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? new Set(parsed.map(String)) : new Set();
  } catch {
    return new Set();
  }
}

export function markSupportIdSeen(id: string): void {
  try {
    const seen = getSeenSupportIds();
    seen.add(id);
    localStorage.setItem(SEEN_SUPPORT_STORAGE_KEY, JSON.stringify(Array.from(seen)));
  } catch {
    // ignore storage errors
  }
}

export interface SupportHistoryItem {
  id: string;
  created_at: string;
  project_name: string;
  category: string;
  message: string;
  resolved: boolean;
  resolved_at?: string | null;
  resolved_by?: string;
  resolution_note?: string;
}

interface SupportRequestModalProps {
  open: boolean;
  onClose: () => void;
  lang: "fr" | "en";
  projectId?: string;
  projectName?: string;
  activeModule: string;
  sectionId?: string;
  sectionTitle?: string;
  initialRequestId?: string | null;
  onMarkedRead?: () => void;
}

type CategoryKey = "formatting" | "content" | "bug";

function renderInlineBold(text: string): React.ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, idx) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return (
        <strong key={idx} className="font-semibold text-slate-900">
          {part.slice(2, -2)}
        </strong>
      );
    }
    return <span key={idx}>{part}</span>;
  });
}

function FormattedSupportReply({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: React.ReactNode[] = [];
  let i = 0;

  while (i < lines.length) {
    const raw = lines[i].trim();
    if (!raw) {
      i += 1;
      continue;
    }

    // Section heading (## or ###)
    const hMatch = raw.match(/^#{1,3}\s+(.+)$/);
    if (hMatch) {
      blocks.push(
        <h4
          key={`h-${i}`}
          className="mt-3 flex items-center gap-1.5 border-b border-slate-200/80 pb-1 text-xs font-bold uppercase tracking-wide text-[#004A8F] first:mt-0"
        >
          {renderInlineBold(hMatch[1])}
        </h4>
      );
      i += 1;
      continue;
    }

    // Tip / callout (> ...)
    if (raw.startsWith(">")) {
      const tipLines: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith(">")) {
        tipLines.push(lines[i].trim().replace(/^>\s?/, ""));
        i += 1;
      }
      blocks.push(
        <div
          key={`tip-${i}`}
          className="flex items-start gap-2 rounded-xl border border-sky-200 bg-sky-50/80 px-3 py-2.5 text-xs leading-relaxed text-sky-950"
        >
          <Sparkles className="mt-0.5 h-3.5 w-3.5 shrink-0 text-sky-600" />
          <div className="space-y-1">
            {tipLines.map((tl, idx) => (
              <p key={idx}>{renderInlineBold(tl)}</p>
            ))}
          </div>
        </div>
      );
      continue;
    }

    // Numbered step (1. ...)
    const stepMatch = raw.match(/^(\d+)[.)]\s+(.+)$/);
    if (stepMatch) {
      blocks.push(
        <div
          key={`step-${i}`}
          className="flex items-start gap-2.5 rounded-xl border border-slate-200/90 bg-slate-50/70 px-3 py-2.5"
        >
          <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[#004A8F] text-[11px] font-bold text-white">
            {stepMatch[1]}
          </span>
          <div className="text-xs leading-relaxed text-slate-700">
            {renderInlineBold(stepMatch[2])}
          </div>
        </div>
      );
      i += 1;
      continue;
    }

    // Bullet list (- or * or •)
    if (/^[-*•]\s+/.test(raw)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*•]\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^[-*•]\s+/, ""));
        i += 1;
      }
      blocks.push(
        <ul key={`ul-${i}`} className="space-y-1.5 pl-1 text-xs leading-relaxed text-slate-700">
          {items.map((it, idx) => (
            <li key={idx} className="flex items-start gap-2">
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" />
              <span>{renderInlineBold(it)}</span>
            </li>
          ))}
        </ul>
      );
      continue;
    }

    // Regular paragraph
    blocks.push(
      <p key={`p-${i}`} className="text-xs leading-relaxed text-slate-700">
        {renderInlineBold(raw)}
      </p>
    );
    i += 1;
  }

  return <div className="space-y-2.5">{blocks}</div>;
}

function fmtShortDate(iso?: string | null, isEn = false): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString(isEn ? "en-GB" : "fr-FR", {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function SupportRequestModal({
  open,
  onClose,
  lang,
  projectId,
  projectName,
  activeModule,
  sectionId,
  initialRequestId,
  onMarkedRead,
}: SupportRequestModalProps) {
  const isEn = lang === "en";
  const [category, setCategory] = useState<CategoryKey>("formatting");
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sentNotice, setSentNotice] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [history, setHistory] = useState<SupportHistoryItem[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [selectedItem, setSelectedItem] = useState<SupportHistoryItem | null>(null);
  const [seenIds, setSeenIds] = useState<Set<string>>(() => getSeenSupportIds());

  useEffect(() => {
    if (!open) {
      setSelectedItem(null);
      return;
    }
    setSentNotice(false);
    setErr(null);
    setLoadingHistory(true);
    setSeenIds(getSeenSupportIds());
    api
      .listMyUpdateRequests()
      .then((rows) => {
        setHistory(rows);
        if (initialRequestId) {
          const found = rows.find((r) => r.id === initialRequestId);
          if (found) {
            setSelectedItem(found);
            if (found.resolved) {
              markSupportIdSeen(found.id);
              setSeenIds(getSeenSupportIds());
              onMarkedRead?.();
            }
          }
        }
      })
      .catch(() => {})
      .finally(() => setLoadingHistory(false));
  }, [open, initialRequestId, onMarkedRead]);

  if (!open) return null;

  const categories: Array<{ key: CategoryKey; label: string }> = isEn
    ? [
        { key: "formatting", label: "Formatting / PDF" },
        { key: "content", label: "Structure / Content" },
        { key: "bug", label: "Technical Issue" },
      ]
    : [
        { key: "formatting", label: "Mise en forme / PDF" },
        { key: "content", label: "Structure / Contenu" },
        { key: "bug", label: "Problème technique" },
      ];

  function openDetail(item: SupportHistoryItem) {
    setSelectedItem(item);
    if (item.resolved) {
      markSupportIdSeen(item.id);
      setSeenIds(getSeenSupportIds());
      onMarkedRead?.();
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (message.trim().length < 4) return;
    setSubmitting(true);
    setErr(null);
    try {
      await api.createUserUpdateRequest({
        project_id: projectId || "",
        project_name: projectName || "",
        module: activeModule || "module1",
        section_id: sectionId || "",
        category,
        message: message.trim(),
      });
      setMessage("");
      setSentNotice(true);
      const rows = await api.listMyUpdateRequests();
      setHistory(rows);
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : String(ex));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4 backdrop-blur-xs">
      <div className="flex max-h-[90vh] w-full max-w-xl flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-5 py-3.5">
          <div className="flex items-center gap-2.5">
            {selectedItem ? (
              <button
                type="button"
                onClick={() => setSelectedItem(null)}
                className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-100"
                title={isEn ? "Back" : "Retour"}
              >
                <ArrowLeft className="h-4 w-4" />
              </button>
            ) : (
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-sky-100 text-sky-700">
                <LifeBuoy className="h-4 w-4" />
              </div>
            )}
            <div>
              <h3 className="text-sm font-bold text-slate-900">
                {selectedItem
                  ? isEn
                    ? "Smart Research Team Response"
                    : "Réponse détaillée — Équipe Smart Research"
                  : isEn
                  ? "Request a Document Adjustment"
                  : "Demander un ajustement / Assistance"}
              </h3>
              <p className="text-[11px] text-slate-500">
                {selectedItem
                  ? selectedItem.project_name || (isEn ? "Protocol Assistance" : "Assistance Protocole")
                  : isEn
                  ? "Sent directly to the Smart Research technical & editorial team."
                  : "Transmis directement à l'équipe technique Smart Research."}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-200 hover:text-slate-700"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* DETAIL VIEW */}
        {selectedItem ? (
          <>
            <div className="flex-1 space-y-4 overflow-y-auto p-5">
              {/* Student's original question */}
              <div className="rounded-xl border border-slate-200 bg-slate-50 p-3.5">
                <div className="mb-1.5 flex items-center justify-between gap-2">
                  <span className="text-[11px] font-bold uppercase tracking-wide text-slate-500">
                    {isEn ? "Your request" : "Votre demande"}
                  </span>
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] text-slate-400">
                      {fmtShortDate(selectedItem.created_at, isEn)}
                    </span>
                    <span
                      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold ${
                        selectedItem.resolved
                          ? "bg-emerald-100 text-emerald-800"
                          : "bg-amber-100 text-amber-800"
                      }`}
                    >
                      {selectedItem.resolved ? (
                        <CheckCircle2 className="h-3 w-3" />
                      ) : (
                        <Clock className="h-3 w-3" />
                      )}
                      {selectedItem.resolved
                        ? isEn
                          ? "Completed"
                          : "Traité"
                        : isEn
                        ? "Pending"
                        : "En attente"}
                    </span>
                  </div>
                </div>
                <p className="whitespace-pre-wrap text-xs leading-relaxed text-slate-700">
                  {selectedItem.message}
                </p>
              </div>

              {/* Formatted Team Response */}
              {selectedItem.resolution_note ? (
                <div className="rounded-2xl border border-emerald-200 bg-white p-4 shadow-xs">
                  <div className="mb-3 flex items-center justify-between gap-2 border-b border-emerald-100 pb-2">
                    <div className="flex items-center gap-2">
                      <span className="flex h-6 w-6 items-center justify-center rounded-full bg-emerald-100 text-emerald-700">
                        <CheckCircle2 className="h-3.5 w-3.5" />
                      </span>
                      <span className="text-xs font-bold text-emerald-900">
                        {selectedItem.resolved_by || "Équipe Smart Research"}
                      </span>
                    </div>
                    {selectedItem.resolved_at && (
                      <span className="text-[11px] text-slate-400">
                        {fmtShortDate(selectedItem.resolved_at, isEn)}
                      </span>
                    )}
                  </div>
                  <FormattedSupportReply text={selectedItem.resolution_note} />
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-amber-300 bg-amber-50/70 p-4 text-xs text-amber-900">
                  {isEn
                    ? "Your request is currently being reviewed by the Smart Research team."
                    : "Votre demande est en cours de traitement par l'équipe Smart Research."}
                </div>
              )}
            </div>

            <div className="flex items-center justify-between border-t border-slate-200 bg-slate-50 px-5 py-3">
              <button
                type="button"
                onClick={() => setSelectedItem(null)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-100"
              >
                <ArrowLeft className="h-3.5 w-3.5" />
                {isEn ? "All requests" : "Toutes mes demandes"}
              </button>
              <button
                type="button"
                onClick={onClose}
                className="rounded-lg bg-[#004A8F] px-4 py-1.5 text-xs font-semibold text-white hover:bg-[#003b73]"
              >
                {isEn ? "Back to my protocol" : "Revenir à mon protocole"}
              </button>
            </div>
          </>
        ) : (
          /* LIST + NEW REQUEST FORM VIEW */
          <div className="flex-1 space-y-4 overflow-y-auto p-5">
            {projectName && (
              <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-[11px] text-slate-600">
                <span className="font-semibold text-slate-800">
                  {isEn ? "Whole protocol:" : "Protocole concerné :"}
                </span>{" "}
                {projectName}
              </div>
            )}

            {sentNotice && (
              <div className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2.5 text-xs font-medium text-emerald-800">
                <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
                <span>
                  {isEn
                    ? "Request sent! You will receive an in-app notification as soon as our team responds."
                    : "Demande envoyée ! Vous recevrez une notification dès que notre équipe aura répondu."}
                </span>
              </div>
            )}

            {err && (
              <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
                {err}
              </div>
            )}

            <form onSubmit={handleSubmit} className="space-y-3">
              <div>
                <label className="mb-1.5 block text-xs font-semibold text-slate-700">
                  {isEn ? "Type of request" : "Type de demande"}
                </label>
                <div className="flex flex-wrap gap-2">
                  {categories.map((c) => (
                    <button
                      key={c.key}
                      type="button"
                      onClick={() => setCategory(c.key)}
                      className={`rounded-full border px-3 py-1 text-xs font-semibold transition ${
                        category === c.key
                          ? "border-sky-600 bg-sky-600 text-white"
                          : "border-slate-200 bg-white text-slate-600 hover:border-sky-300 hover:text-sky-700"
                      }`}
                    >
                      {c.label}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="mb-1.5 block text-xs font-semibold text-slate-700">
                  {isEn ? "Describe the adjustment needed" : "Décrivez l'ajustement ou la question"}
                </label>
                <textarea
                  rows={4}
                  required
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  placeholder={
                    isEn
                      ? "List everything you want changed anywhere in your document or ask a question..."
                      : "Listez librement tout ce que vous souhaitez ajuster dans votre protocole ou posez votre question..."
                  }
                  className="w-full rounded-xl border border-slate-300 px-3 py-2 text-sm text-slate-800 focus:border-sky-500 focus:outline-none"
                />
              </div>

              <div className="flex justify-end">
                <button
                  type="submit"
                  disabled={submitting || message.trim().length < 4}
                  className="inline-flex items-center gap-1.5 rounded-xl bg-sky-600 px-4 py-2 text-xs font-semibold text-white shadow-xs hover:bg-sky-700 disabled:opacity-50"
                >
                  {submitting ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Send className="h-3.5 w-3.5" />
                  )}
                  {isEn ? "Send request" : "Envoyer la demande"}
                </button>
              </div>
            </form>

            {/* User's own recent requests */}
            <div className="border-t border-slate-200 pt-3">
              <div className="mb-2 text-[11px] font-bold uppercase tracking-wide text-slate-500">
                {isEn ? "My recent requests" : "Mes demandes récentes"}
              </div>
              {loadingHistory ? (
                <div className="flex items-center gap-2 text-xs text-slate-400">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  {isEn ? "Loading..." : "Chargement..."}
                </div>
              ) : history.length === 0 ? (
                <p className="text-xs text-slate-400">
                  {isEn ? "No requests submitted yet." : "Aucune demande envoyée pour le moment."}
                </p>
              ) : (
                <div className="space-y-2.5">
                  {history.slice(0, 8).map((item) => {
                    const isUnread = item.resolved && Boolean(item.resolution_note) && !seenIds.has(item.id);
                    const preview = (item.resolution_note || "")
                      .replace(/[#>*`]/g, "")
                      .replace(/\s+/g, " ")
                      .trim();
                    return (
                      <div
                        key={item.id}
                        onClick={() => openDetail(item)}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") openDetail(item);
                        }}
                        className={`cursor-pointer rounded-xl border p-3 text-xs transition ${
                          isUnread
                            ? "border-emerald-300 bg-emerald-50/50 ring-1 ring-emerald-200 hover:bg-emerald-50"
                            : "border-slate-200 bg-slate-50/70 hover:border-sky-300 hover:bg-white"
                        }`}
                      >
                        <div className="mb-1 flex items-center justify-between gap-2">
                          <span className="truncate font-semibold text-slate-800">
                            {item.project_name || (isEn ? "Protocol" : "Protocole")}
                          </span>
                          <div className="flex shrink-0 items-center gap-1.5">
                            {isUnread && (
                              <span className="rounded-full bg-emerald-600 px-2 py-0.5 text-[10px] font-bold text-white">
                                {isEn ? "New" : "Nouveau"}
                              </span>
                            )}
                            {item.resolved ? (
                              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-800">
                                <CheckCircle2 className="h-3 w-3" />
                                {isEn ? "Completed" : "Traité"}
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold text-amber-800">
                                <Clock className="h-3 w-3" />
                                {isEn ? "Pending" : "En attente"}
                              </span>
                            )}
                          </div>
                        </div>
                        <p className="line-clamp-2 text-slate-600">{item.message}</p>
                        {preview && (
                          <div className="mt-2 flex items-center justify-between gap-2 rounded-lg border border-emerald-200/80 bg-white px-2.5 py-1.5 text-[11px] text-emerald-900">
                            <span className="line-clamp-1 flex-1">{preview}</span>
                            <span className="inline-flex shrink-0 items-center gap-0.5 font-bold text-[#004A8F]">
                              {isEn ? "Read full reply" : "Lire la réponse"}
                              <ChevronRight className="h-3.5 w-3.5" />
                            </span>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
