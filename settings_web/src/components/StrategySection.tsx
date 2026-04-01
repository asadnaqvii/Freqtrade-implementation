"use client";

import { useState } from "react";
import type { Strategy } from "@/lib/supabase";
import { updateStrategy } from "@/lib/supabase";

interface Props {
  strategy: Strategy | null;
  onUpdate: (strategy: Strategy) => void;
}

export default function StrategySection({ strategy, onUpdate }: Props) {
  const [form, setForm] = useState({
    stoploss_pct: strategy?.stoploss_pct ?? -5,
    max_open_trades: strategy?.max_open_trades ?? 3,
    timeframe: strategy?.timeframe ?? "5m",
  });
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  if (!strategy) {
    return (
      <section className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)] p-6">
        <h2 className="mb-4 text-lg font-semibold">Strategy</h2>
        <p className="text-sm text-[var(--text-secondary)]">
          No active strategy. Complete onboarding via WhatsApp to activate one.
        </p>
      </section>
    );
  }

  async function handleSave() {
    if (!strategy) return;
    setSaving(true);
    setSaved(false);
    const updated = await updateStrategy(strategy.id, {
      stoploss_pct: form.stoploss_pct,
      max_open_trades: form.max_open_trades,
      timeframe: form.timeframe,
    });
    setSaving(false);
    if (updated) {
      onUpdate(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    }
  }

  const roiEntries = Object.entries(strategy.roi_config).sort(
    ([a], [b]) => parseInt(a) - parseInt(b)
  );

  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)] p-6">
      <h2 className="mb-4 text-lg font-semibold">Strategy</h2>

      {/* Strategy Name & Status */}
      <div className="mb-4 flex items-center gap-3">
        <span className="text-xl font-bold">{strategy.strategy_name}</span>
        <span className="rounded-full bg-[var(--accent)]/20 px-2 py-0.5 text-xs text-[var(--accent)]">
          Active
        </span>
      </div>

      {strategy.reason && (
        <p className="mb-4 text-sm text-[var(--text-secondary)]">
          {strategy.reason}
        </p>
      )}

      <div className="grid gap-4 sm:grid-cols-3">
        {/* Stoploss */}
        <div>
          <label className="mb-1 block text-sm text-[var(--text-secondary)]">
            Stoploss (%)
          </label>
          <input
            type="number"
            step="0.5"
            className="w-full rounded-lg border border-[var(--border)] bg-[var(--bg-input)] px-3 py-2 text-sm focus:border-[var(--accent)] focus:outline-none"
            value={form.stoploss_pct}
            onChange={(e) =>
              setForm({ ...form, stoploss_pct: parseFloat(e.target.value) || 0 })
            }
          />
        </div>

        {/* Max Open Trades */}
        <div>
          <label className="mb-1 block text-sm text-[var(--text-secondary)]">
            Max Open Trades
          </label>
          <input
            type="number"
            min="1"
            max="20"
            className="w-full rounded-lg border border-[var(--border)] bg-[var(--bg-input)] px-3 py-2 text-sm focus:border-[var(--accent)] focus:outline-none"
            value={form.max_open_trades}
            onChange={(e) =>
              setForm({
                ...form,
                max_open_trades: parseInt(e.target.value) || 1,
              })
            }
          />
        </div>

        {/* Timeframe */}
        <div>
          <label className="mb-1 block text-sm text-[var(--text-secondary)]">
            Timeframe
          </label>
          <select
            className="w-full rounded-lg border border-[var(--border)] bg-[var(--bg-input)] px-3 py-2 text-sm focus:border-[var(--accent)] focus:outline-none"
            value={form.timeframe}
            onChange={(e) => setForm({ ...form, timeframe: e.target.value })}
          >
            <option value="1m">1m</option>
            <option value="5m">5m</option>
            <option value="15m">15m</option>
            <option value="1h">1h</option>
            <option value="4h">4h</option>
            <option value="1d">1d</option>
          </select>
        </div>
      </div>

      {/* ROI Table (read-only) */}
      {roiEntries.length > 0 && (
        <div className="mt-4">
          <label className="mb-2 block text-sm text-[var(--text-secondary)]">
            Take Profit Schedule
          </label>
          <div className="overflow-hidden rounded-lg border border-[var(--border)]">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-[var(--bg-input)]">
                  <th className="px-3 py-2 text-left font-medium text-[var(--text-secondary)]">
                    Minutes
                  </th>
                  <th className="px-3 py-2 text-left font-medium text-[var(--text-secondary)]">
                    Profit Target
                  </th>
                </tr>
              </thead>
              <tbody>
                {roiEntries.map(([minutes, pct]) => (
                  <tr key={minutes} className="border-t border-[var(--border)]">
                    <td className="px-3 py-2">{minutes} min</td>
                    <td className="px-3 py-2">
                      {(pct * 100).toFixed(1)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Meta */}
      <p className="mt-4 text-xs text-[var(--text-secondary)]">
        Activated {new Date(strategy.activated_at).toLocaleDateString()} by{" "}
        {strategy.activated_by}
      </p>

      {/* Save */}
      <div className="mt-4 flex items-center gap-3">
        <button
          onClick={handleSave}
          disabled={saving}
          className="rounded-lg bg-[var(--accent)] px-6 py-2 text-sm font-medium text-black transition hover:bg-[var(--accent-hover)] disabled:opacity-50"
        >
          {saving ? "Saving..." : "Update Strategy"}
        </button>
        {saved && (
          <span className="text-sm text-[var(--accent)]">Saved!</span>
        )}
      </div>
    </section>
  );
}
