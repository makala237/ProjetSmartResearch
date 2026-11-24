import { useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Compass,
  Lightbulb,
  Sparkles,
  X,
} from "lucide-react";
import {
  WELCOME_TOUR_STEPS,
  type FeatureGuideSpec,
} from "../data/onboardingGuides";
import type { ModuleKey } from "../types/auth";

interface WelcomeTourModalProps {
  lang: "fr" | "en";
  onJumpModule?: (moduleKey: ModuleKey) => void;
  onComplete: () => void;
  onDismiss: () => void;
}

export function WelcomeTourModal({
  lang,
  onJumpModule,
  onComplete,
  onDismiss,
}: WelcomeTourModalProps) {
  const [stepIndex, setStepIndex] = useState(0);
  const steps = WELCOME_TOUR_STEPS;
  const current = steps[stepIndex] ?? steps[0];
  const isFirst = stepIndex === 0;
  const isLast = stepIndex === steps.length - 1;
  const progressPct = Math.round(((stepIndex + 1) / steps.length) * 100);

  const title = lang === "en" ? current.titleEn : current.titleFr;
  const summary = lang === "en" ? current.summaryEn : current.summaryFr;
  const tips = lang === "en" ? current.tipsEn : current.tipsFr;

  return (
    <div
      data-testid="welcome-tour-modal"
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/55 p-4 backdrop-blur-xs"
    >
      <div className="w-full max-w-xl overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl">
        {/* Top bar */}
        <div className="bg-[#004A8F] px-5 py-4 text-white">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm font-bold">
              <Compass className="h-5 w-5 text-amber-300" />
              <span>
                {lang === "en"
                  ? "Smart Research Interactive Guide"
                  : "Visite guidée Smart Research"}
              </span>
            </div>
            <button
              type="button"
              onClick={onDismiss}
              title={lang === "en" ? "Skip tour" : "Passer la visite"}
              className="rounded-lg p-1 text-blue-100 hover:bg-white/15 hover:text-white"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="mt-3 flex items-center justify-between text-[11px] text-blue-100">
            <span>
              {lang === "en" ? "Step" : "Étape"} {stepIndex + 1} / {steps.length}
            </span>
            <span>{progressPct}%</span>
          </div>
          <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-white/20">
            <div
              className="h-full rounded-full bg-amber-300 transition-all duration-300"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>

        {/* Body */}
        <div className="space-y-4 p-5">
          <div>
            <h3 className="text-lg font-bold text-slate-900">{title}</h3>
            <p className="mt-1.5 text-sm leading-relaxed text-slate-600">
              {summary}
            </p>
          </div>

          <div className="space-y-2 rounded-xl border border-blue-100 bg-blue-50/60 p-3.5">
            {tips.map((tip, idx) => (
              <div
                key={idx}
                className="flex items-start gap-2 text-xs leading-relaxed text-slate-700"
              >
                <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#004A8F]" />
                <span>{tip}</span>
              </div>
            ))}
          </div>

          {current.targetModule && onJumpModule && (
            <button
              type="button"
              onClick={() => onJumpModule(current.targetModule!)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:border-[#004A8F]/40 hover:bg-blue-50"
            >
              <span>
                {lang === "en"
                  ? "Preview this module in background"
                  : "Afficher ce module en arrière-plan"}
              </span>
              <ArrowRight className="h-3.5 w-3.5 text-[#004A8F]" />
            </button>
          )}
        </div>

        {/* Footer */}
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 bg-slate-50 px-5 py-3.5">
          <button
            type="button"
            onClick={onDismiss}
            className="text-xs font-semibold text-slate-500 hover:text-slate-800"
          >
            {lang === "en" ? "Skip for now" : "Passer pour l'instant"}
          </button>

          <div className="flex items-center gap-2">
            {!isFirst && (
              <button
                type="button"
                onClick={() => setStepIndex((i) => Math.max(0, i - 1))}
                className="inline-flex items-center gap-1 rounded-xl border border-slate-300 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-100"
              >
                <ArrowLeft className="h-3.5 w-3.5" />
                <span>{lang === "en" ? "Previous" : "Précédent"}</span>
              </button>
            )}
            {!isLast ? (
              <button
                type="button"
                onClick={() => setStepIndex((i) => Math.min(steps.length - 1, i + 1))}
                className="inline-flex items-center gap-1 rounded-xl bg-[#004A8F] px-4 py-2 text-xs font-bold text-white hover:bg-[#003b73]"
              >
                <span>{lang === "en" ? "Next" : "Suivant"}</span>
                <ArrowRight className="h-3.5 w-3.5" />
              </button>
            ) : (
              <button
                type="button"
                onClick={onComplete}
                className="inline-flex items-center gap-1.5 rounded-xl bg-emerald-600 px-4 py-2 text-xs font-bold text-white hover:bg-emerald-700"
              >
                <CheckCircle2 className="h-4 w-4" />
                <span>{lang === "en" ? "Get started" : "Terminer & Commencer"}</span>
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

interface FeatureDiscoveryCardProps {
  guide: FeatureGuideSpec;
  lang: "fr" | "en";
  onAcknowledge: () => void;
  onAskAssistant: (prompt: string) => void;
  onDisableTips: () => void;
}

export function FeatureDiscoveryCard({
  guide,
  lang,
  onAcknowledge,
  onAskAssistant,
  onDisableTips,
}: FeatureDiscoveryCardProps) {
  const title = lang === "en" ? guide.titleEn : guide.titleFr;
  const summary = lang === "en" ? guide.summaryEn : guide.summaryFr;
  const tips = lang === "en" ? guide.tipsEn : guide.tipsFr;
  const askPrompt = lang === "en" ? guide.askPromptEn : guide.askPromptFr;

  return (
    <div
      data-testid="feature-discovery-card"
      className="fixed bottom-36 right-4 z-40 w-[350px] max-w-[calc(100vw-2rem)] overflow-hidden rounded-2xl border border-amber-200 bg-white shadow-2xl md:bottom-20"
    >
      <div className="flex items-center justify-between gap-2 bg-amber-50 px-3.5 py-2.5 border-b border-amber-200/80">
        <div className="flex items-center gap-1.5 text-xs font-bold text-amber-900">
          <Lightbulb className="h-4 w-4 shrink-0 text-amber-600" />
          <span>
            {lang === "en" ? "First-time discovery" : "Découverte (1re utilisation)"}
          </span>
        </div>
        <button
          type="button"
          onClick={onAcknowledge}
          className="rounded p-0.5 text-amber-700 hover:bg-amber-100"
          title={lang === "en" ? "Close" : "Fermer"}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="space-y-2.5 p-3.5 text-xs">
        <div className="font-bold text-slate-900">{title}</div>
        <p className="leading-relaxed text-slate-600">{summary}</p>
        {tips.length > 0 && (
          <ul className="space-y-1 rounded-lg bg-slate-50 p-2 text-[11px] text-slate-700">
            {tips.map((tip, i) => (
              <li key={i} className="flex items-start gap-1.5">
                <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-[#004A8F]" />
                <span>{tip}</span>
              </li>
            ))}
          </ul>
        )}

        <div className="flex flex-wrap items-center gap-1.5 pt-1">
          <button
            type="button"
            onClick={onAcknowledge}
            className="rounded-lg bg-[#004A8F] px-2.5 py-1.5 text-[11px] font-bold text-white hover:bg-[#003b73]"
          >
            {lang === "en" ? "Got it" : "Compris"}
          </button>
          <button
            type="button"
            onClick={() => onAskAssistant(askPrompt)}
            className="inline-flex items-center gap-1 rounded-lg border border-blue-200 bg-blue-50 px-2.5 py-1.5 text-[11px] font-semibold text-[#004A8F] hover:bg-blue-100"
          >
            <Sparkles className="h-3 w-3" />
            <span>
              {lang === "en" ? "Ask Assistant" : "Demander à l'Assistant"}
            </span>
          </button>
          <button
            type="button"
            onClick={onDisableTips}
            className="ml-auto text-[10px] font-medium text-slate-400 hover:text-slate-700"
          >
            {lang === "en" ? "Disable tips" : "Désactiver les astuces"}
          </button>
        </div>
      </div>
    </div>
  );
}
