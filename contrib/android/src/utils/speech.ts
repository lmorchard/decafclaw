/**
 * Normalizes Markdown prose so that phone TTS engines pronounce
 * natural English sentences instead of reading syntax characters aloud.
 */
export function cleanMarkdownForSpeech(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, ' [code omitted] ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, '$2')
    .replace(/\[\[([^\]]+)\]\]/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/<[^>]+>/g, ' ')
    .replace(/https?:\/\/\S+/g, ' link ')
    .replace(/^\s{0,3}>\s?/gm, '')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/\s+#{1,6}\s*$/gm, '')
    .replace(/^\s{0,3}(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?/gm, '')
    .replace(/^\s*\|?[\s:|-]+\|[\s:|-]*$/gm, ' ')
    .replace(/\|/g, ' ')
    .replace(/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/gm, ' ')
    .replace(/(\*\*|__)(.+?)\1/g, '$2')
    .replace(/\*([^*\n]+)\*/g, '$1')
    .replace(/(^|[\s(])_([^_\n]+)_(?=$|[\s).,;:!?])/g, '$1$2')
    .replace(/\s+/g, ' ')
    .trim();
}

const REGION_LABELS: Record<string, string> = {
  GB: 'UK',
};

/**
 * Turns obscure Google TTS identifiers into clean, human-readable labels.
 * e.g. "en-us-x-sfg-local" -> "US • SFG"
 *      "en-gb-x-rjs-local" -> "UK • RJS"
 *      "en-us-x-star11-local" -> "US • Star 11"
 *      "en-ie-x-...-local" -> "IE • ..."
 */
export function formatVoiceLabel(identifier: string): string {
  const match = identifier.match(
    /^([a-z]{2})-([a-z]{2})(?:-x-(.+?))?(?:-(?:local|network))?(?:#.*)?$/i
  );
  if (!match) {
    return identifier.replace(/-language$/i, '').replace(/^[a-z]{2}-/i, '').toUpperCase();
  }

  const regionCode = match[2].toUpperCase();
  const region = REGION_LABELS[regionCode] ?? regionCode;
  const rawName = match[3]?.replace(/#.*$/, '');

  if (!rawName) {
    return region;
  }

  const name = rawName.startsWith('star')
    ? `Star ${rawName.slice('star'.length)}`
    : rawName.toUpperCase();
  return `${region} • ${name}`;
}
