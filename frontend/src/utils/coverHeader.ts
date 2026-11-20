import type { CoverMetadata } from "../types/protocol";

export interface FormattedCoverHeader {
  universityFr: string;
  universityEn: string;
  universityContactsFr: string[];
  universityContactsEn: string[];
  facultyFr: string;
  facultyEn: string;
  facultyContactsFr: string[];
  facultyContactsEn: string[];
  departmentFr: string;
  departmentEn: string;
  hasOptionalTiers: boolean;
}

export function formatBilingualContactTier(
  poBox?: string,
  email?: string
): { fr: string[]; en: string[] } {
  const cleanBp = (poBox || "")
    .trim()
    .replace(/^(?:b\.?\s*p\.?|p\.?\s*o\.?\s*box)\s*:?\s*/i, "")
    .trim();
  const cleanMail = (email || "")
    .trim()
    .replace(/^(?:courriel|e-?mail|mail)\s*:?\s*/i, "")
    .trim();

  const fr: string[] = [];
  const en: string[] = [];
  if (cleanBp) {
    fr.push(`B.P. : ${cleanBp}`);
    en.push(`P.O. Box: ${cleanBp.replace(/\bcameroun\b/gi, "Cameroon")}`);
  }
  if (cleanMail) {
    fr.push(`Courriel : ${cleanMail}`);
    en.push(`E-mail: ${cleanMail}`);
  }
  return { fr, en };
}

export function getFormattedCoverHeader(
  cover?: Partial<CoverMetadata> | null
): FormattedCoverHeader {
  const universityFr = (
    cover?.university_fr || "UNIVERSITÉ DE YAOUNDÉ I"
  )
    .trim()
    .toUpperCase();
  const universityEn = (
    cover?.university_en || "THE UNIVERSITY OF YAOUNDÉ I"
  )
    .trim()
    .toUpperCase();
  const facultyFr = (
    cover?.faculty_fr || "FACULTÉ DE MÉDECINE ET DES SCIENCES BIOMÉDICALES"
  )
    .trim()
    .toUpperCase();
  const facultyEn = (
    cover?.faculty_en || "FACULTY OF MEDICINE AND BIOMEDICAL SCIENCES"
  )
    .trim()
    .toUpperCase();
  const departmentFr = (cover?.department_fr || "SANTE PUBLIQUE")
    .trim()
    .toUpperCase();
  const departmentEn = (cover?.department_en || "PUBLIC HEALTH")
    .trim()
    .toUpperCase();

  const uniContacts = formatBilingualContactTier(
    cover?.university_po_box,
    cover?.university_email
  );
  const facContacts = formatBilingualContactTier(
    cover?.faculty_po_box,
    cover?.faculty_email
  );

  return {
    universityFr,
    universityEn,
    universityContactsFr: uniContacts.fr,
    universityContactsEn: uniContacts.en,
    facultyFr,
    facultyEn,
    facultyContactsFr: facContacts.fr,
    facultyContactsEn: facContacts.en,
    departmentFr,
    departmentEn,
    hasOptionalTiers: uniContacts.fr.length > 0 || facContacts.fr.length > 0,
  };
}
