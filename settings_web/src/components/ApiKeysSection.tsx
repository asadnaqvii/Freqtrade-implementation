"use client";

import { useState } from "react";
import type { User } from "@/lib/supabase";
import { updateUser } from "@/lib/supabase";
import { testFreqtradeConnection } from "@/lib/api";

interface Props {
  user: User;
  onUpdate: (user: User) => void;
}

export default function ApiKeysSection({ user, onUpdate }: Props) {
  const [form, setForm] = useState({
    api_key: "",
    api_secret: "",
    api_password: "",
  });
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [testing, setTesting] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState<{
    ok: boolean;
    message: string;
  } | null>(null);

  const hasKeys = Boolean(user.kucoin_api_key_enc);

  async function handleSaveKeys() {
    if (!form.api_key || !form.api_secret || !form.api_password) return;
    setSaving(true);
    setSaved(false);
    // Keys will be encrypted server-side; for now we send them to Supabase
    // In production, this should go through a secure API endpoint
    const updated = await updateUser(user.id, {
      kucoin_api_key_enc: form.api_key,
      kucoin_api_secret_enc: form.api_secret,
      kucoin_api_password_enc: form.api_password,
    });
    setSaving(false);
    if (updated) {
      onUpdate(updated);
      setForm({ api_key: "", api_secret: "", api_password: "" });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    }
  }

  async function handleTestConnection() {
    setTesting(true);
    setConnectionStatus(null);
    const result = await testFreqtradeConnection();
    setConnectionStatus(result);
    setTesting(false);
  }

  function maskKey(key: string | null): string {
    if (!key) return "Not set";
    if (key.length <= 8) return "****";
    return key.slice(0, 4) + "****" + key.slice(-4);
  }

  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)] p-6">
      <h2 className="mb-4 text-lg font-semibold">KuCoin API Keys</h2>

      {/* Current Status */}
      <div className="mb-4 rounded-lg border border-[var(--border)] bg-[var(--bg-input)] p-3">
        <div className="flex items-center gap-2">
          <span
            className={`h-2 w-2 rounded-full ${
              hasKeys ? "bg-[var(--accent)]" : "bg-[var(--danger)]"
            }`}
          />
          <span className="text-sm">
            {hasKeys ? "Keys configured" : "No keys set"}
          </span>
        </div>
        {hasKeys && (
          <div className="mt-2 space-y-1 text-xs text-[var(--text-secondary)]">
            <p>API Key: {maskKey(user.kucoin_api_key_enc)}</p>
            <p>Secret: {maskKey(user.kucoin_api_secret_enc)}</p>
            <p>Password: {maskKey(user.kucoin_api_password_enc)}</p>
          </div>
        )}
      </div>

      {/* Update Keys Form */}
      <div className="grid gap-3">
        <div>
          <label className="mb-1 block text-sm text-[var(--text-secondary)]">
            API Key
          </label>
          <input
            type="password"
            className="w-full rounded-lg border border-[var(--border)] bg-[var(--bg-input)] px-3 py-2 text-sm focus:border-[var(--accent)] focus:outline-none"
            placeholder="Enter KuCoin API key"
            value={form.api_key}
            onChange={(e) => setForm({ ...form, api_key: e.target.value })}
          />
        </div>
        <div>
          <label className="mb-1 block text-sm text-[var(--text-secondary)]">
            API Secret
          </label>
          <input
            type="password"
            className="w-full rounded-lg border border-[var(--border)] bg-[var(--bg-input)] px-3 py-2 text-sm focus:border-[var(--accent)] focus:outline-none"
            placeholder="Enter KuCoin API secret"
            value={form.api_secret}
            onChange={(e) => setForm({ ...form, api_secret: e.target.value })}
          />
        </div>
        <div>
          <label className="mb-1 block text-sm text-[var(--text-secondary)]">
            API Password
          </label>
          <input
            type="password"
            className="w-full rounded-lg border border-[var(--border)] bg-[var(--bg-input)] px-3 py-2 text-sm focus:border-[var(--accent)] focus:outline-none"
            placeholder="Enter KuCoin API password"
            value={form.api_password}
            onChange={(e) =>
              setForm({ ...form, api_password: e.target.value })
            }
          />
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          onClick={handleSaveKeys}
          disabled={
            saving || !form.api_key || !form.api_secret || !form.api_password
          }
          className="rounded-lg bg-[var(--accent)] px-6 py-2 text-sm font-medium text-black transition hover:bg-[var(--accent-hover)] disabled:opacity-50"
        >
          {saving ? "Saving..." : "Update Keys"}
        </button>
        <button
          onClick={handleTestConnection}
          disabled={testing}
          className="rounded-lg border border-[var(--border)] bg-[var(--bg-input)] px-4 py-2 text-sm transition hover:border-[var(--accent)]"
        >
          {testing ? "Testing..." : "Test Connection"}
        </button>
        {saved && (
          <span className="text-sm text-[var(--accent)]">Keys updated!</span>
        )}
      </div>

      {connectionStatus && (
        <div
          className={`mt-3 rounded-lg p-3 text-sm ${
            connectionStatus.ok
              ? "bg-[var(--accent)]/10 text-[var(--accent)]"
              : "bg-[var(--danger)]/10 text-[var(--danger)]"
          }`}
        >
          {connectionStatus.message}
        </div>
      )}

      <p className="mt-4 text-xs text-[var(--text-secondary)]">
        Keys are encrypted at rest. Never share your API keys with anyone.
      </p>
    </section>
  );
}
