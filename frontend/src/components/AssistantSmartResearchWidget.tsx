import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  BookmarkCheck,
  Compass,
  History,
  MessageSquare,
  Mic,
  Plus,
  RotateCcw,
  Send,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { api } from "../api/client";
import type { ModuleKey } from "../types/auth";
import type {
  CopilotMessage,
  ProtocolProject,
  SectionVersionEntry,
} from "../types/protocol";

interface AssistantSmartResearchWidgetProps {
  project: ProtocolProject;
  activeModule: ModuleKey;
  selectedSectionId: string;
  sectionTitle: string;
  lang: "fr" | "en";
  onProjectUpdate: (project: ProtocolProject) => void;
  onNavigateModule: (moduleKey: ModuleKey) => void;
  externalPrompt?: { id: number; text: string } | null;
  onReplayTour?: () => void;
  onResetTips?: () => void;
  onFeatureUsed?: (featureId: string) => void;
}

const MODULE_LABELS_FR: Record<ModuleKey, string> = {
  module1: "Rédaction & Recherche",
  module2: "Édition Word / PDF",
  module3: "Masque & Données",
  module4: "Diaporama PPTX",
};

const MODULE_LABELS_EN: Record<ModuleKey, string> = {
  module1: "Drafting & Research",
  module2: "Word / PDF Editing",
  module3: "CRF & Data Entry",
  module4: "PPTX Presentation",
};

