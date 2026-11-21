import { describe, expect, it } from 'vitest';

import { cleanMarkdownForSpeech, formatVoiceLabel } from './speech';

describe('cleanMarkdownForSpeech', () => {
  it('strips emphasis and inline code', () => {
    expect(cleanMarkdownForSpeech('Hello **world**. Use `x + y`.')).toBe(
      'Hello world. Use x + y.'
    );
    expect(cleanMarkdownForSpeech('This is *very* good and _also_ fine.')).toBe(
      'This is very good and also fine.'
    );
  });

  it('replaces code fences with a spoken marker', () => {
    expect(cleanMarkdownForSpeech('```js\nconst a = 1;\n```\nDone.')).toBe(
      '[code omitted] Done.'
    );
  });

  it('unwraps links and wiki links', () => {
    expect(cleanMarkdownForSpeech('See [the docs](https://example.com).')).toBe(
      'See the docs.'
    );
    expect(cleanMarkdownForSpeech('See [[My Page|the wiki page]].')).toBe(
      'See the wiki page.'
    );
    expect(cleanMarkdownForSpeech('See [[My Page]].')).toBe('See My Page.');
  });

  it('drops images, HTML tags, and bare URLs', () => {
    expect(cleanMarkdownForSpeech('![alt](x.png) Picture.')).toBe('Picture.');
    expect(cleanMarkdownForSpeech('<b>Bold</b> text.')).toBe('Bold text.');
    expect(cleanMarkdownForSpeech('Visit https://example.com/path now.')).toBe(
      'Visit link now.'
    );
  });

  it('strips headers, lists, blockquotes, tables, and rules', () => {
    const markdown = [
      '# Title',
      '- first',
      '- [x] second',
      '1. one',
      '2. two',
      '> quoted',
      '---',
    ].join('\n');
    expect(cleanMarkdownForSpeech(markdown)).toBe(
      'Title first second one two quoted'
    );

    const table = ['| Name | Value |', '| --- | --- |', '| a | 1 |'].join('\n');
    expect(cleanMarkdownForSpeech(table)).toBe('Name Value a 1');
  });

  it('leaves decimals and ordinary prose alone', () => {
    expect(cleanMarkdownForSpeech('Pi is 3.14 today.')).toBe('Pi is 3.14 today.');
  });
});

describe('formatVoiceLabel', () => {
  it('formats Google voice identifiers', () => {
    expect(formatVoiceLabel('en-us-x-sfg-local')).toBe('US • SFG');
    expect(formatVoiceLabel('en-gb-x-rjs-local')).toBe('UK • RJS');
    expect(formatVoiceLabel('en-au-x-star11-local')).toBe('AU • Star 11');
  });

  it('uses the region code for regions without an alias', () => {
    expect(formatVoiceLabel('en-ie-x-abc-local')).toBe('IE • ABC');
    expect(formatVoiceLabel('en-ca-x-abc-network')).toBe('CA • ABC');
  });

  it('tolerates locale-only and unknown identifiers', () => {
    expect(formatVoiceLabel('en-us-language')).toBe('US');
    expect(formatVoiceLabel('fr-fr-x-abc-local')).toBe('FR • ABC');
    expect(formatVoiceLabel('something-else')).toBe('SOMETHING-ELSE');
  });
});
