import { useState } from "react";
import { AlertCircle, KeyRound, Loader2, LogOut, ShieldAlert, X } from "lucide-react";
import { api, setAuthTokens } from "../api/client";
import { useLanguage } from "../i18n/LanguageContext";
import type { TokenResponse } from "../types/auth";

interface ChangePasswordModalProps {
  userEmail: string;
  mandatory?: boolean;
  onSuccess: (tokenRes: TokenResponse) => void;
  onCancel?: () => void;
  onLogout?: () => void;
}

export function ChangePasswordModal({
  userEmail,
  mandatory = false,
  onSuccess,
  onCancel,
  onLogout,
}: ChangePasswordModalProps) {
  const { lang } = useLanguage();
  const isEn = lang === "en";
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (newPassword.trim().length < 8) {
      setError(
        isEn
          ? "The new password must be at least 8 characters long."
          : "Le nouveau mot de passe doit contenir au moins 8 caractères."
      );
      return;
    }
    if (newPassword !== confirmPassword) {
      setError(
        isEn
          ? "Password confirmation does not match the new password."
          : "La confirmation ne correspond pas au nouveau mot de passe."
      );
      return;
    }

    setLoading(true);
    try {
      const res = await api.changePassword(currentPassword, newPassword);
      setAuthTokens(res.access_token, res.refresh_token);
      onSuccess(res);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : isEn
          ? "Unable to update password."
          : "Impossible de modifier le mot de passe."
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 px-4 py-6 backdrop-blur-xs">
      <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 shadow-xl">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-amber-100 text-amber-800">
              {mandatory ? <ShieldAlert className="h-5 w-5" /> : <KeyRound className="h-5 w-5" />}
            </div>
            <div>
              <h3 className="text-base font-bold text-slate-900">
                {mandatory
                  ? isEn
                    ? "Mandatory password change"
                    : "Changement de mot de passe obligatoire"
                  : isEn
                  ? "Change your password"
                  : "Modifier votre mot de passe"}
              </h3>
              <p className="text-xs text-slate-500">{userEmail}</p>
            </div>
          </div>
          {!mandatory && onCancel && (
            <button
              type="button"
              onClick={onCancel}
              className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
              title={isEn ? "Close" : "Fermer"}
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        {mandatory && (
          <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900">
            {isEn
              ? "On your first sign-in (or after a password reset), please set a personal password of at least 8 characters before accessing the modules."
              : "Lors de votre première connexion (ou après réinitialisation), veuillez définir un mot de passe personnel d'au moins 8 caractères avant d'accéder aux modules."}
          </p>
        )}

        {error && (
          <div className="mt-3 flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="mt-4 space-y-3.5">
          <div>
            <label className="block text-xs font-semibold text-slate-700">
              {isEn ? "Current / temporary password" : "Mot de passe actuel / temporaire"}
            </label>
            <input
              type="password"
              required
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-[#6C2826] focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-xs font-semibold text-slate-700">
              {isEn ? "New password (min. 8 characters)" : "Nouveau mot de passe (min. 8 caractères)"}
            </label>
            <input
              type="password"
              required
              minLength={8}
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-[#6C2826] focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-xs font-semibold text-slate-700">
              {isEn ? "Confirm new password" : "Confirmer le nouveau mot de passe"}
            </label>
            <input
              type="password"
              required
              minLength={8}
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-[#6C2826] focus:outline-none"
            />
          </div>

          <div className="flex flex-wrap items-center justify-end gap-2 pt-2">
            {mandatory && onLogout && (
              <button
                type="button"
                onClick={onLogout}
                className="mr-auto inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50"
              >
                <LogOut className="h-3.5 w-3.5" />
                {isEn ? "Sign out" : "Se déconnecter"}
              </button>
            )}
            {!mandatory && onCancel && (
              <button
                type="button"
                onClick={onCancel}
                className="rounded-lg border border-slate-300 px-3.5 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50"
              >
                {isEn ? "Cancel" : "Annuler"}
              </button>
            )}
            <button
              type="submit"
              disabled={loading}
              className="inline-flex items-center gap-1.5 rounded-lg bg-[#6C2826] px-4 py-2 text-xs font-semibold text-white hover:bg-[#571f1d] disabled:opacity-60"
            >
              {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {isEn ? "Save password" : "Enregistrer le mot de passe"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
