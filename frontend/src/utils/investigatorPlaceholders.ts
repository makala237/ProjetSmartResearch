export const INVESTIGATOR_PLACEHOLDER_SOURCE =
  "\\[(?:\\s*(?:À|A)\\s+compl[ée]ter|\\s*To\\s+be\\s+completed|\\s*(?:À|A)\\s+renseigner)[^\\]]*\\]";

export function createPlaceholderRegex(): RegExp {
  return new RegExp(INVESTIGATOR_PLACEHOLDER_SOURCE, "gi");
}

export function extractInvestigatorPlaceholders(text: string): string[] {
  if (!text) return [];
  const matches = text.match(createPlaceholderRegex());
  return matches ? Array.from(matches) : [];
}

export interface PlaceholderTextPart {
  text: string;
  isPlaceholder: boolean;
}

export function splitTextWithPlaceholders(text: string): PlaceholderTextPart[] {
  if (!text) return [];
  const regex = createPlaceholderRegex();
  const parts: PlaceholderTextPart[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push({
        text: text.slice(lastIndex, match.index),
        isPlaceholder: false,
      });
    }
    parts.push({
      text: match[0],
      isPlaceholder: true,
    });
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) {
    parts.push({
      text: text.slice(lastIndex),
      isPlaceholder: false,
    });
  }

  return parts.length > 0 ? parts : [{ text, isPlaceholder: false }];
}

export function highlightPlaceholdersInHtml(html: string): string {
  if (!html) return "";
  // Only replace inside text nodes (outside <...> HTML tags)
  return html
    .split(/(<[^>]+>)/g)
    .map((chunk) => {
      if (chunk.startsWith("<") && chunk.endsWith(">")) {
        return chunk;
      }
      return chunk.replace(
        createPlaceholderRegex(),
        (m) => `<mark class="sr-placeholder-highlight">${m}</mark>`
      );
    })
    .join("");
}
