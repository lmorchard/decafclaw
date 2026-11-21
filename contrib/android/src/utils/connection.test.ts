import { describe, expect, it } from 'vitest';

import { conversationWebUrl, normalizeHost } from './connection';

describe('normalizeHost', () => {
  it('keeps valid http(s) hosts', () => {
    expect(normalizeHost('http://decafclaw:18880')).toBe('http://decafclaw:18880');
    expect(normalizeHost('https://example.com/base')).toBe('https://example.com/base');
  });

  it('trims whitespace and trailing slashes', () => {
    expect(normalizeHost('  http://decafclaw:18880/  ')).toBe('http://decafclaw:18880');
    expect(normalizeHost('https://example.com/base/')).toBe('https://example.com/base');
  });

  it('defaults to http when the scheme is omitted', () => {
    expect(normalizeHost('decafclaw:18880')).toBe('http://decafclaw:18880');
    expect(normalizeHost('example.com/base')).toBe('http://example.com/base');
  });

  it('rejects unusable input', () => {
    expect(normalizeHost('')).toBeNull();
    expect(normalizeHost('   ')).toBeNull();
    expect(normalizeHost('ftp://example.com')).toBeNull();
    expect(normalizeHost('http://')).toBeNull();
    expect(normalizeHost('http://example.com/path?x=1')).toBeNull();
  });
});

describe('conversationWebUrl', () => {
  it('deep-links the conversation', () => {
    expect(conversationWebUrl('http://decafclaw:18880', 'web-lmorchard-abc')).toBe(
      'http://decafclaw:18880/?conv=web-lmorchard-abc'
    );
    expect(conversationWebUrl('http://decafclaw:18880/', 'web-lmorchard-abc')).toBe(
      'http://decafclaw:18880/?conv=web-lmorchard-abc'
    );
  });

  it('falls back to the web UI root without a conversation', () => {
    expect(conversationWebUrl('http://decafclaw:18880', null)).toBe(
      'http://decafclaw:18880/'
    );
  });

  it('encodes the conversation id', () => {
    expect(conversationWebUrl('https://example.com/base', 'a/b c')).toBe(
      'https://example.com/base/?conv=a%2Fb%20c'
    );
  });
});
