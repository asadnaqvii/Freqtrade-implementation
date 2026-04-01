"use client";

import { useState } from "react";
import type { User } from "@/lib/supabase";
import { updateUser } from "@/lib/supabase";

interface Props {
  user: User;
  onUpdate: (user: User) => void;
}

export default function NotificationsSection({ user, onUpdate }: Props) {
  const [config, setConfig] = useState(user.notifications_config);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  function toggle(key: keyof typeof config) {
    if (key === "summary_time") return;
    setConfig({ ...config, [key]: !config[key] });
  }

  async function handleSave() {
    setSaving(true);
    setSaved(false);
    const updated = await updateUser(user.id, {
      notifications_config: config,
    });
    setSaving(false);
    if (updated) {
      onUpdate(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    }
  }

  const toggles: { key: keyof typeof config; label: string; desc: string }[] = [
    {
      key: "trade_notifications",
      label: "Trade Notifications",
      desc: "Get notified when trades open or close",
    },
    {
      key: "daily",
      label: "Daily Summary",
      desc: "Daily P&L report at your chosen time",
    },
    {
      key: "weekly",
      label: "Weekly Summary",
      desc: "Weekly performance report every Monday",
    },
    {
      key: "monthly",
      label: "Monthly Summary",
      desc: "Monthly performance review on the 1st",
    },
    {
      key: "macro_alerts",
      label: "Macro Alerts",
      desc: "Market intelligence alerts when significant events occur",
    },
  ];

  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)] p-6">
      <h2 className="mb-4 text-lg font-semibold">Notifications</h2>

      <div className="space-y-3">
        {toggles.map(({ key, label, desc }) => (
          <div
            key={key}
            className="flex items-center justify-between rounded-lg border border-[var(--border)] bg-[var(--bg-input)] p-3"
          >
            <div>
              <p className="text-sm font-medium">{label}</p>
              <p className="text-xs text-[var(--text-secondary)]">{desc}</p>
            </div>
            <button
              onClick={() => toggle(key)}
              className={`relative h-6 w-11 rounded-full transition ${
                config[key] ? "bg-[var(--accent)]" : "bg-[var(--border)]"
              }`}
            >
              <span
                className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition ${
                  config[key] ? "left-[22px]" : "left-0.5"
                }`}
              />
            </button>
          </div>
        ))}
      </div>

      {/* Summary Time */}
      <div className="mt-4">
        <label className="mb-1 block text-sm text-[var(--text-secondary)]">
          Daily Summary Time (UTC)
        </label>
        <input
          type="time"
          className="rounded-lg border border-[var(--border)] bg-[var(--bg-input)] px-3 py-2 text-sm focus:border-[var(--accent)] focus:outline-none"
          value={config.summary_time}
          onChange={(e) =>
            setConfig({ ...config, summary_time: e.target.value })
          }
        />
      </div>

      {/* Save */}
      <div className="mt-4 flex items-center gap-3">
        <button
          onClick={handleSave}
          disabled={saving}
          className="rounded-lg bg-[var(--accent)] px-6 py-2 text-sm font-medium text-black transition hover:bg-[var(--accent-hover)] disabled:opacity-50"
        >
          {saving ? "Saving..." : "Save Notifications"}
        </button>
        {saved && (
          <span className="text-sm text-[var(--accent)]">Saved!</span>
        )}
      </div>
    </section>
  );
}
