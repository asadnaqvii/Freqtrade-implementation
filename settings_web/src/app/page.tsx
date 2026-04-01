"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import type { User, Strategy } from "@/lib/supabase";

const ProfileSection = dynamic(() => import("@/components/ProfileSection"), { ssr: false });
const StrategySection = dynamic(() => import("@/components/StrategySection"), { ssr: false });
const ApiKeysSection = dynamic(() => import("@/components/ApiKeysSection"), { ssr: false });
const NotificationsSection = dynamic(() => import("@/components/NotificationsSection"), { ssr: false });

function SettingsPage() {
  const [phone, setPhone] = useState("");
  const [user, setUser] = useState<User | null>(null);
  const [strategy, setStrategy] = useState<Strategy | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Check URL params for pre-filled phone
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const p = params.get("phone") || params.get("id");
    if (p) {
      setPhone(p);
      loadUser(p);
    }
  }, []);

  async function loadUser(phoneOrId: string) {
    setLoading(true);
    setError("");
    const { getUser, getActiveStrategy } = await import("@/lib/supabase");
    const u = await getUser(phoneOrId);
    if (!u) {
      setError("User not found. Make sure you've completed WhatsApp onboarding first.");
      setLoading(false);
      return;
    }
    setUser(u);
    const s = await getActiveStrategy(u.id);
    setStrategy(s);
    setLoading(false);
  }

  function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    if (phone.trim()) loadUser(phone.trim());
  }

  // Not logged in — show phone lookup
  if (!user) {
    return (
      <main className="mx-auto flex min-h-screen max-w-md flex-col items-center justify-center px-4">
        <div className="w-full rounded-xl border border-[var(--border)] bg-[var(--bg-card)] p-8">
          <h1 className="mb-1 text-2xl font-bold">Neuronaq</h1>
          <p className="mb-6 text-sm text-[var(--text-secondary)]">
            Enter your WhatsApp number to manage your trading bot settings.
          </p>

          <form onSubmit={handleLogin}>
            <label className="mb-1 block text-sm text-[var(--text-secondary)]">
              WhatsApp Number
            </label>
            <input
              className="mb-4 w-full rounded-lg border border-[var(--border)] bg-[var(--bg-input)] px-3 py-2 text-sm focus:border-[var(--accent)] focus:outline-none"
              placeholder="+1234567890"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
            />
            {error && (
              <p className="mb-3 text-sm text-[var(--danger)]">{error}</p>
            )}
            <button
              type="submit"
              disabled={loading || !phone.trim()}
              className="w-full rounded-lg bg-[var(--accent)] py-2 text-sm font-medium text-black transition hover:bg-[var(--accent-hover)] disabled:opacity-50"
            >
              {loading ? "Loading..." : "Open Settings"}
            </button>
          </form>
        </div>
      </main>
    );
  }

  // Logged in — show settings
  return (
    <main className="mx-auto min-h-screen max-w-2xl px-4 py-8">
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Neuronaq Settings</h1>
          <p className="text-sm text-[var(--text-secondary)]">
            {user.name || user.whatsapp_number} &middot;{" "}
            <span className="capitalize">{user.role}</span>
          </p>
        </div>
        <button
          onClick={() => {
            setUser(null);
            setStrategy(null);
            setPhone("");
          }}
          className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm transition hover:border-[var(--danger)] hover:text-[var(--danger)]"
        >
          Sign Out
        </button>
      </div>

      {/* Sections */}
      <div className="space-y-6">
        <ProfileSection user={user} onUpdate={setUser} />
        <StrategySection strategy={strategy} onUpdate={setStrategy} />
        <ApiKeysSection user={user} onUpdate={setUser} />
        <NotificationsSection user={user} onUpdate={setUser} />
      </div>

      <p className="mt-8 text-center text-xs text-[var(--text-secondary)]">
        Neuronaq Trading Bot &middot; Changes sync with your WhatsApp bot
        automatically
      </p>
    </main>
  );
}

export default dynamic(() => Promise.resolve(SettingsPage), { ssr: false });
