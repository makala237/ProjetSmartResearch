export type AppLang = "fr" | "en";

export interface FormattedAppError {
  rawMessage: string;
  displayMessage: string;
  severity: "error" | "warning";
  isTechnical: boolean;
}

/**
 * Strip raw HTML error pages (e.g. Google/Cloud Run 502/504 HTML) into a concise
 * human-readable technical summary for admins.
 */
export function sanitizeTechnicalMessage(raw: string): string {
  const trimmed = (raw || "").trim();
  if (!trimmed) return "";

  if (/<html[\s>]|<!doctype html>/i.test(trimmed)) {
    const titleMatch = trimmed.match(/<title[^>]*>(.*?)<\/title>/i);
    const cleaned = trimmed
      .replace(/<style[\s\S]*?<\/style>/gi, " ")
      .replace(/<script[\s\S]*?<\/script>/gi, " ")
      .replace(/<[^>]+>/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    const title = titleMatch ? titleMatch[1].trim() : "";
    const snippet = cleaned.slice(0, 260);
    return title && !snippet.toLowerCase().startsWith(title.toLowerCase())
      ? `${title} — ${snippet}`
      : snippet || "HTTP 502/504 Server Error";
  }

  return trimmed;
}

const TECHNICAL_PATTERNS: RegExp[] = [
  /<html[\s>]|<!doctype html>/i,
  /\b(500|502|503|504)\b/,
  /HTTP\s*5\d\d/i,
  /Failed to fetch|NetworkError|Load failed|fetch failed|ECONN|ERR_NETWORK/i,
  /Unterminated string|Expecting value|JSONDecodeError|Unexpected token|JSON\.parse/i,
  /Traceback|RuntimeError|TypeError|ValueError|AttributeError|KeyError|IndexError|list index out of range/i,
  /Tous les modèles configurés|model.*failed|APIError|RESOURCE_EXHAUSTED|DEADLINE_EXCEEDED/i,
  /maximum request timeout|timed out|timeout/i,
];

const POLITE_WARNING_PATTERNS: RegExp[] = [
  /Limite de fréquence|Limite de débit|AI_RATE_LIMIT|requêtes IA par minute|tokens IA par minute|Rate limit reached|Token throughput limit/i,
  /Plafond journalier|quota IA|Crédits IA insuffisants|tokens IA|Daily request cap|Daily token cap|Insufficient AI credits/i,
  /Votre accès en écriture|Aucune licence active|MODULE_EXPIRED|NO_ACTIVE_LICENSE|write access.*expired|No active license/i,
  /session a expiré|reconnecter|mot de passe|identifiants|invalide|Session expired|Session revoked|Incorrect username|password/i,
  /ne dispose pas encore de contenu source|sélectionnez|requis/i,
];

/**
 * Format an error message according to user role:
 * - Admin: receives the real technical error message (HTML cleaned).
 * - Student: receives a polite, apologetic message without raw technical jargon.
 */
export function formatErrorForRole(
  rawError: string,
  isAdmin: boolean,
  lang: AppLang
): FormattedAppError {
  const cleanTech = sanitizeTechnicalMessage(rawError);
  const isTechnical = TECHNICAL_PATTERNS.some((rx) => rx.test(rawError) || rx.test(cleanTech));
  const isWarning = !isTechnical && POLITE_WARNING_PATTERNS.some((rx) => rx.test(cleanTech));

  if (isAdmin) {
    return {
      rawMessage: cleanTech,
      displayMessage: cleanTech,
      severity: isWarning ? "warning" : "error",
      isTechnical,
    };
  }

  if (isWarning) {
    const alreadyPolite = /^(nous sommes|désolé|veuillez|we are|sorry|please)/i.test(cleanTech);
    const prefix = alreadyPolite
      ? ""
      : lang === "en"
      ? "We apologize for the inconvenience: "
      : "Nous sommes désolés : ";
    return {
      rawMessage: cleanTech,
      displayMessage: `${prefix}${cleanTech}`,
      severity: "warning",
      isTechnical: false,
    };
  }

  if (isTechnical || cleanTech.length > 260) {
    return {
      rawMessage: cleanTech,
      displayMessage:
        lang === "en"
          ? "We are truly sorry — a temporary technical issue occurred while processing your request. Please try again in a few moments; our technical team has been automatically notified."
          : "Nous sommes sincèrement désolés, une erreur technique momentanée est survenue lors du traitement de votre demande. Veuillez réessayer dans quelques instants ; notre équipe technique a été automatiquement informée.",
      severity: "error",
      isTechnical: true,
    };
  }

  const alreadyPolite = /^(nous sommes|désolé|veuillez|we are|sorry|please)/i.test(cleanTech);
  return {
    rawMessage: cleanTech,
    displayMessage: alreadyPolite
      ? cleanTech
      : lang === "en"
      ? `We are sorry, an issue occurred: ${cleanTech}`
      : `Nous sommes désolés, un incident est survenu : ${cleanTech}`,
    severity: "error",
    isTechnical: false,
  };
}
