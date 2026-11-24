import { Extension } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import { useEditor, EditorContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import TextAlign from "@tiptap/extension-text-align";
import Underline from "@tiptap/extension-underline";
import {
  AlignCenter,
  AlignJustify,
  AlignLeft,
  Bold,
  BookOpen,
  Edit3,
  Heading2,
  Heading3,
  Italic,
  List,
  ListOrdered,
  Loader2,
  Maximize2,
  Minimize2,
  PlusCircle,
  Redo2,
  Shrink,
  Sparkles,
  Underline as UnderlineIcon,
  Undo2,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useLanguage } from "../i18n/LanguageContext";
import { createPlaceholderRegex } from "../utils/investigatorPlaceholders";

export type SelectionTransformAction =
  | "reformulate"
  | "modify"
  | "complement"
  | "cite"
  | "shorten"
  | "expand";

const InvestigatorPlaceholderExtension = Extension.create({
  name: "investigatorPlaceholderHighlight",
  addProseMirrorPlugins() {
    return [
      new Plugin({
        key: new PluginKey("investigatorPlaceholderHighlight"),
        props: {
          decorations(state) {
            const decorations: Decoration[] = [];
            state.doc.descendants((node, pos) => {
              if (!node.isText || !node.text) return;
              const regex = createPlaceholderRegex();
              let match: RegExpExecArray | null;
              while ((match = regex.exec(node.text)) !== null) {
                const from = pos + match.index;
                const to = from + match[0].length;
                decorations.push(
                  Decoration.inline(from, to, {
                    class: "sr-placeholder-highlight",
                  })
                );
              }
            });
            return DecorationSet.create(state.doc, decorations);
          },
        },
      }),
    ];
  },
});

interface RichTextEditorProps {
  contentHtml: string;
  contentMarkdown: string;
  onChange: (html: string, plainText: string) => void;
  placeholder?: string;
  wordCount?: number;
  onSelectionAction?: (
    selectedText: string,
    action: SelectionTransformAction,
    instruction?: string
  ) => void;
  selectionBusy?: boolean;
  onExternalUndo?: () => void;
  canExternalUndo?: boolean;
  onExternalRedo?: () => void;
  canExternalRedo?: boolean;
}

export function RichTextEditor({
  contentHtml,
  contentMarkdown,
  onChange,
  wordCount,
  onSelectionAction,
  selectionBusy = false,
  onExternalUndo,
  canExternalUndo = false,
  onExternalRedo,
  canExternalRedo = false,
}: RichTextEditorProps) {
  const { lang } = useLanguage();
  const isEn = lang === "en";
  const [expanded, setExpanded] = useState(false);
  const [selectedText, setSelectedText] = useState("");
  const [showCustomPrompt, setShowCustomPrompt] = useState(false);
  const [customActionMode, setCustomActionMode] = useState<"modify" | "complement">("modify");
  const [customPrompt, setCustomPrompt] = useState("");

  const initialContent = contentHtml?.trim()
    ? contentHtml
    : contentMarkdown
    ? contentMarkdown
        .split("\n\n")
        .map((p) => `<p>${p.replace(/\n/g, "<br/>")}</p>`)
        .join("")
    : "<p></p>";

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: {
          levels: [2, 3],
        },
      }),
      Underline,
      TextAlign.configure({
        types: ["heading", "paragraph"],
      }),
      InvestigatorPlaceholderExtension,
    ],
    content: initialContent,
    editorProps: {
      attributes: {
        class: "px-5 py-4 text-[15px] leading-7 text-slate-900 focus:outline-none",
      },
    },
    onUpdate: ({ editor }) => {
      const html = editor.getHTML();
      const text = editor.getText();
      onChange(html, text);
    },
    onSelectionUpdate: ({ editor }) => {
      const { from, to, empty } = editor.state.selection;
      if (empty || to - from < 4) {
        setSelectedText("");
        setShowCustomPrompt(false);
        return;
      }
      const txt = editor.state.doc.textBetween(from, to, "\n").trim();
      if (txt.length >= 4) {
        setSelectedText(txt);
      } else {
        setSelectedText("");
        setShowCustomPrompt(false);
      }
    },
  });

  useEffect(() => {
    if (!editor) return;
    const currentHtml = editor.getHTML();
    const target = contentHtml?.trim()
      ? contentHtml
      : contentMarkdown
      ? contentMarkdown
          .split("\n\n")
          .map((p) => `<p>${p.replace(/\n/g, "<br/>")}</p>`)
          .join("")
      : "<p></p>";

    if (currentHtml !== target && editor.getText().trim() !== contentMarkdown.trim()) {
      editor.commands.setContent(target, { emitUpdate: false });
      setSelectedText("");
      setShowCustomPrompt(false);
    }
  }, [contentHtml, contentMarkdown, editor]);

  if (!editor) {
    return null;
  }

  const btnClass = (isActive: boolean) =>
    `p-1.5 rounded transition ${
      isActive
        ? "bg-indigo-100 text-indigo-800 font-semibold"
        : "text-slate-600 hover:bg-slate-100"
    }`;

  const canUndoNow = editor.can().undo() || canExternalUndo;
  const canRedoNow = editor.can().redo() || canExternalRedo;

  function handleUndoClick() {
    if (!editor) return;
    if (editor.can().undo()) {
      editor.chain().focus().undo().run();
    } else if (canExternalUndo && onExternalUndo) {
      onExternalUndo();
    }
  }

  function handleRedoClick() {
    if (!editor) return;
    if (editor.can().redo()) {
      editor.chain().focus().redo().run();
    } else if (canExternalRedo && onExternalRedo) {
      onExternalRedo();
    }
  }

  function triggerSelection(action: SelectionTransformAction, instr?: string) {
    if (!onSelectionAction || !selectedText.trim()) return;
    onSelectionAction(selectedText.trim(), action, instr);
    setShowCustomPrompt(false);
    setCustomPrompt("");
  }

  const selectionWordCount = selectedText
    ? selectedText.split(/\s+/).filter(Boolean).length
    : 0;

  return (
    <div
      className={`sr-rich-editor ${
        expanded ? "sr-rich-editor--expanded" : ""
      } overflow-hidden rounded-xl border border-slate-300 bg-white shadow-sm focus-within:border-indigo-500 focus-within:ring-2 focus-within:ring-indigo-500/20`}
    >
      {/* TipTap Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-slate-50/90 px-3 py-2 text-xs">
        <div className="flex flex-wrap items-center gap-1">
          {/* Word-style Undo / Redo */}
          <button
            type="button"
            disabled={!canUndoNow}
            onClick={handleUndoClick}
            className="rounded p-1.5 text-slate-700 hover:bg-slate-200/70 disabled:opacity-35 transition"
            title={isEn ? "Undo (Ctrl+Z)" : "Annuler la dernière modification (Ctrl+Z)"}
          >
            <Undo2 className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            disabled={!canRedoNow}
            onClick={handleRedoClick}
            className="rounded p-1.5 text-slate-700 hover:bg-slate-200/70 disabled:opacity-35 transition"
            title={isEn ? "Redo (Ctrl+Y)" : "Rétablir (Ctrl+Y)"}
          >
            <Redo2 className="h-3.5 w-3.5" />
          </button>

          <span className="mx-1 h-4 w-px bg-slate-300" />

          <button
            type="button"
            onClick={() => editor.chain().focus().toggleBold().run()}
            className={btnClass(editor.isActive("bold"))}
            title={isEn ? "Bold (Ctrl+B)" : "Gras (Ctrl+B)"}
          >
            <Bold className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => editor.chain().focus().toggleItalic().run()}
            className={btnClass(editor.isActive("italic"))}
            title={isEn ? "Italic (Ctrl+I)" : "Italique (Ctrl+I)"}
          >
            <Italic className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => editor.chain().focus().toggleUnderline().run()}
            className={btnClass(editor.isActive("underline"))}
            title={isEn ? "Underline (Ctrl+U)" : "Souligné (Ctrl+U)"}
          >
            <UnderlineIcon className="h-3.5 w-3.5" />
          </button>

          <span className="mx-1 h-4 w-px bg-slate-300" />

          <button
            type="button"
            onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}
            className={btnClass(editor.isActive("heading", { level: 2 }))}
            title={isEn ? "Heading 2" : "Titre de niveau 2"}
          >
            <Heading2 className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()}
            className={btnClass(editor.isActive("heading", { level: 3 }))}
            title={isEn ? "Heading 3" : "Titre de niveau 3"}
          >
            <Heading3 className="h-3.5 w-3.5" />
          </button>

          <span className="mx-1 h-4 w-px bg-slate-300" />

          <button
            type="button"
            onClick={() => editor.chain().focus().toggleBulletList().run()}
            className={btnClass(editor.isActive("bulletList"))}
            title={isEn ? "Bullet List" : "Liste à puces"}
          >
            <List className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => editor.chain().focus().toggleOrderedList().run()}
            className={btnClass(editor.isActive("orderedList"))}
            title={isEn ? "Numbered List" : "Liste numérotée"}
          >
            <ListOrdered className="h-3.5 w-3.5" />
          </button>

          <span className="mx-1 h-4 w-px bg-slate-300" />

          <button
            type="button"
            onClick={() => editor.chain().focus().setTextAlign("left").run()}
            className={btnClass(editor.isActive({ textAlign: "left" }))}
            title={isEn ? "Align Left" : "Aligner à gauche"}
          >
            <AlignLeft className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => editor.chain().focus().setTextAlign("center").run()}
            className={btnClass(editor.isActive({ textAlign: "center" }))}
            title={isEn ? "Center" : "Centrer"}
          >
            <AlignCenter className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => editor.chain().focus().setTextAlign("justify").run()}
            className={btnClass(editor.isActive({ textAlign: "justify" }))}
            title={isEn ? "Justify (FMSB)" : "Justifier (FMSB)"}
          >
            <AlignJustify className="h-3.5 w-3.5" />
          </button>
        </div>

        <div className="flex items-center gap-2">
          {wordCount !== undefined && (
            <span className="rounded-full bg-white px-2.5 py-0.5 text-[11px] font-medium text-slate-600 ring-1 ring-slate-200">
              {wordCount}{" "}
              {isEn
                ? wordCount === 1
                  ? "word"
                  : "words"
                : wordCount <= 1
                ? "mot"
                : "mots"}
            </span>
          )}
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-pressed={expanded}
            className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1 text-[11px] font-semibold transition ${
              expanded
                ? "border-indigo-300 bg-indigo-50 text-indigo-800 hover:bg-indigo-100"
                : "border-slate-200 bg-white text-slate-700 hover:bg-slate-100"
            }`}
            title={
              expanded
                ? isEn
                  ? "Compact editor height"
                  : "Réduire la hauteur de l'éditeur"
                : isEn
                ? "Expand editor vertically"
                : "Agrandir l'éditeur en hauteur"
            }
          >
            {expanded ? (
              <Minimize2 className="h-3.5 w-3.5" />
            ) : (
              <Maximize2 className="h-3.5 w-3.5" />
            )}
            <span>
              {expanded
                ? isEn
                  ? "Compact"
                  : "Réduire"
                : isEn
                ? "Expand editor"
                : "Agrandir l'éditeur"}
            </span>
          </button>
        </div>
      </div>

      {/* Contextual Floating Selection AI Bar */}
      {onSelectionAction && selectedText && (
        <div
          className="border-b border-indigo-200 bg-indigo-50/90 px-3 py-2 text-xs"
          onMouseDown={(e) => {
            // Keep TipTap text selection visible while clicking toolbar buttons (except inside input)
            if ((e.target as HTMLElement).tagName !== "INPUT") {
              e.preventDefault();
            }
          }}
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="inline-flex items-center gap-1 rounded-full bg-indigo-600 px-2 py-0.5 text-[10px] font-bold text-white">
                <Sparkles className="h-3 w-3" />
                {isEn
                  ? `Selection (${selectionWordCount}w)`
                  : `Sélection (${selectionWordCount} mots)`}
              </span>

              <button
                type="button"
                disabled={selectionBusy}
                onClick={() => triggerSelection("reformulate")}
                className="inline-flex items-center gap-1 rounded-md border border-indigo-200 bg-white px-2.5 py-1 text-[11px] font-semibold text-indigo-800 hover:bg-indigo-100 disabled:opacity-50"
              >
                {selectionBusy ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <Sparkles className="h-3 w-3" />
                )}
                {isEn ? "Reformulate" : "Reformuler"}
              </button>

              <button
                type="button"
                disabled={selectionBusy}
                onClick={() => {
                  setCustomActionMode("modify");
                  setShowCustomPrompt(true);
                }}
                className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2.5 py-1 text-[11px] font-semibold text-slate-700 hover:bg-slate-100 disabled:opacity-50"
              >
                <Edit3 className="h-3 w-3 text-indigo-600" />
                {isEn ? "Edit with instruction…" : "Modifier…"}
              </button>

              <button
                type="button"
                disabled={selectionBusy}
                onClick={() => {
                  setCustomActionMode("complement");
                  setShowCustomPrompt(true);
                }}
                className="inline-flex items-center gap-1 rounded-md border border-emerald-200 bg-white px-2.5 py-1 text-[11px] font-semibold text-emerald-800 hover:bg-emerald-50 disabled:opacity-50"
              >
                <PlusCircle className="h-3 w-3" />
                {isEn ? "Complement" : "Compléter"}
              </button>

              <button
                type="button"
                disabled={selectionBusy}
                onClick={() => triggerSelection("cite")}
                className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2.5 py-1 text-[11px] font-semibold text-slate-700 hover:bg-slate-100 disabled:opacity-50"
              >
                <BookOpen className="h-3 w-3 text-amber-600" />
                {isEn ? "Add Vancouver cites" : "Citer [n]"}
              </button>

              <button
                type="button"
                disabled={selectionBusy}
                onClick={() => triggerSelection("shorten")}
                className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-50"
              >
                <Shrink className="h-3 w-3" />
                {isEn ? "Shorten" : "Raccourcir"}
              </button>

              <button
                type="button"
                disabled={selectionBusy}
                onClick={() => triggerSelection("expand")}
                className="inline-flex items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-50"
              >
                {isEn ? "Expand" : "Développer"}
              </button>
            </div>

            <button
              type="button"
              onClick={() => {
                setSelectedText("");
                setShowCustomPrompt(false);
              }}
              className="rounded p-1 text-slate-400 hover:bg-indigo-100 hover:text-slate-700"
              title={isEn ? "Close selection bar" : "Fermer"}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>

          {showCustomPrompt && (
            <form
              className="mt-2 flex items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                triggerSelection(customActionMode, customPrompt.trim());
              }}
            >
              <input
                type="text"
                autoFocus
                value={customPrompt}
                onChange={(e) => setCustomPrompt(e.target.value)}
                placeholder={
                  customActionMode === "complement"
                    ? isEn
                      ? "What complementary information to add after this passage? (optional)"
                      : "Quel complément d'information ajouter après ce passage ? (optionnel)"
                    : isEn
                    ? "How should this selected passage be modified?"
                    : "Comment modifier ce passage sélectionné ?"
                }
                className="flex-1 rounded-lg border border-indigo-300 bg-white px-3 py-1.5 text-xs focus:border-indigo-500 focus:outline-none"
              />
              <button
                type="submit"
                disabled={selectionBusy}
                className="inline-flex items-center gap-1 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {selectionBusy ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Sparkles className="h-3.5 w-3.5" />
                )}
                {isEn ? "Run" : "Lancer"}
              </button>
            </form>
          )}
        </div>
      )}

      {/* Editor Content Area */}
      <div className={expanded ? "max-h-none overflow-y-visible" : "max-h-[72vh] overflow-y-auto"}>
        <EditorContent editor={editor} />
      </div>
    </div>
  );
}
