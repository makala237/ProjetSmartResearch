import { useState } from "react";
import { AlertCircle, Eye, EyeOff, KeyRound, Loader2, ShieldCheck } from "lucide-react";
import { api, setAuthTokens } from "../api/client";
import { useLanguage } from "../i18n/LanguageContext";
import type { TokenResponse } from "../types/auth";

interface LoginScreenProps {
  onAuthenticated: (tokenRes: TokenResponse) => void;
  notice?: string | null;
}

export function LoginScreen({ onAuthenticated, notice }: LoginScreenProps) {
  const { lang, setLang, t } = useLanguage();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim() || !password) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.login(email.trim(), password);
      setAuthTokens(res.access_token, res.refresh_token);
      onAuthenticated(res);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : lang === "en"
          ? "Sign-in failed."
          : "Échec de connexion."
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative flex min-h-screen flex-col justify-center overflow-hidden bg-gradient-to-br from-[#004A8F]/10 via-[#F4F7FB] to-[#1FA84F]/10 px-4 py-10">
      {/* Subtle decorative radial glows matching logo palette */}
      <div className="pointer-events-none absolute -left-28 -top-28 h-80 w-80 rounded-full bg-[#0284C7]/15 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-28 -right-28 h-80 w-80 rounded-full bg-[#1FA84F]/15 blur-3xl" />

      <div className="relative mx-auto w-full max-w-md overflow-hidden rounded-3xl border border-slate-200/90 bg-white/95 shadow-xl shadow-slate-900/5 backdrop-blur">
        <div className="h-1.5 w-full bg-gradient-to-r from-[#004A8F] via-[#0284C7] to-[#1FA84F]" />

        <div className="p-7 sm:p-8">
          <div className="mb-2 flex justify-end">
            <select
              aria-label={t("nav.language_label")}
              value={lang}
              onChange={(e) => setLang(e.target.value === "en" ? "en" : "fr")}
              className="rounded-lg border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] font-semibold text-slate-700"
            >
              <option value="fr">FR — Français</option>
              <option value="en">EN — English</option>
            </select>
          </div>

          <div className="flex flex-col items-center text-center">
            <img
              src="/logo-smart-research.png"
              alt={`Smart Research — ${t("app.tagline")}`}
              className="h-28 w-auto object-contain drop-shadow-xs"
            />
            <span className="mt-2 inline-flex items-center gap-1.5 rounded-full border border-[#004A8F]/15 bg-[#004A8F]/5 px-3 py-0.5 text-[11px] font-semibold text-[#004A8F]">
              {t("login.subtitle")}
            </span>
          </div>

          <div className="mt-5 border-t border-slate-100 pt-5">
            <h2 className="text-base font-bold text-slate-900">
              {t("login.title")}
            </h2>
            <p className="mt-1 text-xs leading-relaxed text-slate-500">
              {lang === "en"
                ? "Enter the credentials issued by your administrator. Only one active session is allowed per account."
                : "Saisissez les identifiants délivrés par l'administration. Une seule session active est autorisée par compte."}
            </p>
          </div>

          {notice && (
            <div className="mt-4 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-xs text-amber-900">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <span>{notice}</span>
            </div>
          )}

          {error && (
            <div className="mt-4 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-xs text-rose-800">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-rose-600" />
              <span>{error}</span>
            </div>
          )}

          <form onSubmit={handleSubmit} className="mt-5 space-y-4">
            <div>
              <label
                htmlFor="login-email"
                className="block text-xs font-semibold text-slate-700"
              >
                {t("login.email")}
              </label>
              <input
                id="login-email"
                type="email"
                required
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder={lang === "en" ? "student@example.org" : "etudiant@exemple.cm"}
                className="mt-1 w-full rounded-xl border border-slate-300 bg-slate-50/50 px-3.5 py-2.5 text-sm text-slate-900 transition focus:border-[#004A8F] focus:bg-white focus:outline-none focus:ring-2 focus:ring-[#004A8F]/20"
              />
            </div>

            <div>
              <label
                htmlFor="login-password"
                className="block text-xs font-semibold text-slate-700"
              >
                {t("login.password")}
              </label>
              <div className="relative mt-1">
                <input
                  id="login-password"
                  type={showPassword ? "text" : "password"}
                  required
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  className="w-full rounded-xl border border-slate-300 bg-slate-50/50 py-2.5 pl-3.5 pr-10 text-sm text-slate-900 transition focus:border-[#004A8F] focus:bg-white focus:outline-none focus:ring-2 focus:ring-[#004A8F]/20"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((prev) => !prev)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-[#004A8F]"
                  title={showPassword ? (lang === "en" ? "Hide" : "Masquer") : (lang === "en" ? "Show" : "Afficher")}
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>

            <button
              type="submit"
              disabled={loading}
              className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-[#004A8F] to-[#0284C7] px-4 py-2.5 text-sm font-semibold text-white shadow-md shadow-[#004A8F]/20 transition hover:brightness-105 disabled:opacity-60"
            >
              {loading ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {lang === "en" ? "Signing in..." : "Connexion en cours..."}
                </>
              ) : (
                <>
                  <KeyRound className="h-4 w-4" />
                  {t("login.submit")}
                </>
              )}
            </button>
          </form>

          <div className="mt-6 flex items-center gap-2 rounded-xl border border-emerald-100 bg-emerald-50/60 px-3.5 py-2.5 text-[11px] text-slate-600">
            <ShieldCheck className="h-4 w-4 shrink-0 text-[#1FA84F]" />
            <span>
              {lang === "en"
                ? "User accounts and module licenses are provisioned and activated by Smart Research administration."
                : "Les comptes et licences d'accès par module sont délivrés et activés par l'administration Smart Research."}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
