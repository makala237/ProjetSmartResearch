import { useEffect, useState } from "react";
import { CheckCircle2, Clock, LifeBuoy, Loader2, Send, X } from "lucide-react";
import { api } from "../api/client";

interface SupportRequestModalProps {
  open: boolean;
  onClose: () => void;
  lang: "fr" | "en";
  projectId?: string;
  projectName?: string;
  activeModule: string;
  sectionId?: string;
  sectionTitle?: string;
}

type CategoryKey = "formatting" | "content" | "bug";

export function SupportRequestModal({
  open,
  onClose,
  lang,
  projectId,
  projectName,
  activeModule,
  sectionId,
  sectionTitle,
}: SupportRequestModalProps) {
  const isEn = lang === "en";
  const [category, setCategory] = useState<CategoryKey>("formatting");
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sentNotice, setSentNotice] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [history, setHistory] = useState<
    Array<{
      id: string;
      created_at: string;
      project_name: string;
      category: string;
      message: string;
      resolved: boolean;
      resolution_note?: string;
    }>
  >([]);
  const [loadingHistory, setLoadingHistory] = useState(false);

  useEffect(() => {
    if (!open) return;
    setSentNotice(false);
    setErr(null);
    setLoadingHistory(true);
    api
      .listMyUpdateRequests()
      .then((rows) => setHistory(rows))
      .catch(() => {})
      .finally(() => setLoadingHistory(false));
  }, [open]);

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
      <div className="flex max-h-[88vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-200 bg-slate-50 px-5 py-3.5">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-sky-100 text-sky-700">
              <LifeBuoy className="h-4 w-4" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-slate-900">
                {isEn ? "Request a Document Adjustment" : "Demander un ajustement / Assistance"}
              </h3>
              <p className="text-[11px] text-slate-500">
                {isEn
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

        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          {/* Auto-captured context strip (whole protocol) */}
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
                  ? "Request sent! You will receive an in-app notification as soon as your document is updated."
                  : "Demande envoyée ! Vous recevrez une notification dès que votre document aura été mis à jour."}
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
                {isEn ? "Describe the adjustment needed" : "Décrivez l'ajustement souhaité"}
              </label>
              <textarea
                rows={4}
                required
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder={
                  isEn
                    ? "List everything you want changed anywhere in your document (e.g. fix Table 3, adjust budget, update Annex 2, reword objectives)..."
                    : "Listez librement tout ce que vous souhaitez ajuster dans votre protocole (ex. : corriger le Tableau 3, ajuster le budget, revoir l'Annexe 2, reformuler les objectifs)..."
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
              <div className="space-y-2">
                {history.slice(0, 6).map((item) => (
                  <div
                    key={item.id}
                    className="rounded-lg border border-slate-200 bg-slate-50/70 p-2.5 text-xs"
                  >
                    <div className="mb-1 flex items-center justify-between gap-2">
                      <span className="font-semibold text-slate-700">
                        {item.project_name || (isEn ? "Protocol" : "Protocole")}
                      </span>
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
                    <p className="text-slate-600">{item.message}</p>
                    {item.resolution_note && (
                      <p className="mt-1 rounded bg-emerald-50 px-2 py-1 text-[11px] text-emerald-800">
                        <strong>{isEn ? "Team note:" : "Réponse :"}</strong> {item.resolution_note}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
