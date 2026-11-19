import { useState } from "react";
import { AlertCircle, Eye, EyeOff, KeyRound, Loader2, ShieldCheck } from "lucide-react";
import { api, setAuthTokens } from "../api/client";
import type { TokenResponse } from "../types/auth";

interface LoginScreenProps {
  onAuthenticated: (tokenRes: TokenResponse) => void;
  notice?: string | null;
}

export function LoginScreen({ onAuthenticated, notice }: LoginScreenProps) {
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
      setError(err instanceof Error ? err.message : "Échec de connexion.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen flex-col justify-center bg-gradient-to-br from-slate-100 via-white to-rose-50/40 px-4 py-10">
      <div className="mx-auto w-full max-w-md rounded-2xl border border-slate-200 bg-white p-7 shadow-lg sm:p-8">
        <div className="flex items-center gap-3">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-[#6C2826] text-base font-bold text-white shadow-sm">
            SR
          </div>
          <div>
            <h1 className="text-xl font-bold text-slate-900">
              SmartResearch <span className="text-[#6C2826]">FMSB</span>
            </h1>
            <p className="text-xs text-slate-500">
              Plateforme CAMES de protocole médical, CRF &amp; soutenance
            </p>
          </div>
        </div>

        <div className="mt-6 border-t border-slate-100 pt-5">
          <h2 className="text-base font-semibold text-slate-900">
            Connexion à votre espace personnel
          </h2>
          <p className="mt-1 text-xs text-slate-500">
            Saisissez les identifiants délivrés par l'administration. Une seule session active est autorisée par compte.
          </p>
        </div>

        {notice && (
          <div className="mt-4 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-xs text-amber-900">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
            <span>{notice}</span>
          </div>
        )}

        {error && (
          <div className="mt-4 flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-xs text-rose-800">
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
              Adresse email
            </label>
            <input
              id="login-email"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="etudiant@exemple.cm"
              className="mt-1 w-full rounded-lg border border-slate-300 px-3.5 py-2.5 text-sm text-slate-900 focus:border-[#6C2826] focus:outline-none focus:ring-1 focus:ring-[#6C2826]"
            />
          </div>

          <div>
            <label
              htmlFor="login-password"
              className="block text-xs font-semibold text-slate-700"
            >
              Mot de passe
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
                className="w-full rounded-lg border border-slate-300 py-2.5 pl-3.5 pr-10 text-sm text-slate-900 focus:border-[#6C2826] focus:outline-none focus:ring-1 focus:ring-[#6C2826]"
              />
              <button
                type="button"
                onClick={() => setShowPassword((prev) => !prev)}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                title={showPassword ? "Masquer" : "Afficher"}
              >
                {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-[#6C2826] px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-[#571f1d] disabled:opacity-60"
          >
            {loading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Connexion en cours...
              </>
            ) : (
              <>
                <KeyRound className="h-4 w-4" />
                Se connecter
              </>
            )}
          </button>
        </form>

        <div className="mt-6 flex items-center gap-2 rounded-lg bg-slate-50 px-3.5 py-2.5 text-[11px] text-slate-500">
          <ShieldCheck className="h-4 w-4 shrink-0 text-emerald-600" />
          <span>
            Les comptes et licences d'accès par module sont délivrés et activés par l'administration SmartResearch FMSB.
          </span>
        </div>
      </div>
    </div>
  );
}
