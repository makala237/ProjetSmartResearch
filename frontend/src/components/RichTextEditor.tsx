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
  Underline as UnderlineIcon,
} from "lucide-react";
import { useEffect } from "react";

interface RichTextEditorProps {
  contentHtml: string;
  contentMarkdown: string;
  onChange: (html: string, plainText: string) => void;
  placeholder?: string;
}

export function RichTextEditor({
  contentHtml,
  contentMarkdown,
  onChange,
}: RichTextEditorProps) {
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
    ],
    content: initialContent,
    editorProps: {
      attributes: {
        class:
          "min-h-[220px] max-h-[480px] overflow-y-auto p-4 text-sm leading-relaxed text-slate-900 focus:outline-none",
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
    <div className="rounded-xl border border-slate-300 bg-white shadow-sm focus-within:border-indigo-500 focus-within:ring-1 focus-within:ring-indigo-500">
      {/* TipTap Toolbar */}
      <div className="flex flex-wrap items-center gap-1 border-b border-slate-200 bg-slate-50/80 px-2.5 py-1.5 text-xs">
        <button
          type="button"
          onClick={() => editor.chain().focus().toggleBold().run()}
          className={btnClass(editor.isActive("bold"))}
          title="Gras (Ctrl+B)"
        >
          <Bold className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={() => editor.chain().focus().toggleItalic().run()}
          className={btnClass(editor.isActive("italic"))}
          title="Italique (Ctrl+I)"
        >
          <Italic className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={() => editor.chain().focus().toggleUnderline().run()}
          className={btnClass(editor.isActive("underline"))}
          title="Souligné (Ctrl+U)"
        >
          <UnderlineIcon className="h-3.5 w-3.5" />
        </button>

        <span className="mx-1 h-4 w-px bg-slate-300" />

        <button
          type="button"
          onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}
          className={btnClass(editor.isActive("heading", { level: 2 }))}
          title="Titre de niveau 2"
        >
          <Heading2 className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={() => editor.chain().focus().toggleHeading({ level: 3 }).run()}
          className={btnClass(editor.isActive("heading", { level: 3 }))}
          title="Titre de niveau 3"
        >
          <Heading3 className="h-3.5 w-3.5" />
        </button>

        <span className="mx-1 h-4 w-px bg-slate-300" />

        <button
          type="button"
          onClick={() => editor.chain().focus().toggleBulletList().run()}
          className={btnClass(editor.isActive("bulletList"))}
          title="Liste à puces"
        >
          <List className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={() => editor.chain().focus().toggleOrderedList().run()}
          className={btnClass(editor.isActive("orderedList"))}
          title="Liste numérotée"
        >
          <ListOrdered className="h-3.5 w-3.5" />
        </button>

        <span className="mx-1 h-4 w-px bg-slate-300" />

        <button
          type="button"
          onClick={() => editor.chain().focus().setTextAlign("left").run()}
          className={btnClass(editor.isActive({ textAlign: "left" }))}
          title="Aligner à gauche"
        >
          <AlignLeft className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={() => editor.chain().focus().setTextAlign("center").run()}
          className={btnClass(editor.isActive({ textAlign: "center" }))}
          title="Centrer"
        >
          <AlignCenter className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={() => editor.chain().focus().setTextAlign("justify").run()}
          className={btnClass(editor.isActive({ textAlign: "justify" }))}
          title="Justifier (FMSB)"
        >
          <AlignJustify className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Editor Content Area */}
      <EditorContent editor={editor} />
    </div>
  );
}
