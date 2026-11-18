import React, { useEffect, useState } from "react";
import {
  Airplay,
  Award,
  CheckCircle2,
  Clock,
  Download,
  FileSpreadsheet,
  Image as ImageIcon,
  Layers,
  Mic,
  Plus,
  RefreshCw,
  Sparkles,
  Trash2,
} from "lucide-react";
import { api } from "../api/client";
import type { PresentationDeck, Slide, SlideElement } from "../types/presentation";
import type { ProtocolProject } from "../types/protocol";

interface Module4PresentationProps {
  project: ProtocolProject;
  onError?: (msg: string) => void;
}

export function Module4Presentation({ project, onError }: Module4PresentationProps) {
  const [deck, setDeck] = useState<PresentationDeck | null>(null);
  const [selectedSlideIndex, setSelectedSlideIndex] = useState<number>(1);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [aiGenerating, setAiGenerating] = useState(false);

  useEffect(() => {
    loadDeck();
  }, [project.project_id]);

  async function loadDeck() {
    setLoading(true);
    try {
      const data = await api.getPresentation(project.project_id);
      setDeck(data);
      if (data.slides.length > 0) {
        setSelectedSlideIndex(data.current_slide_index || 1);
      }
    } catch (err) {
      if (onError) onError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function saveDeck(newDeck: PresentationDeck) {
    setSaving(true);
    try {
      const saved = await api.updatePresentation(project.project_id, newDeck);
      setDeck(saved);
    } catch (err) {
      if (onError) onError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleResetFromProtocol() {
    if (!window.confirm("Voulez-vous générer le diaporama granulaire complet à partir du protocole ?")) {
      return;
    }
    setLoading(true);
    try {
      const fresh = await api.resetPresentationFromProtocol(project.project_id);
      setDeck(fresh);
      setSelectedSlideIndex(1);
    } catch (err) {
      if (onError) onError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function handleAiGenerateSlide(slideIdx: number) {
    setAiGenerating(true);
    try {
      const res = await api.generateSlideAi(project.project_id, slideIdx);
      if (res.status === "ok" && deck) {
        const updatedSlides = deck.slides.map((s) =>
          s.slide_index === slideIdx ? res.slide : s
        );
        const newDeck = { ...deck, slides: updatedSlides };
        setDeck(newDeck);
      }
    } catch (err) {
      if (onError) onError(err instanceof Error ? err.message : String(err));
    } finally {
      setAiGenerating(false);
    }
  }

  const activeSlide: Slide | undefined = deck?.slides.find(
    (s) => s.slide_index === selectedSlideIndex
  );

  function updateActiveSlide(updates: Partial<Slide>) {
    if (!deck || !activeSlide) return;
    const updatedSlides = deck.slides.map((s) =>
      s.slide_index === activeSlide.slide_index ? { ...s, ...updates } : s
    );
    const newDeck = { ...deck, slides: updatedSlides };
    setDeck(newDeck);
    saveDeck(newDeck);
  }

  function handleUpdateElement(elId: string, content: string) {
    if (!activeSlide) return;
    const newElements = activeSlide.elements.map((el) =>
      el.id === elId ? { ...el, content } : el
    );
    updateActiveSlide({ elements: newElements });
  }

  function handleAddBullet() {
    if (!activeSlide) return;
    const newEl: SlideElement = {
      id: `el-${activeSlide.slide_index}-${Date.now()}`,
      element_type: "bullet_main",
      content: "Nouvel argument clinique ou méthodologique...",
      order: activeSlide.elements.length + 1,
    };
    updateActiveSlide({ elements: [...activeSlide.elements, newEl] });
  }

  function handleRemoveElement(elId: string) {
    if (!activeSlide) return;
    const newElements = activeSlide.elements.filter((el) => el.id !== elId);
    updateActiveSlide({ elements: newElements });
  }

  if (loading) {
    return (
      <div className="flex h-96 items-center justify-center">
        <div className="flex items-center gap-3 text-slate-500">
          <RefreshCw className="h-5 w-5 animate-spin text-[#6C2826]" />
          <span>Chargement du diaporama de soutenance...</span>
        </div>
      </div>
    );
  }

  if (!deck || deck.slides.length === 0) {
    return (
      <div className="rounded-2xl border border-slate-200 bg-white p-8 text-center shadow-sm">
        <Airplay className="mx-auto h-12 w-12 text-[#6C2826]" />
        <h3 className="mt-3 text-lg font-bold text-slate-900">
          Générateur de Diaporama PowerPoint (CAMES / FMSB)
        </h3>
        <p className="mx-auto mt-2 max-w-md text-sm text-slate-600">
          Générez un diaporama granulaire complet où chaque grand titre et sous-section apparaît
          sur sa propre diapositive dédiée avec vos templates graphiques.
        </p>
        <button
          onClick={handleResetFromProtocol}
          className="mt-6 inline-flex items-center gap-2 rounded-xl bg-[#6C2826] px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-[#58201e]"
        >
          <Sparkles className="h-4 w-4" />
          Générer les diapositives complètes depuis le protocole
        </button>
      </div>
    );
  }

  const notesText = activeSlide?.presenter_notes || "";
  const wordCount = notesText.trim() ? notesText.trim().split(/\s+/).length : 0;
  const estimatedSeconds = Math.round((wordCount / 130) * 60);

  return (
    <div className="flex flex-col gap-6">
      {/* Top Header Controls */}
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-[#6C2826]/10 text-[#6C2826]">
            <Airplay className="h-6 w-6" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-base font-bold text-slate-900">
                Module 4 : Diaporama & Soutenance (PowerPoint FMSB)
              </h2>
              <span className="rounded-full bg-[#6C2826]/10 px-2.5 py-0.5 text-xs font-bold text-[#6C2826]">
                {deck.slides.length} Diapositives (Plan Granulaire Intégral)
              </span>
            </div>
            <p className="text-xs text-slate-500">
              Format 16:9 Paysage • Chaque grand titre dispose de sa diapositive dédiée • Typographie ≥ 24pt
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => handleAiGenerateSlide(selectedSlideIndex)}
            disabled={aiGenerating}
            className="inline-flex items-center gap-1.5 rounded-lg border border-[#6C2826]/30 bg-[#6C2826]/5 px-3 py-1.5 text-xs font-semibold text-[#6C2826] hover:bg-[#6C2826]/10 disabled:opacity-50"
            title="Approfondir et étiqueter cette diapositive avec Gemini 3.5 & 3.1"
          >
            <Sparkles className={`h-3.5 w-3.5 ${aiGenerating ? "animate-spin" : ""}`} />
            {aiGenerating ? "IA en cours..." : "Régénérer avec l'IA"}
          </button>

          <button
            onClick={handleResetFromProtocol}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-100"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Réinitialiser
          </button>

          <a
            href={`/api/projects/${project.project_id}/presentation/export/pptx`}
            download={`Soutenance_${project.project_id}.pptx`}
            className="inline-flex items-center gap-2 rounded-lg bg-[#6C2826] px-4 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-[#58201e]"
          >
            <Download className="h-4 w-4" />
            Télécharger PowerPoint (.pptx)
          </a>
        </div>
      </div>

      {/* Main Studio Grid: Left Slide List + Center 16:9 Canvas + Right Notes */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Left Sidebar: Slide Navigation (3 Cols) */}
        <div className="flex flex-col gap-2 rounded-2xl border border-slate-200 bg-white p-3 shadow-sm lg:col-span-3">
          <div className="mb-2 flex items-center justify-between px-2 text-xs font-bold text-slate-500">
            <span>PLAN DU DIAPORAMA</span>
            <span>{deck.slides.length} SLIDES</span>
          </div>

          <div className="flex max-h-[720px] flex-col gap-1.5 overflow-y-auto pr-1">
            {deck.slides.map((s) => {
              const isSelected = s.slide_index === selectedSlideIndex;
              return (
                <button
                  key={s.id}
                  onClick={() => setSelectedSlideIndex(s.slide_index)}
                  className={`group relative flex items-start gap-2.5 rounded-xl border p-2.5 text-left transition ${
                    isSelected
                      ? "border-[#6C2826] bg-[#6C2826]/5 shadow-sm ring-1 ring-[#6C2826]"
                      : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50"
                  }`}
                >
                  <div
                    className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-xs font-bold ${
                      isSelected
                        ? "bg-[#6C2826] text-white"
                        : "bg-slate-100 text-slate-600 group-hover:bg-slate-200"
                    }`}
                  >
                    {s.slide_index}
                  </div>

                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
                        {s.slide_type === "welcome"
                          ? "Accueil"
                          : s.slide_type === "cover"
                          ? "Garde"
                          : s.slide_type === "plan"
                          ? "Plan"
                          : s.slide_type === "introduction"
                          ? "Intro"
                          : s.slide_type === "objective_general"
                          ? "Obj. Gén."
                          : s.slide_type === "objective_specific"
                          ? "Obj. Spéc."
                          : s.slide_type === "methodology"
                          ? "Méthodo"
                          : s.slide_type === "chronogram"
                          ? "Chrono"
                          : s.slide_type === "budget"
                          ? "Budget"
                          : s.slide_type === "acknowledgements"
                          ? "Fin"
                          : `Slide ${s.slide_index}`}
                      </span>
                      {s.elements.some((el) => el.element_type === "media") && (
                        <ImageIcon className="h-3 w-3 text-indigo-500" />
                      )}
                    </div>
                    <p className="truncate text-xs font-semibold text-slate-800">
                      {s.title || "Sans titre"}
                    </p>
                  </div>

                  {s.is_validated && (
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
                  )}
                </button>
              );
            })}
          </div>
        </div>

        {/* Center Canvas: 16:9 Widescreen Visual Workspace (9 Cols) */}
        <div className="flex flex-col gap-6 lg:col-span-9">
          {/* 16:9 Slide Screen Display */}
          <div className="relative overflow-hidden rounded-2xl border-2 border-slate-300 bg-white p-6 shadow-md">
            {/* Aspect Ratio Container 16:9 */}
            <div className="relative aspect-[16/9] w-full overflow-hidden rounded-xl border border-slate-100 bg-[#FDFBF7] text-slate-800 shadow-inner">
              
              {/* SLIDE 1: WELCOME */}
              {activeSlide?.slide_type === "welcome" && (
                <div className="relative h-full w-full overflow-hidden">
                  <img
                    src="/tamplates/tamplate image de fond première et deuxième diapositive.png"
                    alt="Fond Bienvenue"
                    className="absolute inset-0 h-full w-full object-cover"
                  />
                  <div className="absolute inset-0 flex items-center justify-center p-8">
                    <div className="w-[85%] rounded-3xl border-2 border-[#004B87] bg-white/95 p-8 text-center shadow-2xl backdrop-blur-sm">
                      <h2 className="text-xl font-bold uppercase tracking-wider text-[#004B87] sm:text-2xl">
                        BIENVENUE À LA SOUTENANCE DU PROTOCOLE DE RECHERCHE
                      </h2>
                      <div className="my-4 h-1 w-24 mx-auto rounded-full bg-[#6C2826]" />
                      <h1 className="text-2xl font-black uppercase text-[#6C2826] sm:text-3xl">
                        {activeSlide.subtitle || `DE L'IMPÉTRANT(E) : ${project.cover.author_name || "MBIANDA TCHOUAFA BRICE KEVIN"}`}
                      </h1>
                    </div>
                  </div>
                </div>
              )}

              {/* SLIDE 2: COVER (STRICT OFFICIAL FMSB / CAMES) */}
              {activeSlide?.slide_type === "cover" && (
                <div className="relative h-full w-full overflow-hidden bg-white">
                  <img
                    src="/tamplates/tamplate image de fond première et deuxième diapositive.png"
                    alt="Fond Couverture"
                    className="absolute bottom-0 left-0 right-0 h-[76%] w-full object-cover"
                  />
                  {/* Top Bar with bilingual headers and Logo */}
                  <div className="absolute top-2 left-6 right-6 flex items-start justify-between text-[10px] font-bold leading-tight text-slate-800">
                    <div className="w-[35%] text-center">
                      RÉPUBLIQUE DU CAMEROUN<br />
                      <span className="font-normal text-slate-500">Paix - Travail - Patrie</span><br />
                      <span className="text-slate-400">----------</span><br />
                      UNIVERSITÉ DE YAOUNDÉ I<br />
                      <span className="text-slate-400">----------</span><br />
                      FACULTÉ DE MÉDECINE ET DES SCIENCES BIOMÉDICALES
                    </div>
                    <div className="flex w-[20%] justify-center">
                      <img
                        src="/tamplates/fmsb_uy1_logo.png"
                        alt="Emblème UY1 / FMSB"
                        className="h-14 w-auto object-contain"
                      />
                    </div>
                    <div className="w-[35%] text-center">
                      REPUBLIC OF CAMEROON<br />
                      <span className="font-normal text-slate-500">Peace - Work - Fatherland</span><br />
                      <span className="text-slate-400">----------</span><br />
                      THE UNIVERSITY OF YAOUNDÉ I<br />
                      <span className="text-slate-400">----------</span><br />
                      FACULTY OF MEDICINE AND BIOMEDICAL SCIENCES
                    </div>
                  </div>

                  {/* Department banner */}
                  <div className="absolute top-[25%] left-10 right-10 rounded-lg bg-[#6C2826] py-1 text-center text-xs font-black uppercase tracking-wider text-white shadow-sm">
                    {activeSlide.elements.find(el => el.element_type === "department")?.content || project.cover.department_fr || "DÉPARTEMENT DE CHIRURGIE ET SPÉCIALITÉS"}
                  </div>

                  {/* Title Box */}
                  <div className="absolute top-[32%] left-10 right-10 rounded-2xl border-2 border-[#004B87] bg-white/95 px-4 py-2 text-center shadow-md">
                    <h1 className="text-sm font-black uppercase text-[#004B87] sm:text-base leading-snug line-clamp-3">
                      {activeSlide.title}
                    </h1>
                  </div>

                  {/* Degree & Author */}
                  <div className="absolute top-[57%] left-10 right-10 text-center">
                    <p className="text-[11px] font-bold italic text-[#6C2826]">
                      Protocole de Thèse rédigé en vue de l'obtention du Diplôme de Docteur en Médecine
                    </p>
                    <p className="mt-1 text-xs font-black text-slate-900">
                      Rédigé et présenté par : {project.cover.author_name || "MBIANDA TCHOUAFA BRICE KEVIN"} &nbsp;|&nbsp; Matricule : {project.cover.matricule || "2026-FMSB-042"}
                    </p>
                  </div>

                  {/* Directors */}
                  <div className="absolute top-[72%] left-10 right-10 flex items-start justify-between text-xs font-bold text-slate-800">
                    <div className="w-[45%] text-center whitespace-pre-line bg-white/80 rounded-xl p-1.5 border border-slate-200">
                      <span className="text-[#004B87]">Directeur de thèse :</span><br />
                      {project.cover.director || "Prof. Angwafo III Fru"}
                    </div>
                    <div className="w-[45%] text-center whitespace-pre-line bg-white/80 rounded-xl p-1.5 border border-slate-200">
                      <span className="text-[#004B87]">Co-directeur(s) :</span><br />
                      {project.cover.co_directors?.join(", ") || "Dr. Mvogo Jean"}
                    </div>
                  </div>

                  {/* Academic Year */}
                  <div className="absolute bottom-2 left-0 right-0 text-center">
                    <span className="rounded-md border border-slate-200 bg-white/90 px-4 py-0.5 text-[10px] font-bold text-slate-600">
                      Année académique {project.cover.academic_year || "2025 - 2026"}
                    </span>
                  </div>
                </div>
              )}

              {/* SLIDE 3: PLAN (6 GRANDES PUCES) */}
              {activeSlide?.slide_type === "plan" && (
                <div className="relative h-full w-full overflow-hidden">
                  <img
                    src="/tamplates/tamplate diapositive Plan.png"
                    alt="Plan Infographique"
                    className="absolute inset-0 h-full w-full object-cover"
                  />
                  {/* Left flask title */}
                  <div className="absolute left-[3%] top-[37%] w-[16%] text-center">
                    <h2 className="text-xs sm:text-sm font-black text-[#004B87] leading-tight">
                      PLAN DU DIAPORAMA
                    </h2>
                    <p className="mt-0.5 text-[9px] font-semibold italic text-slate-600">
                      Sommaire
                    </p>
                  </div>
                  {/* 6 Right pills */}
                  <div className="absolute right-[5%] top-[11.5%] bottom-[12.5%] w-[58%] flex flex-col justify-between">
                    {[
                      "1. INTRODUCTION & CONTEXTE",
                      "2. OBJECTIF GÉNÉRAL",
                      "3. OBJECTIFS SPÉCIFIQUES",
                      "4. MÉTHODOLOGIE DE L'ÉTUDE",
                      "5. CHRONOGRAMME DES ACTIVITÉS",
                      "6. BUDGET PRÉVISIONNEL",
                    ].map((pillText, idx) => (
                      <div
                        key={idx}
                        className="flex h-[13%] items-center pl-8 text-xs font-black text-slate-800"
                      >
                        {pillText}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* SLIDES INTRODUCTION / OBJECTIFS / MÉTHODOLOGIE AVEC TEMPLATES */}
              {(activeSlide?.slide_type === "introduction" ||
                activeSlide?.slide_type === "objective_general" ||
                activeSlide?.slide_type === "objective_specific" ||
                activeSlide?.slide_type === "methodology") && (
                <div className="relative h-full w-full overflow-hidden">
                  <img
                    src={
                      activeSlide.slide_type === "introduction"
                        ? "/tamplates/tamplate diapositive introduction.png"
                        : activeSlide.slide_type === "objective_general"
                        ? "/tamplates/tamplate diapositive objectif générale.png"
                        : activeSlide.slide_type === "objective_specific"
                        ? "/tamplates/tamplate objectifs spécifiques.png"
                        : "/tamplates/tamplate diapositive  méthodologie.png"
                    }
                    alt={activeSlide.title}
                    className="absolute inset-0 h-full w-full object-cover"
                  />
                  {/* Top Title Capsule */}
                  <div className="absolute left-[17%] top-[3.5%] w-[68%] h-[12%] flex items-center">
                    <h2 className="text-base sm:text-lg font-black text-[#004B87] truncate">
                      {activeSlide.title}
                    </h2>
                  </div>

                  {/* Body Content */}
                  <div className="absolute left-[8%] right-[8%] top-[25%] bottom-[12%] flex flex-col justify-around gap-2 p-2">
                    {activeSlide.slide_type === "objective_general" ? (
                      <div className="my-auto rounded-2xl border-2 border-[#004B87]/30 bg-white/90 p-6 text-center shadow-md">
                        <p className="text-base sm:text-lg font-bold text-slate-900 leading-relaxed">
                          {activeSlide.elements[0]?.content || activeSlide.subtitle}
                        </p>
                      </div>
                    ) : (
                      activeSlide.elements.map((el) => (
                        <div
                          key={el.id}
                          className="flex items-start gap-2.5 rounded-xl bg-white/85 p-2.5 shadow-sm border border-slate-100"
                        >
                          <span className="mt-0.5 text-base font-black text-[#004B87] leading-none">
                            •
                          </span>
                          <p className="text-xs sm:text-sm font-semibold text-slate-800 leading-snug">
                            {el.content}
                          </p>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              )}

              {/* SLIDE 15: CHRONOGRAMME (DÉPOSÉE TELLE QUELLE SANS TEXTE !) */}
              {activeSlide?.slide_type === "chronogram" && (
                <div className="relative h-full w-full overflow-hidden bg-white">
                  <img
                    src="/tamplates/Diapositive..Chronogramme des activités 2026–2027 (1).png"
                    alt="Chronogramme Gantt 3D"
                    className="absolute inset-0 h-full w-full object-contain"
                  />
                </div>
              )}

              {/* SLIDE 16: BUDGET RADIAL */}
              {activeSlide?.slide_type === "budget" && (
                <div className="relative h-full w-full overflow-hidden">
                  <img
                    src="/tamplates/Tamplate diapositive budget.png"
                    alt="Budget Radial"
                    className="absolute inset-0 h-full w-full object-cover"
                  />
                  {/* Center Total Box in white pill */}
                  <div className="absolute left-[43%] top-[53%] w-[14%] h-[7%] flex items-center justify-center text-center">
                    <span className="text-xs sm:text-sm font-black text-[#004B87]">
                      {activeSlide.subtitle || "1 250 000 FCFA"}
                    </span>
                  </div>
                  {/* 5 Left Pills */}
                  <div className="absolute left-[9.5%] top-[17%] bottom-[5.5%] w-[23.5%] flex flex-col justify-between">
                    {activeSlide.elements.slice(0, 5).map((el, i) => (
                      <div key={i} className="flex h-[13%] items-center px-1 text-[9px] sm:text-[10px] font-bold text-slate-900 leading-tight">
                        <span className="truncate">{el.content}</span>
                      </div>
                    ))}
                  </div>
                  {/* 5 Right Pills */}
                  <div className="absolute right-[9.5%] top-[17%] bottom-[5.5%] w-[23.5%] flex flex-col justify-between">
                    {activeSlide.elements.slice(5, 10).map((el, i) => (
                      <div key={i} className="flex h-[13%] items-center px-1 text-[9px] sm:text-[10px] font-bold text-slate-900 leading-tight">
                        <span className="truncate">{el.content}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* SLIDE 17: REMERCIEMENTS & CLÔTURE */}
              {activeSlide?.slide_type === "acknowledgements" && (
                <div className="relative h-full w-full overflow-hidden">
                  <img
                    src="/tamplates/Merci pour votre attention !.png"
                    alt="Remerciements"
                    className="absolute inset-0 h-full w-full object-cover"
                  />
                  <div className="absolute right-[9%] top-[30%] w-[28%] text-center">
                    <h3 className="text-base font-black text-[#004B87]">
                      MERCI POUR VOTRE AIMABLE ATTENTION
                    </h3>
                    <p className="mt-2 text-xs font-bold italic text-slate-700">
                      « Sapientia Collativa Cognitio »
                    </p>
                    <p className="mt-1 text-xs font-bold text-slate-800">
                      {project.cover.author_name || "MBIANDA TCHOUAFA BRICE KEVIN"}
                    </p>
                    <p className="mt-1 text-[11px] text-slate-500">
                      Faculté de Médecine et des Sciences Biomédicales
                    </p>
                  </div>
                </div>
              )}

              {/* STANDARD CONTENT SLIDES */}
              {activeSlide?.slide_type === "content" && (
                <div className="flex h-full flex-col p-8">
                  {/* Top Header with Breadcrumb and Progress Indexer */}
                  <div className="flex items-start justify-between border-b border-[#6C2826]/20 pb-3">
                    <div>
                      <div className="text-xs font-bold uppercase tracking-wider text-[#6C2826]">
                        {activeSlide.breadcrumb || "MÉTHODOLOGIE"}
                      </div>
                      <input
                        type="text"
                        value={activeSlide.title}
                        onChange={(e) => updateActiveSlide({ title: e.target.value })}
                        className="mt-0.5 w-full border-none bg-transparent p-0 text-2xl font-black text-slate-900 focus:outline-none focus:ring-0"
                      />
                    </div>
                    <div className="rounded-lg border border-[#6C2826] bg-[#6C2826]/10 px-3 py-1 text-xs font-black text-[#6C2826]">
                      {activeSlide.slide_index < 10 ? `0${activeSlide.slide_index}` : activeSlide.slide_index} / {deck.slides.length}
                    </div>
                  </div>

                  {/* Body Content Layout */}
                  <div className="mt-4 grid flex-1 grid-cols-12 gap-4">
                    {/* Left Column: Bullets */}
                    <div
                      className={`flex flex-col justify-around gap-2.5 ${
                        activeSlide.elements.some((el) => el.element_type === "media")
                          ? "col-span-7"
                          : "col-span-12"
                      }`}
                    >
                      {activeSlide.elements
                        .filter((el) => el.element_type !== "media")
                        .map((el) => (
                          <div
                            key={el.id}
                            className="group flex items-start gap-3 rounded-xl border border-slate-200 bg-white p-3.5 shadow-sm transition hover:border-[#6C2826]/40"
                          >
                            <span className="mt-1 text-lg font-black text-[#6C2826] leading-none">
                              •
                            </span>
                            <textarea
                              rows={2}
                              value={el.content}
                              onChange={(e) => handleUpdateElement(el.id, e.target.value)}
                              className="w-full resize-none border-none bg-transparent p-0 text-sm font-semibold text-slate-800 leading-snug focus:outline-none focus:ring-0"
                            />
                            <button
                              onClick={() => handleRemoveElement(el.id)}
                              className="opacity-0 transition group-hover:opacity-100 text-slate-400 hover:text-rose-600"
                              title="Supprimer cette puce"
                            >
                              <Trash2 className="h-4 w-4" />
                            </button>
                          </div>
                        ))}

                      <button
                        onClick={handleAddBullet}
                        className="flex items-center justify-center gap-2 rounded-xl border border-dashed border-slate-300 py-2 text-xs font-semibold text-slate-500 hover:border-[#6C2826] hover:text-[#6C2826]"
                      >
                        <Plus className="h-3.5 w-3.5" />
                        Ajouter un point clé
                      </button>
                    </div>

                    {/* Right Column: Media / Figure if present */}
                    {activeSlide.elements.some((el) => el.element_type === "media") && (
                      <div className="col-span-5 flex flex-col items-center justify-center rounded-xl border border-slate-200 bg-slate-50 p-3 text-center shadow-inner">
                        {(() => {
                          const media = activeSlide.elements.find(
                            (el) => el.element_type === "media"
                          );
                          return (
                            <>
                              <div className="relative flex aspect-video w-full items-center justify-center overflow-hidden rounded-lg bg-white border border-slate-200">
                                <ImageIcon className="h-10 w-10 text-slate-300" />
                              </div>
                              <span className="mt-2 text-[11px] font-semibold text-slate-600">
                                {media?.title || "Figure de la recherche"}
                              </span>
                            </>
                          );
                        })()}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Bottom Area: Presenter Notes */}
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-3">
              <div className="flex items-center gap-2">
                <Mic className="h-5 w-5 text-[#6C2826]" />
                <h3 className="text-sm font-bold text-slate-900">
                  Notes de Présentation Orale (Discours de l'Impétrant devant le Jury)
                </h3>
              </div>
              <div className="flex items-center gap-3 text-xs text-slate-500">
                <span className="inline-flex items-center gap-1 font-semibold text-slate-700">
                  <Clock className="h-3.5 w-3.5 text-amber-600" />
                  ~{estimatedSeconds} secondes
                </span>
                <span>•</span>
                <span>{wordCount} mots</span>
                <span>(Cadence idéale : 120-140 mots/min)</span>
              </div>
            </div>

            <p className="mt-2 text-xs text-slate-500">
              Ces notes sont invisibles sur l'écran de projection : elles apparaissent en mode Présentateur PowerPoint
              et vous guident pas à pas lors de votre élocution.
            </p>

            <textarea
              rows={4}
              value={activeSlide?.presenter_notes || ""}
              onChange={(e) => updateActiveSlide({ presenter_notes: e.target.value })}
              placeholder="Rédigez ici ce que vous direz oralement au jury pour cette diapositive..."
              className="mt-3 w-full rounded-xl border border-slate-200 bg-slate-50/50 p-3 text-xs leading-relaxed text-slate-800 focus:border-[#6C2826] focus:bg-white focus:outline-none"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
