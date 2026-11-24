import { useState } from "react";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Cpu,
  Plus,
  Sparkles,
  Trash2,
} from "lucide-react";
import type {
  ModelCatalogItemOut,
  ModelCatalogResponse,
  ModelRoutingEntry,
  UserModelDailyUsageOut,
  UserModelRoutingConfig,
} from "../types/admin";

interface ModelRoutingEditorProps {
  catalog: ModelCatalogResponse | null;
  value: UserModelRoutingConfig;
  onChange: (next: UserModelRoutingConfig) => void;
  dailyUsage?: UserModelDailyUsageOut[];
  compact?: boolean;
}

const RANK_LABELS = [
  "1 · Primaire",
  "2 · Secondaire",
  "3 · Backup 3",
  "4 · Backup 4",
  "5 · Backup 5",
  "6 · Backup 6",
  "7 · Backup 7",
  "8 · Backup 8",
  "9 · Backup 9",
];

function rankBadgeClass(index: number): string {
  if (index === 0) return "bg-indigo-600 text-white";
  if (index === 1) return "bg-sky-100 text-sky-800 border border-sky-200";
  return "bg-slate-100 text-slate-700 border border-slate-200";
}

function tierBadge(tier: string): { label: string; cls: string } {
  if (tier === "vip") return { label: "VIP", cls: "bg-amber-100 text-amber-800" };
  if (tier === "lite") return { label: "Lite", cls: "bg-emerald-100 text-emerald-800" };
  return { label: "Flash", cls: "bg-blue-100 text-blue-800" };
}

const FALLBACK_CATALOG: ModelCatalogResponse = {
  models: [
    { id: "gemini-3.8-flash", label: "Gemini 3.8 Flash", tier: "vip", in_use: false, free_tier_rpm: 5, free_tier_tpm: 250000, free_tier_rpd: 20, description: "Modèle Flash le plus intelligent — idéal en Primaire VIP." },
    { id: "gemini-3.7-flash", label: "Gemini 3.7 Flash", tier: "vip", in_use: false, free_tier_rpm: 5, free_tier_tpm: 250000, free_tier_rpd: 20, description: "Flash haute qualité (génération 3.7)." },
    { id: "gemini-3.6-flash", label: "Gemini 3.6 Flash", tier: "vip", in_use: false, free_tier_rpm: 5, free_tier_tpm: 250000, free_tier_rpd: 20, description: "Flash rapide et équilibré (génération 3.6)." },
    { id: "gemini-3.5-flash", label: "Gemini 3.5 Flash", tier: "standard", in_use: true, free_tier_rpm: 5, free_tier_tpm: 250000, free_tier_rpd: 20, description: "Actuellement utilisé — backup haute qualité." },
    { id: "gemini-3.5-flash-lite", label: "Gemini 3.5 Flash-Lite", tier: "lite", in_use: true, free_tier_rpm: 15, free_tier_tpm: 250000, free_tier_rpd: 20, description: "Actuellement utilisé — rapide." },
    { id: "gemini-3.1-flash-lite", label: "Gemini 3.1 Flash-Lite", tier: "lite", in_use: true, free_tier_rpm: 15, free_tier_tpm: 250000, free_tier_rpd: 20, description: "Actuellement utilisé — relais Flash-Lite." },
    { id: "gemini-2.5-pro", label: "Gemini 2.5 Pro", tier: "vip", in_use: false, free_tier_rpm: 5, free_tier_tpm: 250000, free_tier_rpd: 20, description: "Raisonnement approfondi." },
    { id: "gemini-2.5-flash", label: "Gemini 2.5 Flash", tier: "standard", in_use: false, free_tier_rpm: 5, free_tier_tpm: 250000, free_tier_rpd: 20, description: "Backup stable 2.5." },
    { id: "gemini-2.5-flash-lite", label: "Gemini 2.5 Flash-Lite", tier: "lite", in_use: false, free_tier_rpm: 15, free_tier_tpm: 250000, free_tier_rpd: 20, description: "Filet de sécurité 2.5 Flash-Lite." },
  ],
  presets: [
    {
      id: "vip_38_10rpd",
      label: "VIP 3.8 Flash (10 req/j → 3.6 → Lite)",
      description: "10 premières requêtes/jour sur Gemini 3.8 Flash, puis 10 sur 3.6 Flash, puis Flash-Lite.",
      config: {
        enabled: true,
        models: [
          { model: "gemini-3.8-flash", max_rpd: 10, max_tpm: 250000 },
          { model: "gemini-3.6-flash", max_rpd: 10, max_tpm: 250000 },
          { model: "gemini-3.5-flash-lite", max_rpd: 20, max_tpm: 250000 },
          { model: "gemini-3.1-flash-lite", max_rpd: 20, max_tpm: 250000 },
          { model: "gemini-3.5-flash", max_rpd: 20, max_tpm: 250000 },
        ],
      },
    },
    {
      id: "vip_36_10rpd",
      label: "VIP 3.6 Flash (10 req/j → Lite)",
      description: "10 premières requêtes/jour sur Gemini 3.6 Flash, puis relais Flash-Lite.",
      config: {
        enabled: true,
        models: [
          { model: "gemini-3.6-flash", max_rpd: 10, max_tpm: 250000 },
          { model: "gemini-3.5-flash-lite", max_rpd: 20, max_tpm: 250000 },
          { model: "gemini-3.1-flash-lite", max_rpd: 20, max_tpm: 250000 },
        ],
      },
    },
  ],
  note: "Plafonds de référence Free Tier Gemini : 20 RPD · 250 000 TPM par modèle (vérifiez sur aistudio.google.com/rate-limit).",
};

