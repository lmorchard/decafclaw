import { describe, it, expect } from 'vitest';

describe('vitest wiring', () => {
  it('runs a test', () => {
    expect(1 + 1).toBe(2);
  });
  it('has a jsdom document', () => {
    expect(typeof document).toBe('object');
    expect(document.documentElement.tagName).toBe('HTML');
  });
});
