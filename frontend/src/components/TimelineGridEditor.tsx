import { CalendarPlus, Plus, Trash2, X } from "lucide-react";
import type { TimelineGrid } from "../types/protocol";

interface TimelineGridEditorProps {
  timelineGrid: TimelineGrid;
  onChange: (nextGrid: TimelineGrid) => void;
  onCommit?: (nextGrid: TimelineGrid) => void;
  variant?: "preview" | "tables";
  saving?: boolean;
}

export function TimelineGridEditor({
  timelineGrid,
  onChange,
  onCommit,
  variant = "tables",
  saving = false,
}: TimelineGridEditorProps) {
  const periods = timelineGrid.periods || [];
  const rows = timelineGrid.rows || [];

  const applyChange = (next: TimelineGrid, commitNow = false) => {
    onChange(next);
    if (commitNow && onCommit) {
      onCommit(next);
    }
  };

  const handleRenamePeriod = (periodIdx: number, newLabel: string) => {
    const oldLabel = periods[periodIdx];
    const nextPeriods = periods.map((p, i) => (i === periodIdx ? newLabel : p));
    const nextRows = rows.map((row) => ({
      ...row,
      active_periods: Array.from(
        new Set(
          (row.active_periods || []).map((p) => (p === oldLabel ? newLabel : p))
        )
      ),
    }));
    applyChange({ periods: nextPeriods, rows: nextRows }, false);
  };

  const handleAddPeriod = () => {
    let idx = periods.length + 1;
    let candidate = `Période ${idx}`;
    while (periods.includes(candidate)) {
      idx += 1;
      candidate = `Période ${idx}`;
    }
    const next: TimelineGrid = {
      periods: [...periods, candidate],
      rows: rows.map((r) => ({ ...r })),
    };
    applyChange(next, true);
  };

  const handleRemovePeriod = (periodIdx: number) => {
    if (periods.length <= 1) return;
    const removed = periods[periodIdx];
    const next: TimelineGrid = {
      periods: periods.filter((_, i) => i !== periodIdx),
      rows: rows.map((r) => ({
        ...r,
        active_periods: (r.active_periods || []).filter((p) => p !== removed),
      })),
    };
    applyChange(next, true);
  };

  const handleUpdateActivity = (rowIdx: number, newActivity: string) => {
    const next: TimelineGrid = {
      periods: [...periods],
      rows: rows.map((r, i) =>
        i === rowIdx ? { ...r, activity: newActivity } : r
      ),
    };
    applyChange(next, false);
  };

  const handleAddRow = () => {
    const defaultPeriod = periods[0] ? [periods[0]] : [];
    const next: TimelineGrid = {
      periods: [...periods],
      rows: [
        ...rows,
        {
          activity: `Nouvelle activité ${rows.length + 1}`,
          active_periods: defaultPeriod,
        },
      ],
    };
    applyChange(next, true);
  };

  const handleRemoveRow = (rowIdx: number) => {
    if (rows.length <= 1) return;
    const next: TimelineGrid = {
      periods: [...periods],
      rows: rows.filter((_, i) => i !== rowIdx),
    };
    applyChange(next, true);
  };

  const handleToggleCell = (rowIdx: number, period: string) => {
    const row = rows[rowIdx];
    if (!row) return;
    const active = row.active_periods || [];
    const has = active.includes(period);
    const nextActive = has
      ? active.filter((p) => p !== period)
      : Array.from(new Set([...active, period]));
    const next: TimelineGrid = {
      periods: [...periods],
      rows: rows.map((r, i) =>
        i === rowIdx ? { ...r, active_periods: nextActive } : r
      ),
    };
    applyChange(next, true);
  };

  const isPreview = variant === "preview";

  return (
    <div className="space-y-2 font-sans">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            onClick={handleAddRow}
            className="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2 py-1 text-[11px] font-semibold text-slate-700 hover:bg-slate-50"
          >
            <Plus className="h-3 w-3 text-[#6C2826]" />
            Ajouter une activité
          </button>
          <button
            type="button"
            onClick={handleAddPeriod}
            className="inline-flex items-center gap-1 rounded border border-slate-300 bg-white px-2 py-1 text-[11px] font-semibold text-slate-700 hover:bg-slate-50"
          >
            <CalendarPlus className="h-3 w-3 text-indigo-600" />
            Ajouter une date
          </button>
        </div>
        {saving && (
          <span className="text-[11px] font-medium text-indigo-600">
            Enregistrement du chronogramme…
          </span>
        )}
      </div>

      <div className="overflow-x-auto rounded border border-slate-200 bg-white">
        <table className="w-full text-left text-xs">
          <thead className="bg-[#6C2826] text-white">
            <tr>
              <th className={isPreview ? "min-w-[180px] p-1.5" : "min-w-[200px] p-2"}>
                Activité FMSB
              </th>
              {periods.map((per, pi) => (
                <th
                  key={pi}
                  className={
                    isPreview
                      ? "min-w-[82px] p-1 text-center"
                      : "min-w-[92px] p-1.5 text-center"
                  }
                >
                  <div className="group flex items-center justify-center gap-0.5">
                    <input
                      type="text"
                      aria-label={`Date colonne ${pi + 1}`}
                      value={per}
                      onChange={(e) => handleRenamePeriod(pi, e.target.value)}
                      onBlur={() => onCommit?.(timelineGrid)}
                      className="w-full min-w-[64px] rounded border border-transparent bg-white/10 px-1 py-0.5 text-center text-[11px] font-bold text-white placeholder:text-white/60 hover:bg-white/20 focus:border-white/60 focus:bg-white focus:text-slate-900 focus:outline-none"
                    />
                    {periods.length > 1 && (
                      <button
                        type="button"
                        onClick={() => handleRemovePeriod(pi)}
                        title={`Supprimer la date « ${per} »`}
                        className="rounded p-0.5 text-white/70 opacity-70 hover:bg-white/20 hover:text-white hover:opacity-100"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    )}
                  </div>
                </th>
              ))}
              <th className="w-8 p-1"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-200">
            {rows.map((row, ri) => (
              <tr key={ri} className="hover:bg-slate-50/70">
                <td className={isPreview ? "p-1" : "p-1.5"}>
                  <input
                    type="text"
                    aria-label={`Activité ligne ${ri + 1}`}
                    value={row.activity}
                    onChange={(e) => handleUpdateActivity(ri, e.target.value)}
                    onBlur={() => onCommit?.(timelineGrid)}
                    className="w-full rounded border border-transparent px-1.5 py-1 text-xs font-medium text-slate-800 hover:border-slate-300 focus:border-[#6C2826] focus:bg-white focus:outline-none"
                  />
                </td>
                {periods.map((per, pi) => {
                  const isChecked = (row.active_periods || []).includes(per);
                  return (
                    <td
                      key={pi}
                      className={`${
                        isPreview ? "p-1" : "p-1.5"
                      } text-center transition ${
                        isChecked ? "bg-amber-100/80" : ""
                      }`}
                    >
                      {isPreview ? (
                        <button
                          type="button"
                          onClick={() => handleToggleCell(ri, per)}
                          aria-label={`${row.activity} — ${per}`}
                          aria-pressed={isChecked}
                          title={`Cliquer pour ${
                            isChecked ? "désactiver" : "activer"
                          } « ${per} »`}
                          className={`mx-auto flex h-6 w-full items-center justify-center rounded text-xs font-bold transition ${
                            isChecked
                              ? "text-[#6C2826] hover:bg-amber-200/70"
                              : "text-slate-300 hover:bg-slate-100 hover:text-slate-500"
                          }`}
                        >
                          {isChecked ? "■" : "·"}
                        </button>
                      ) : (
                        <input
                          type="checkbox"
                          checked={isChecked}
                          aria-label={`${row.activity} — ${per}`}
                          onChange={() => handleToggleCell(ri, per)}
                          className="h-4 w-4 rounded text-[#6C2826] focus:ring-[#6C2826]"
                        />
                      )}
                    </td>
                  );
                })}
                <td className="p-1 text-center">
                  {rows.length > 1 && (
                    <button
                      type="button"
                      onClick={() => handleRemoveRow(ri)}
                      title="Supprimer cette activité"
                      className="rounded p-1 text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
