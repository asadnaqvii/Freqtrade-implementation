"use client";

import { useState } from "react";
import type { User } from "@/lib/supabase";
import { updateUser } from "@/lib/supabase";

interface Props {
  user: User;
  onUpdate: (user: User) => void;
}

export default function ProfileSection({ user, onUpdate }: Props) {
  const [form, setForm] = useState({
    name: user.name || "",
    risk_tolerance: user.risk_tolerance,
    yield_target_pct: user.yield_target_pct,
    max_drawdown_pct: user.max_drawdown_pct,
    trading_style: user.trading_style,
    pairs_blacklist: user.pairs_blacklist.join(", "),
    automation_mode: user.automation_mode,
  });
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  async function handleSave() {
    setSaving(true);
    setSaved(false);
    const updated = await updateUser(user.id, {
      name: form.name || null,
      risk_tolerance: form.risk_tolerance,
      yield_target_pct: form.yield_target_pct,
      max_drawdown_pct: form.max_drawdown_pct,
      trading_style: form.trading_style,
      pairs_blacklist: form.pairs_blacklist
        .split(",")
        .map((p) => p.trim())
        .filter(Boolean),
      automation_mode: form.automation_mode,
    });
    setSaving(false);
    if (updated) {
      onUpdate(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    }
  }

  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)] p-6">
      <h2 className="mb-4 text-lg font-semibold">Profile</h2>

      <div className="grid gap-4 sm:grid-cols-2">
        {/* Name */}
        <div>
          <label className="mb-1 block text-sm text-[var(--text-secondary)]">
            Name
          </label>
          <input
            className="w-full rounded-lg border border-[var(--border)] bg-[var(--bg-input)] px-3 py-2 text-sm focus:border-[var(--accent)] focus:outline-none"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </div>

        {/* Phone (read-only) */}
        <div>
          <label className="mb-1 block text-sm text-[var(--text-secondary)]">
            WhatsApp Number
          </label>
          <input
            className="w-full rounded-lg border border-[var(--border)] bg-[var(--bg-input)] px-3 py-2 text-sm opacity-60"
            value={user.whatsapp_number}
            disabled
          />
        </div>

        {/* Risk Tolerance */}
        <div>
          <label className="mb-1 block text-sm text-[var(--text-secondary)]">
            Risk Tolerance
          </label>
          <select
            className="w-full rounded-lg border border-[var(--border)] bg-[var(--bg-input)] px-3 py-2 text-sm focus:border-[var(--accent)] focus:outline-none"
            value={form.risk_tolerance}
            onChange={(e) =>
              setForm({
                ...form,
                risk_tolerance: e.target.value as User["risk_tolerance"],
              })
            }
          >
            <option value="conservative">Conservative</option>
            <option value="moderate">Moderate</option>
            <option value="aggressive">Aggressive</option>
          </select>
        </div>

        {/* Trading Style */}
        <div>
          <label className="mb-1 block text-sm text-[var(--text-secondary)]">
            Trading Style
          </label>
          <select
            className="w-full rounded-lg border border-[var(--border)] bg-[var(--bg-input)] px-3 py-2 text-sm focus:border-[var(--accent)] focus:outline-none"
            value={form.trading_style}
            onChange={(e) =>
              setForm({
                ...form,
                trading_style: e.target.value as User["trading_style"],
              })
            }
          >
            <option value="patient">Patient</option>
            <option value="active">Active</option>
          </select>
        </div>

        {/* Yield Target */}
        <div>
          <label className="mb-1 block text-sm text-[var(--text-secondary)]">
            Monthly Yield Target (%)
          </label>
          <input
            type="number"
            step="0.5"
            className="w-full rounded-lg border border-[var(--border)] bg-[var(--bg-input)] px-3 py-2 text-sm focus:border-[var(--accent)] focus:outline-none"
            value={form.yield_target_pct}
            onChange={(e) =>
              setForm({ ...form, yield_target_pct: parseFloat(e.target.value) || 0 })
            }
          />
        </div>

        {/* Max Drawdown */}
        <div>
          <label className="mb-1 block text-sm text-[var(--text-secondary)]">
            Max Monthly Drawdown (%)
          </label>
          <input
            type="number"
            step="0.5"
            className="w-full rounded-lg border border-[var(--border)] bg-[var(--bg-input)] px-3 py-2 text-sm focus:border-[var(--accent)] focus:outline-none"
            value={form.max_drawdown_pct}
            onChange={(e) =>
              setForm({ ...form, max_drawdown_pct: parseFloat(e.target.value) || 0 })
            }
          />
        </div>

        {/* Pairs Blacklist */}
        <div className="sm:col-span-2">
          <label className="mb-1 block text-sm text-[var(--text-secondary)]">
            Pairs Blacklist (comma-separated)
          </label>
          <input
            className="w-full rounded-lg border border-[var(--border)] bg-[var(--bg-input)] px-3 py-2 text-sm focus:border-[var(--accent)] focus:outline-none"
            placeholder="e.g. DOGE/USDT, SHIB/USDT"
            value={form.pairs_blacklist}
            onChange={(e) =>
              setForm({ ...form, pairs_blacklist: e.target.value })
            }
          />
        </div>

        {/* Automation Mode Toggle */}
        <div className="sm:col-span-2">
          <label className="mb-2 block text-sm text-[var(--text-secondary)]">
            Trading Mode
          </label>
          <div className="flex gap-2">
            <button
              className={`rounded-lg px-4 py-2 text-sm font-medium transition ${
                form.automation_mode === "paper"
                  ? "bg-[var(--accent)] text-black"
                  : "border border-[var(--border)] bg-[var(--bg-input)] text-[var(--text-secondary)]"
              }`}
              onClick={() => setForm({ ...form, automation_mode: "paper" })}
            >
              Paper Trading
            </button>
            <button
              className={`rounded-lg px-4 py-2 text-sm font-medium transition ${
                form.automation_mode === "live"
                  ? "bg-[var(--danger)] text-white"
                  : "border border-[var(--border)] bg-[var(--bg-input)] text-[var(--text-secondary)]"
              }`}
              onClick={() => setForm({ ...form, automation_mode: "live" })}
            >
              Live Trading
            </button>
          </div>
          {form.automation_mode === "live" && (
            <p className="mt-2 text-xs text-[var(--warning)]">
              Live trading uses real funds. Make sure your KuCoin API keys are
              configured.
            </p>
          )}
        </div>
      </div>

      {/* Save Button */}
      <div className="mt-6 flex items-center gap-3">
        <button
          onClick={handleSave}
          disabled={saving}
          className="rounded-lg bg-[var(--accent)] px-6 py-2 text-sm font-medium text-black transition hover:bg-[var(--accent-hover)] disabled:opacity-50"
        >
          {saving ? "Saving..." : "Save Profile"}
        </button>
        {saved && (
          <span className="text-sm text-[var(--accent)]">Saved!</span>
        )}
      </div>
    </section>
  );
}
