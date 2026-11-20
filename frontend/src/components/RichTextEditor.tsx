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
  Heading2,
  Heading3,
  Italic,
  List,
  ListOrdered,
  Maximize2,
  Minimize2,
  Underline as UnderlineIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useLanguage } from "../i18n/LanguageContext";
import { createPlaceholderRegex } from "../utils/investigatorPlaceholders";

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
}

export function RichTextEditor({
  contentHtml,
  contentMarkdown,
  onChange,
  wordCount,
}: RichTextEditorProps) {
  const { lang } = useLanguage();
  const [expanded, setExpanded] = useState(false);

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
      editor.commands.setContent(target);
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

  return (
    <div
      className={`sr-rich-editor ${
        expanded ? "sr-rich-editor--expanded" : ""
      } overflow-hidden rounded-xl border border-slate-300 bg-white shadow-sm focus-within:border-indigo-500 focus-within:ring-2 focus-within:ring-indigo-500/20`}
    >
      {/* TipTap Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-slate-50/90 px-3 py-2 text-xs">
        <div className="flex flex-wrap items-center gap-1">
          <button
            type="button"
            onClick={() => editor.chain().focus().toggleBold().run()}
            className={btnClass(editor.isActive("bold"))}
            title={lang === "en" ? "Bold (Ctrl+B)" : "Gras (Ctrl+B)"}
          >
            <Bold className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => editor.chain().focus().toggleItalic().run()}
            className={btnClass(editor.isActive("italic"))}
            title={lang === "en" ? "Italic (Ctrl+I)" : "Italique (Ctrl+I)"}
          >
            <Italic className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => editor.chain().focus().toggleUnderline().run()}
            className={btnClass(editor.isActive("underline"))}
            title={lang === "en" ? "Underline (Ctrl+U)" : "Souligné (Ctrl+U)"}
          >
            <UnderlineIcon className="h-3.5 w-3.5" />
          </button>

          <span className="mx-1 h-4 w-px bg-slate-300" />

          <button
            type="button"
            onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}
            className={btnClass(editor.isActive("heading", { level: 2 }))}
            title={lang === "en" ? "Heading 2" : "Titre de niveau 2"}
          >
            <Heading2 className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()}
            className={btnClass(editor.isActive("heading", { level: 3 }))}
            title={lang === "en" ? "Heading 3" : "Titre de niveau 3"}
          >
            <Heading3 className="h-3.5 w-3.5" />
          </button>

          <span className="mx-1 h-4 w-px bg-slate-300" />

          <button
            type="button"
            onClick={() => editor.chain().focus().toggleBulletList().run()}
            className={btnClass(editor.isActive("bulletList"))}
            title={lang === "en" ? "Bullet List" : "Liste à puces"}
          >
            <List className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => editor.chain().focus().toggleOrderedList().run()}
            className={btnClass(editor.isActive("orderedList"))}
            title={lang === "en" ? "Numbered List" : "Liste numérotée"}
          >
            <ListOrdered className="h-3.5 w-3.5" />
          </button>

          <span className="mx-1 h-4 w-px bg-slate-300" />

          <button
            type="button"
            onClick={() => editor.chain().focus().setTextAlign("left").run()}
            className={btnClass(editor.isActive({ textAlign: "left" }))}
            title={lang === "en" ? "Align Left" : "Aligner à gauche"}
          >
            <AlignLeft className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => editor.chain().focus().setTextAlign("center").run()}
            className={btnClass(editor.isActive({ textAlign: "center" }))}
            title={lang === "en" ? "Center" : "Centrer"}
          >
            <AlignCenter className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => editor.chain().focus().setTextAlign("justify").run()}
            className={btnClass(editor.isActive({ textAlign: "justify" }))}
            title={lang === "en" ? "Justify (FMSB)" : "Justifier (FMSB)"}
          >
            <AlignJustify className="h-3.5 w-3.5" />
          </button>
        </div>

        <div className="flex items-center gap-2">
          {wordCount !== undefined && (
            <span className="rounded-full bg-white px-2.5 py-0.5 text-[11px] font-medium text-slate-600 ring-1 ring-slate-200">
              {wordCount}{" "}
              {lang === "en"
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
                ? lang === "en"
                  ? "Compact editor height"
                  : "Réduire la hauteur de l'éditeur"
                : lang === "en"
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
                ? lang === "en"
                  ? "Compact"
                  : "Réduire"
                : lang === "en"
                ? "Expand editor"
                : "Agrandir l'éditeur"}
            </span>
          </button>
        </div>
      </div>

      {/* Editor Content Area */}
      <div className={expanded ? "max-h-none overflow-y-visible" : "max-h-[72vh] overflow-y-auto"}>
        <EditorContent editor={editor} />
      </div>
    </div>
  );
}
