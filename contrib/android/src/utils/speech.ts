/**
 * Normalizes Markdown prose so that phone TTS engines pronounce
 * natural English sentences instead of reading syntax characters aloud.
 */
export function cleanMarkdownForSpeech(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, ' [code omitted] ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/\[\[([^\]]+)\]\]/g, '$1')
    .replace(/[*_]{1,3}([^*_]+)[*_]{1,3}/g, '$1')
    .replace(/^[#]{1,6}\s+/gm, '')
    .replace(/^[-*+]\s+/gm, '')
    .replace(/\n+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/**
 * Turns obscure Google TTS identifiers into clean, human-readable labels.
 * e.g. "en-us-x-sfg-local" -> "US • SFG"
 *      "en-gb-x-rjs-local" -> "UK • RJS"
 *      "en-us-x-star11-local" -> "US • Star 11"
 */
export function formatVoiceLabel(identifier: string): string {
  let region = 'US';
  if (identifier.startsWith('en-gb') || identifier.startsWith('en-GB')) region = 'UK';
  else if (identifier.startsWith('en-au') || identifier.startsWith('en-AU')) region = 'AU';
  else if (identifier.startsWith('en-in') || identifier.startsWith('en-IN')) region = 'IN';
  else if (identifier.startsWith('en-ng') || identifier.startsWith('en-NG')) region = 'NG';

  const match = identifier.match(/^en-[a-zA-Z]{2}-x-(.+?)(?:-(?:local|network))?$/);
  if (match) {
    let name = match[1];
    if (name.startsWith('star')) {
      name = 'Star ' + name.replace('star', '');
    } else {
      name = name.toUpperCase();
    }
    return `${region} • ${name}`;
  }

  return identifier.replace(/-language$/, '').replace(/^en-/, '').toUpperCase();
}