export function AssistantSmartResearchWidget({
  project,
  activeModule,
  selectedSectionId,
  sectionTitle,
  lang,
  onProjectUpdate,
  onNavigateModule,
  externalPrompt,
  onReplayTour,
  onResetTips,
  onFeatureUsed,
}: AssistantSmartResearchWidgetProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<"chat" | "directives" | "versions">("chat");
  const [showGuideBar, setShowGuideBar] = useState(false);
  const [inputText, setInputText] = useState("");
  const [chatMode, setChatMode] = useState<"auto" | "question" | "edit">("auto");
  const [busyChat, setBusyChat] = useState(false);
  const [busyAction, setBusyAction] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [dismissedMsgIds, setDismissedMsgIds] = useState<Record<string, boolean>>({});
  const [newDirective, setNewDirective] = useState("");
  const [activeTextModels, setActiveTextModels] = useState<string[]>(["gemini-3.8-flash"]);
  const [activeVoiceModels, setActiveVoiceModels] = useState<string[]>(["gemini-3.8-live"]);

  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const lastExternalPromptIdRef = useRef<number>(0);

  const messages: CopilotMessage[] = project.copilot_messages ?? [];
  const directives = project.supervisor_directives ?? [];
  const activeDirectivesCount = directives.filter((d) => d.active).length;
  const activeSection = project.sections?.[selectedSectionId];
  const sectionVersions: SectionVersionEntry[] = [...(activeSection?.versions ?? [])].reverse();

  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    api
      .getCopilotStatus(project.project_id)
      .then((status) => {
        if (cancelled) return;
        if (status.text_models?.length) setActiveTextModels(status.text_models);
        if (status.voice_models?.length) setActiveVoiceModels(status.voice_models);
      })
      .catch(() => {
        /* ignore transient status fetch error */
      });
    return () => {
      cancelled = true;
    };
  }, [isOpen, project.project_id]);

  useEffect(() => {
    if (isOpen && activeTab === "chat") {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [isOpen, activeTab, messages.length]);

  const handleSendMessage = useCallback(
    async (overrideText?: string, overrideMode?: "auto" | "question" | "edit") => {
      const text = (overrideText ?? inputText).trim();
      if (!text || busyChat) return;
      setErrorMsg(null);
      setBusyChat(true);
      try {
        const res = await api.copilotChat(project.project_id, {
          message: text,
          active_module: activeModule,
          active_section_id: selectedSectionId || undefined,
          mode: overrideMode ?? chatMode,
        });
        if (!overrideText) setInputText("");
        if (res.active_text_models?.length) setActiveTextModels(res.active_text_models);
        if (res.active_voice_models?.length) setActiveVoiceModels(res.active_voice_models);
        onProjectUpdate(res.project);
      } catch (err) {
        setErrorMsg(err instanceof Error ? err.message : String(err));
      } finally {
        setBusyChat(false);
      }
    },
    [inputText, busyChat, project.project_id, activeModule, selectedSectionId, chatMode, onProjectUpdate]
  );

  useEffect(() => {
    if (!externalPrompt || externalPrompt.id === lastExternalPromptIdRef.current) {
      return;
    }
    lastExternalPromptIdRef.current = externalPrompt.id;
    setIsOpen(true);
    setActiveTab("chat");
    handleSendMessage(externalPrompt.text, "question");
  }, [externalPrompt, handleSendMessage]);

  async function handleApplyProposal(msg: CopilotMessage) {
    if (!msg.proposal || busyAction) return;
    setErrorMsg(null);
    setBusyAction(true);
    try {
      const updated = await api.applyCopilotProposal(project.project_id, {
        section_id: msg.proposal.section_id,
        proposed_markdown: msg.proposal.proposed_markdown,
        message_id: msg.message_id,
      });
      onProjectUpdate(updated);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAction(false);
    }
  }

  async function handleAddDirective(textOverride?: string, source = "manual") {
    const clean = (textOverride ?? newDirective).trim();
    if (!clean || busyAction) return;
    setErrorMsg(null);
    setBusyAction(true);
    try {
      const updated = await api.createDirective(project.project_id, clean, source);
      if (!textOverride) setNewDirective("");
      onProjectUpdate(updated);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAction(false);
    }
  }

  async function handleToggleDirective(directiveId: string, active: boolean) {
    if (busyAction) return;
    setErrorMsg(null);
    setBusyAction(true);
    try {
      const updated = await api.updateDirective(project.project_id, directiveId, { active });
      onProjectUpdate(updated);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAction(false);
    }
  }

  async function handleDeleteDirective(directiveId: string) {
    if (busyAction) return;
    setErrorMsg(null);
    setBusyAction(true);
    try {
      const updated = await api.deleteDirective(project.project_id, directiveId);
      onProjectUpdate(updated);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAction(false);
    }
  }

  async function handleRestoreVersion(sectionId: string, versionId: string) {
    if (busyAction) return;
    setErrorMsg(null);
    setBusyAction(true);
    try {
      const updated = await api.restoreSectionVersion(
        project.project_id,
        sectionId,
        versionId
      );
      onProjectUpdate(updated);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAction(false);
    }
  }

  async function handleClearHistory() {
    if (busyAction || messages.length === 0) return;
    setErrorMsg(null);
    setBusyAction(true);
    try {
      const updated = await api.clearCopilotMessages(project.project_id);
      onProjectUpdate(updated);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAction(false);
    }
  }

  const moduleBadgeLabel =
    lang === "en" ? MODULE_LABELS_EN[activeModule] : MODULE_LABELS_FR[activeModule];

  const quickPrompts =
    lang === "en"
      ? [
          "Where did the main definition or reference in this section come from?",
          "Review this section against my supervisor's directives without overwriting it.",
          "Compare and propose a cleaner merged version of this section.",
        ]
      : [
          "D'où provient la définition ou la source principale de cette section ?",
          "Vérifie la cohérence de cette section avec mes directives sans rien modifier.",
          "Compare et propose une version améliorée sans perdre les passages existants.",
        ];

  return (
    <div className="fixed bottom-20 right-4 z-40 md:bottom-5">
      {!isOpen ? (
        <button
          type="button"
          data-testid="assistant-smart-research-fab"
          onClick={() => setIsOpen(true)}
          className="group flex items-center gap-2.5 rounded-full bg-[#004A8F] px-4 py-3 text-xs font-bold text-white shadow-xl ring-2 ring-white/80 transition hover:bg-[#003b73]"
        >
          <Sparkles className="h-4 w-4 text-amber-300" />
          <span>Assistant Smart Research</span>
          {activeDirectivesCount > 0 && (
            <span className="rounded-full bg-white/20 px-1.5 py-0.5 text-[10px]">
              {activeDirectivesCount}
            </span>
          )}
        </button>
      ) : (
        <div
          data-testid="assistant-smart-research-panel"
          className="flex h-[560px] max-h-[82vh] w-[380px] max-w-[calc(100vw-2rem)] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl sm:w-[420px]"
        >
          {/* Header */}
          <div className="flex items-start justify-between gap-2 bg-[#004A8F] px-3.5 py-3 text-white">
            <div className="min-w-0">
              <div className="flex items-center gap-1.5 text-sm font-bold">
                <Sparkles className="h-4 w-4 shrink-0 text-amber-300" />
                <span className="truncate">Assistant Smart Research</span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-blue-100">
                <span className="rounded bg-white/15 px-1.5 py-0.5 font-semibold">
                  {moduleBadgeLabel}
                </span>
                {sectionTitle && (
                  <span className="max-w-[210px] truncate rounded bg-white/10 px-1.5 py-0.5">
                    {sectionTitle}
                  </span>
                )}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              {(onReplayTour || onResetTips) && (
                <button
                  type="button"
                  onClick={() => setShowGuideBar((v) => !v)}
                  title={lang === "en" ? "Guide & Onboarding" : "Guide & Astuces"}
                  className={`rounded-lg p-1.5 transition ${
                    showGuideBar
                      ? "bg-white/25 text-white"
                      : "text-blue-100 hover:bg-white/15 hover:text-white"
                  }`}
                >
                  <Compass className="h-3.5 w-3.5" />
                </button>
              )}
              {activeTab === "chat" && messages.length > 0 && (
                <button
                  type="button"
                  onClick={handleClearHistory}
                  disabled={busyAction}
                  title={lang === "en" ? "Clear conversation" : "Effacer la discussion"}
                  className="rounded-lg p-1.5 text-blue-100 hover:bg-white/15 hover:text-white disabled:opacity-40"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              )}
              <button
                type="button"
                onClick={() => setIsOpen(false)}
                title={lang === "en" ? "Close" : "Réduire"}
                className="rounded-lg p-1.5 text-blue-100 hover:bg-white/15 hover:text-white"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>

          {showGuideBar && (
            <div className="flex flex-wrap items-center justify-between gap-1.5 border-b border-blue-100 bg-blue-50 px-3 py-2 text-[11px]">
              {onReplayTour && (
                <button
                  type="button"
                  onClick={() => {
                    setShowGuideBar(false);
                    onReplayTour();
                  }}
                  className="rounded-lg border border-blue-200 bg-white px-2 py-1 font-semibold text-[#004A8F] hover:bg-blue-100/60"
                >
                  {lang === "en" ? "Replay guided tour" : "Relancer la visite guidée"}
                </button>
              )}
              {onResetTips && (
                <button
                  type="button"
                  onClick={() => {
                    setShowGuideBar(false);
                    onResetTips();
                  }}
                  className="rounded-lg border border-amber-200 bg-white px-2 py-1 font-semibold text-amber-800 hover:bg-amber-50"
                >
                  {lang === "en" ? "Re-enable 1st-click tips" : "Réactiver les astuces 1er clic"}
                </button>
              )}
            </div>
          )}

          {/* Navigation tabs */}
          <div className="grid grid-cols-3 border-b border-slate-200 bg-slate-50 text-[11px] font-semibold">
            <button
              type="button"
              onClick={() => setActiveTab("chat")}
              className={`flex items-center justify-center gap-1 py-2.5 transition ${
                activeTab === "chat"
                  ? "border-b-2 border-[#004A8F] bg-white text-[#004A8F]"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              <MessageSquare className="h-3.5 w-3.5" />
              <span>{lang === "en" ? "Chat" : "Discussion"}</span>
            </button>
            <button
              type="button"
              onClick={() => {
                setActiveTab("directives");
                onFeatureUsed?.("supervisor_directives");
              }}
              className={`flex items-center justify-center gap-1 py-2.5 transition ${
                activeTab === "directives"
                  ? "border-b-2 border-[#004A8F] bg-white text-[#004A8F]"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              <BookmarkCheck className="h-3.5 w-3.5" />
              <span>{lang === "en" ? "Directives" : "Directives"}</span>
              {activeDirectivesCount > 0 && (
                <span className="rounded-full bg-[#004A8F]/10 px-1.5 text-[10px] text-[#004A8F]">
                  {activeDirectivesCount}
                </span>
              )}
            </button>
            <button
              type="button"
              onClick={() => {
                setActiveTab("versions");
                onFeatureUsed?.("section_versions");
              }}
              className={`flex items-center justify-center gap-1 py-2.5 transition ${
                activeTab === "versions"
                  ? "border-b-2 border-[#004A8F] bg-white text-[#004A8F]"
                  : "text-slate-600 hover:text-slate-900"
              }`}
            >
              <History className="h-3.5 w-3.5" />
              <span>Versions</span>
              {sectionVersions.length > 0 && (
                <span className="rounded-full bg-slate-200 px-1.5 text-[10px] text-slate-700">
                  {sectionVersions.length}
                </span>
              )}
            </button>
          </div>

          {errorMsg && (
            <div className="border-b border-rose-200 bg-rose-50 px-3 py-2 text-[11px] text-rose-800">
              {errorMsg}
            </div>
          )}

          {/* TAB 1: NON-DESTRUCTIVE CHAT */}
          {activeTab === "chat" && (
            <>
              <div className="flex-1 space-y-3 overflow-y-auto bg-slate-50/60 p-3">
                {messages.length === 0 ? (
                  <div className="space-y-2.5">
                    <div className="rounded-xl border border-blue-100 bg-white p-3 text-xs leading-relaxed text-slate-700 shadow-xs">
                      {lang === "en" ? (
                        <>
                          I am <strong>Assistant Smart Research</strong>. Ask me about a source,
                          methodology, or app workflow — your section text will{" "}
                          <strong>never</strong> be overwritten unless you click Apply.
                        </>
                      ) : (
                        <>
                          Je suis <strong>Assistant Smart Research</strong>. Posez librement vos
                          questions sur une source, une définition ou la méthodologie : votre texte{" "}
                          <strong>ne sera jamais écrasé</strong> sans votre validation.
                        </>
                      )}
                    </div>
                    <div className="space-y-1.5">
                      {quickPrompts.map((qp, idx) => (
                        <button
                          key={idx}
                          type="button"
                          onClick={() => handleSendMessage(qp)}
                          disabled={busyChat}
                          className="block w-full rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-left text-[11px] text-slate-700 transition hover:border-[#004A8F]/40 hover:bg-blue-50/40"
                        >
                          {qp}
                        </button>
                      ))}
                    </div>
                  </div>
                ) : (
                  messages.map((msg) => {
                    const isUser = msg.role === "user";
                    const proposal = msg.proposal;
                    const proposalDismissed = dismissedMsgIds[msg.message_id];
                    return (
                      <div
                        key={msg.message_id}
                        className={`flex flex-col ${isUser ? "items-end" : "items-start"}`}
                      >
                        <div
                          className={`max-w-[92%] rounded-2xl px-3 py-2 text-xs leading-relaxed whitespace-pre-wrap shadow-xs ${
                            isUser
                              ? "bg-[#004A8F] text-white"
                              : "border border-slate-200 bg-white text-slate-800"
                          }`}
                        >
                          {msg.content}
                        </div>

                        {/* Non-destructive Edit Proposal card */}
                        {!isUser && proposal && !proposalDismissed && (
                          <div className="mt-1.5 w-[94%] rounded-xl border border-amber-200 bg-amber-50/70 p-2.5 text-[11px]">
                            <div className="font-bold text-amber-900">
                              {lang === "en"
                                ? "Proposed revision (preview)"
                                : "Proposition d'amélioration (aperçu)"}
                            </div>
                            {proposal.summary && (
                              <p className="mt-0.5 text-slate-700">{proposal.summary}</p>
                            )}
                            <div className="mt-1.5 max-h-28 overflow-y-auto rounded border border-amber-200/80 bg-white p-2 font-serif text-[11px] leading-snug text-slate-800">
                              {proposal.proposed_markdown}
                            </div>
                            {proposal.applied ? (
                              <div className="mt-2 inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-800">
                                {lang === "en"
                                  ? "Applied (previous version saved)"
                                  : "Appliqué (version précédente sauvegardée)"}
                              </div>
                            ) : (
                              <div className="mt-2 flex items-center gap-2">
                                <button
                                  type="button"
                                  disabled={busyAction}
                                  onClick={() => handleApplyProposal(msg)}
                                  className="rounded-lg bg-emerald-600 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-emerald-700 disabled:opacity-40"
                                >
                                  {lang === "en" ? "Apply to section" : "Appliquer dans la section"}
                                </button>
                                <button
                                  type="button"
                                  onClick={() =>
                                    setDismissedMsgIds((prev) => ({
                                      ...prev,
                                      [msg.message_id]: true,
                                    }))
                                  }
                                  className="rounded-lg border border-slate-300 bg-white px-2.5 py-1 text-[11px] font-semibold text-slate-700 hover:bg-slate-50"
                                >
                                  {lang === "en" ? "Dismiss" : "Ignorer"}
                                </button>
                              </div>
                            )}
                          </div>
                        )}

                        {/* Supervisor Directive detection */}
                        {!isUser && msg.suggested_directive && (
                          <div className="mt-1.5 w-[94%] rounded-xl border border-indigo-200 bg-indigo-50/70 p-2.5 text-[11px]">
                            <div className="font-bold text-indigo-900">
                              {lang === "en"
                                ? "Supervisor directive detected"
                                : "Directive encadreur détectée"}
                            </div>
                            <p className="mt-0.5 text-slate-700">{msg.suggested_directive}</p>
                            <button
                              type="button"
                              disabled={busyAction}
                              onClick={() =>
                                handleAddDirective(msg.suggested_directive || "", "copilot")
                              }
                              className="mt-2 inline-flex items-center gap-1 rounded-lg bg-indigo-600 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-indigo-700 disabled:opacity-40"
                            >
                              <Plus className="h-3 w-3" />
                              {lang === "en"
                                ? "Add to global directives"
                                : "Ajouter aux directives globales"}
                            </button>
                          </div>
                        )}

                        {/* Version restore button */}
                        {!isUser && msg.restore_version_id && (
                          <button
                            type="button"
                            disabled={busyAction}
                            onClick={() =>
                              handleRestoreVersion(
                                msg.active_section_id || selectedSectionId,
                                msg.restore_version_id!
                              )
                            }
                            className="mt-1.5 inline-flex items-center gap-1 rounded-lg border border-emerald-300 bg-emerald-50 px-2.5 py-1 text-[11px] font-semibold text-emerald-800 hover:bg-emerald-100"
                          >
                            <RotateCcw className="h-3 w-3" />
                            {lang === "en"
                              ? `Restore version ${msg.restore_version_id}`
                              : `Restaurer la version ${msg.restore_version_id}`}
                          </button>
                        )}

                        {/* Module navigation button */}
                        {!isUser && msg.target_module && (
                          <button
                            type="button"
                            onClick={() =>
                              onNavigateModule(msg.target_module as ModuleKey)
                            }
                            className="mt-1.5 inline-flex items-center gap-1 rounded-lg border border-blue-300 bg-blue-50 px-2.5 py-1 text-[11px] font-semibold text-[#004A8F] hover:bg-blue-100"
                          >
                            <span>
                              {lang === "en" ? "Open" : "Ouvrir"}{" "}
                              {lang === "en"
                                ? MODULE_LABELS_EN[msg.target_module as ModuleKey] ??
                                  msg.target_module
                                : MODULE_LABELS_FR[msg.target_module as ModuleKey] ??
                                  msg.target_module}
                            </span>
                            <ArrowRight className="h-3 w-3" />
                          </button>
                        )}
                      </div>
                    );
                  })
                )}
                {busyChat && (
                  <div className="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-500">
                    <span className="h-2 w-2 animate-pulse rounded-full bg-[#004A8F]" />
                    <span>
                      {lang === "en"
                        ? "Assistant Smart Research is thinking..."
                        : "Assistant Smart Research réfléchit..."}
                    </span>
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>

              {/* Composer */}
              <div className="border-t border-slate-200 bg-white p-2.5">
                <div className="mb-2 flex items-center justify-between gap-1 text-[10px]">
                  <div className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-0.5 font-semibold">
                    {(["auto", "question", "edit"] as const).map((m) => (
                      <button
                        key={m}
                        type="button"
                        onClick={() => setChatMode(m)}
                        className={`rounded-md px-2 py-0.5 transition ${
                          chatMode === m
                            ? "bg-[#004A8F] text-white"
                            : "text-slate-600 hover:text-slate-900"
                        }`}
                      >
                        {m === "auto"
                          ? "Auto"
                          : m === "question"
                          ? lang === "en"
                            ? "Q&A only"
                            : "Question seule"
                          : lang === "en"
                          ? "Suggest edit"
                          : "Proposer texte"}
                      </button>
                    ))}
                  </div>
                  <button
                    type="button"
                    disabled
                    title={
                      lang === "en"
                        ? `Live voice (${activeVoiceModels[0] ?? "gemini-3.8-live"}) — Phase 3 roadmap`
                        : `Micro Live (${activeVoiceModels[0] ?? "gemini-3.8-live"}) — feuille de route Phase 3`
                    }
                    className="inline-flex cursor-not-allowed items-center gap-1 rounded-lg border border-slate-200 bg-slate-100 px-2 py-0.5 font-semibold text-slate-400"
                  >
                    <Mic className="h-3 w-3" />
                    <span>Live</span>
                  </button>
                </div>

                <div className="flex items-end gap-1.5">
                  <textarea
                    rows={2}
                    value={inputText}
                    onChange={(e) => setInputText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        handleSendMessage();
                      }
                    }}
                    placeholder={
                      lang === "en"
                        ? `Ask Assistant Smart Research (${activeTextModels[0] ?? "gemini-3.8-flash"})...`
                        : "Posez une question sans risque d'écraser votre section..."
                    }
                    className="flex-1 resize-none rounded-xl border border-slate-300 px-2.5 py-2 text-xs focus:border-[#004A8F] focus:outline-none"
                  />
                  <button
                    type="button"
                    disabled={busyChat || !inputText.trim()}
                    onClick={() => handleSendMessage()}
                    className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[#004A8F] text-white transition hover:bg-[#003b73] disabled:opacity-40"
                    title={lang === "en" ? "Send" : "Envoyer"}
                  >
                    <Send className="h-4 w-4" />
                  </button>
                </div>
              </div>
            </>
          )}

          {/* TAB 2: GLOBAL SUPERVISOR DIRECTIVES */}
          {activeTab === "directives" && (
            <div className="flex flex-1 flex-col overflow-hidden bg-slate-50/50 p-3">
              <p className="mb-2.5 text-[11px] leading-relaxed text-slate-600">
                {lang === "en"
                  ? "Supervisor directives saved here are injected as top priority across every chapter (overriding generic defaults)."
                  : "Les directives de votre directeur enregistrées ici s'appliquent en priorité sur l'ensemble des 5 chapitres."}
              </p>

              <div className="mb-3 flex gap-1.5">
                <input
                  type="text"
                  value={newDirective}
                  onChange={(e) => setNewDirective(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      handleAddDirective();
                    }
                  }}
                  placeholder={
                    lang === "en"
                      ? "Ex: Focus solely on pediatric ICU at CHUY..."
                      : "Ex : Restreindre l'étude aux dossiers 2020-2025 du CHUY..."
                  }
                  className="flex-1 rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs focus:border-[#004A8F] focus:outline-none"
                />
                <button
                  type="button"
                  disabled={busyAction || !newDirective.trim()}
                  onClick={() => handleAddDirective()}
                  className="inline-flex items-center gap-1 rounded-lg bg-[#004A8F] px-2.5 py-1.5 text-xs font-semibold text-white hover:bg-[#003b73] disabled:opacity-40"
                >
                  <Plus className="h-3.5 w-3.5" />
                  <span>{lang === "en" ? "Add" : "Ajouter"}</span>
                </button>
              </div>

              <div className="flex-1 space-y-2 overflow-y-auto">
                {directives.length === 0 ? (
                  <div className="rounded-xl border border-dashed border-slate-300 bg-white p-4 text-center text-xs text-slate-500">
                    {lang === "en"
                      ? "No supervisor directives recorded yet."
                      : "Aucune directive d'encadreur enregistrée pour l'instant."}
                  </div>
                ) : (
                  directives.map((dir) => (
                    <div
                      key={dir.directive_id}
                      className={`rounded-xl border p-2.5 text-xs transition ${
                        dir.active
                          ? "border-blue-200 bg-white"
                          : "border-slate-200 bg-slate-100/70 opacity-70"
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <label className="flex flex-1 cursor-pointer items-start gap-2">
                          <input
                            type="checkbox"
                            checked={dir.active}
                            disabled={busyAction}
                            onChange={(e) =>
                              handleToggleDirective(dir.directive_id, e.target.checked)
                            }
                            className="mt-0.5 rounded border-slate-300 text-[#004A8F]"
                          />
                          <span className="leading-snug text-slate-800">{dir.text}</span>
                        </label>
                        <button
                          type="button"
                          disabled={busyAction}
                          onClick={() => handleDeleteDirective(dir.directive_id)}
                          className="rounded p-1 text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}

          {/* TAB 3: SECTION VERSION HISTORY */}
          {activeTab === "versions" && (
            <div className="flex flex-1 flex-col overflow-hidden bg-slate-50/50 p-3">
              <div className="mb-2 text-[11px] text-slate-600">
                {lang === "en"
                  ? `Archived versions for: ${sectionTitle}`
                  : `Historique restaurable pour : ${sectionTitle}`}
              </div>
              <div className="flex-1 space-y-2 overflow-y-auto">
                {sectionVersions.length === 0 ? (
                  <div className="rounded-xl border border-dashed border-slate-300 bg-white p-4 text-center text-xs text-slate-500">
                    {lang === "en"
                      ? "No archived versions yet for this section. Every generation, manual edit, or proposal creates a restore point."
                      : "Aucune version archivée pour cette section. Chaque régénération, modification manuelle ou proposition crée automatiquement un point de restauration."}
                  </div>
                ) : (
                  sectionVersions.map((ver) => (
                    <div
                      key={ver.version_id}
                      className="rounded-xl border border-slate-200 bg-white p-2.5 text-xs shadow-xs"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] font-bold text-slate-700">
                            {ver.source}
                          </span>
                          <span className="text-[10px] text-slate-500">
                            {new Date(ver.created_at).toLocaleString()} · {ver.word_count}{" "}
                            {lang === "en" ? "words" : "mots"}
                          </span>
                        </div>
                        <button
                          type="button"
                          disabled={busyAction}
                          onClick={() =>
                            handleRestoreVersion(selectedSectionId, ver.version_id)
                          }
                          className="inline-flex items-center gap-1 rounded-lg bg-[#004A8F] px-2 py-1 text-[11px] font-semibold text-white hover:bg-[#003b73] disabled:opacity-40"
                        >
                          <RotateCcw className="h-3 w-3" />
                          <span>{lang === "en" ? "Restore" : "Restaurer"}</span>
                        </button>
                      </div>
                      {ver.note && (
                        <div className="mt-1 text-[10px] font-medium text-slate-500">
                          {ver.note}
                        </div>
                      )}
                      <p className="mt-1.5 line-clamp-2 text-[11px] text-slate-700">
                        {ver.content_markdown}
                      </p>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