export function ModelRoutingEditor({
  catalog,
  value,
  onChange,
  dailyUsage,
  compact = false,
}: ModelRoutingEditorProps) {
  const [selectedToAdd, setSelectedToAdd] = useState("");
  const effectiveCatalog = catalog && catalog.models.length > 0 ? catalog : FALLBACK_CATALOG;
  const modelsCatalog = effectiveCatalog.models;
  const presets = effectiveCatalog.presets;

  const catalogById = new Map<string, ModelCatalogItemOut>(
    modelsCatalog.map((m) => [m.id.toLowerCase(), m])
  );
  const usageByModel = new Map<string, UserModelDailyUsageOut>(
    (dailyUsage ?? []).map((u) => [u.model.toLowerCase(), u])
  );

  const usedIds = new Set(value.models.map((m) => m.model.toLowerCase()));
  const availableToAdd = modelsCatalog.filter((m) => !usedIds.has(m.id.toLowerCase()));

  function setEnabled(enabled: boolean) {
    if (!enabled) {
      onChange({ enabled: false, models: value.models });
      return;
    }
    if (value.models.length > 0) {
      onChange({ enabled: true, models: value.models });
      return;
    }
    // Seed with the exact current production models so enabling custom mode never disrupts service
    const starter: ModelRoutingEntry[] = [
      { model: "gemini-3.5-flash-lite", max_rpd: 20, max_tpm: 250000 },
      { model: "gemini-3.1-flash-lite", max_rpd: 20, max_tpm: 250000 },
      { model: "gemini-3.5-flash", max_rpd: 20, max_tpm: 250000 },
    ];
    onChange({ enabled: true, models: starter });
  }

  function applyPreset(presetConfig: UserModelRoutingConfig) {
    onChange({
      enabled: true,
      models: presetConfig.models.map((m) => ({ ...m })),
    });
  }

  function moveEntry(idx: number, delta: -1 | 1) {
    const target = idx + delta;
    if (target < 0 || target >= value.models.length) return;
    const next = [...value.models];
    const [moved] = next.splice(idx, 1);
    next.splice(target, 0, moved);
    onChange({ ...value, enabled: true, models: next });
  }

  function removeEntry(idx: number) {
    const next = value.models.filter((_, i) => i !== idx);
    onChange({ ...value, models: next });
  }

  function updateEntry(idx: number, patch: Partial<ModelRoutingEntry>) {
    const next = value.models.map((entry, i) =>
      i === idx ? { ...entry, ...patch } : entry
    );
    onChange({ ...value, enabled: true, models: next });
  }

  function addModel(modelId: string) {
    if (!modelId) return;
    const meta = catalogById.get(modelId.toLowerCase());
    const next: ModelRoutingEntry = {
      model: modelId,
      max_rpd: meta ? Math.min(meta.free_tier_rpd, meta.tier === "vip" ? 10 : 20) : 20,
      max_tpm: meta ? meta.free_tier_tpm : 250000,
    };
    onChange({
      enabled: true,
      models: [...value.models, next],
    });
    setSelectedToAdd("");
  }

  return (
    <div className="space-y-3">
      {/* Mode toggle */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-0.5 text-[11px] font-semibold">
          <button
            type="button"
            onClick={() => setEnabled(false)}
            className={`rounded-md px-2.5 py-1 transition ${
              !value.enabled
                ? "bg-white text-slate-900 shadow-sm"
                : "text-slate-500 hover:text-slate-800"
            }`}
          >
            Chaîne système par défaut
          </button>
          <button
            type="button"
            onClick={() => setEnabled(true)}
            className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 transition ${
              value.enabled
                ? "bg-indigo-600 text-white shadow-sm"
                : "text-slate-500 hover:text-slate-800"
            }`}
          >
            <Cpu className="h-3 w-3" />
            Chaîne personnalisée
          </button>
        </div>
        {!value.enabled && (
          <span className="text-[11px] text-slate-500">
            Défaut : <code>3.5-flash-lite</code> → <code>3.1-flash-lite</code> → <code>3.5-flash</code>
          </span>
        )}
      </div>

      {value.enabled && (
        <div className="space-y-3 rounded-xl border border-indigo-200 bg-indigo-50/30 p-3">
          {/* Presets */}
          {presets.length > 0 && (
            <div>
              <div className="mb-1.5 flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-indigo-800">
                <Sparkles className="h-3 w-3" />
                Modèles préconfigurés (1 clic)
              </div>
              <div className="flex flex-wrap gap-1.5">
                {presets.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => applyPreset(p.config)}
                    title={p.description}
                    className="rounded-lg border border-indigo-200 bg-white px-2.5 py-1 text-[11px] font-semibold text-indigo-800 hover:bg-indigo-50 transition"
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Ordered model chain */}
          {value.models.length === 0 ? (
            <div className="rounded-lg border border-dashed border-amber-300 bg-amber-50/60 p-3 text-center text-xs text-amber-900">
              Aucun modèle dans la chaîne. Ajoutez au moins un modèle ci-dessous ou cliquez sur un preset.
            </div>
          ) : (
            <div className="space-y-2">
              {value.models.map((entry, idx) => {
                const meta = catalogById.get(entry.model.toLowerCase());
                const usage = usageByModel.get(entry.model.toLowerCase());
                const overRpd =
                  meta != null &&
                  entry.max_rpd != null &&
                  entry.max_rpd > meta.free_tier_rpd;
                const overTpm =
                  meta != null &&
                  entry.max_tpm != null &&
                  entry.max_tpm > meta.free_tier_tpm;
                const tb = tierBadge(meta?.tier ?? "standard");

                return (
                  <div
                    key={entry.model}
                    className="rounded-lg border border-slate-200 bg-white p-2.5 shadow-xs"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span
                          className={`rounded-md px-2 py-0.5 text-[10px] font-bold ${rankBadgeClass(
                            idx
                          )}`}
                        >
                          {RANK_LABELS[idx] ?? `${idx + 1} · Backup ${idx + 1}`}
                        </span>
                        <span className="font-mono text-xs font-bold text-slate-900">
                          {entry.model}
                        </span>
                        <span
                          className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${tb.cls}`}
                        >
                          {tb.label}
                        </span>
                        {meta?.in_use && (
                          <span className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-600">
                            Système
                          </span>
                        )}
                        {usage && (
                          <span
                            className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                              entry.max_rpd != null && usage.rpd_used >= entry.max_rpd
                                ? "bg-rose-100 text-rose-700"
                                : "bg-emerald-50 text-emerald-700"
                            }`}
                          >
                            Auj. : {usage.rpd_used}
                            {entry.max_rpd != null ? ` / ${entry.max_rpd}` : ""} req
                          </span>
                        )}
                      </div>

                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          disabled={idx === 0}
                          onClick={() => moveEntry(idx, -1)}
                          title="Monter en priorité"
                          className="rounded border border-slate-200 p-1 text-slate-600 hover:bg-slate-100 disabled:opacity-30"
                        >
                          <ArrowUp className="h-3.5 w-3.5" />
                        </button>
                        <button
                          type="button"
                          disabled={idx === value.models.length - 1}
                          onClick={() => moveEntry(idx, 1)}
                          title="Descendre en priorité"
                          className="rounded border border-slate-200 p-1 text-slate-600 hover:bg-slate-100 disabled:opacity-30"
                        >
                          <ArrowDown className="h-3.5 w-3.5" />
                        </button>
                        <button
                          type="button"
                          onClick={() => removeEntry(idx)}
                          title="Retirer ce modèle"
                          className="rounded border border-rose-200 p-1 text-rose-600 hover:bg-rose-50"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>

                    {/* RPD & TPM limits */}
                    <div
                      className={`mt-2 grid gap-2 ${
                        compact ? "grid-cols-1 sm:grid-cols-2" : "grid-cols-2"
                      }`}
                    >
                      <div>
                        <label className="block text-[10px] font-semibold text-slate-600">
                          Max requêtes / jour (RPD)
                        </label>
                        <input
                          type="number"
                          min={0}
                          max={100000}
                          placeholder="Illimité"
                          value={entry.max_rpd ?? ""}
                          onChange={(e) => {
                            const raw = e.target.value.trim();
                            updateEntry(idx, {
                              max_rpd: raw === "" ? null : Math.max(0, Number(raw)),
                            });
                          }}
                          className={`mt-0.5 w-full rounded border px-2 py-1 text-xs font-mono ${
                            overRpd
                              ? "border-amber-400 bg-amber-50/50 text-amber-900"
                              : "border-slate-300 bg-white"
                          }`}
                        />
                        {meta && (
                          <div className="mt-0.5 flex items-center gap-1 text-[10px] text-slate-500">
                            {overRpd && (
                              <AlertTriangle className="h-3 w-3 text-amber-600 shrink-0" />
                            )}
                            <span className={overRpd ? "text-amber-700 font-semibold" : ""}>
                              Max Free Tier : ~{meta.free_tier_rpd.toLocaleString("fr-FR")} RPD (~
                              {meta.free_tier_rpm} RPM)
                            </span>
                          </div>
                        )}
                      </div>

                      <div>
                        <label className="block text-[10px] font-semibold text-slate-600">
                          Max tokens / minute (TPM)
                        </label>
                        <input
                          type="number"
                          min={0}
                          max={50000000}
                          step={10000}
                          placeholder="Illimité"
                          value={entry.max_tpm ?? ""}
                          onChange={(e) => {
                            const raw = e.target.value.trim();
                            updateEntry(idx, {
                              max_tpm: raw === "" ? null : Math.max(0, Number(raw)),
                            });
                          }}
                          className={`mt-0.5 w-full rounded border px-2 py-1 text-xs font-mono ${
                            overTpm
                              ? "border-amber-400 bg-amber-50/50 text-amber-900"
                              : "border-slate-300 bg-white"
                          }`}
                        />
                        {meta && (
                          <div className="mt-0.5 flex items-center gap-1 text-[10px] text-slate-500">
                            {overTpm && (
                              <AlertTriangle className="h-3 w-3 text-amber-600 shrink-0" />
                            )}
                            <span className={overTpm ? "text-amber-700 font-semibold" : ""}>
                              Max Free Tier : ~{meta.free_tier_tpm.toLocaleString("fr-FR")} TPM
                            </span>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Add model dropdown */}
          {availableToAdd.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <select
                value={selectedToAdd}
                onChange={(e) => setSelectedToAdd(e.target.value)}
                className="flex-1 min-w-[200px] rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs"
              >
                <option value="">+ Ajouter un modèle Gemini à la chaîne...</option>
                {availableToAdd.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label} ({m.id}) — Free Tier ~{m.free_tier_rpd} RPD /{" "}
                    {Math.round(m.free_tier_tpm / 1000)}k TPM
                    {m.in_use ? " [Actuel]" : ""}
                  </option>
                ))}
              </select>
              <button
                type="button"
                disabled={!selectedToAdd}
                onClick={() => addModel(selectedToAdd)}
                className="inline-flex items-center gap-1 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-40"
              >
                <Plus className="h-3.5 w-3.5" />
                Ajouter
              </button>
            </div>
          )}

          {effectiveCatalog.note && (
            <p className="text-[10px] leading-relaxed text-slate-500">{effectiveCatalog.note}</p>
          )}
        </div>
      )}
    </div>
  );
}
