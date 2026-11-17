import { describe, expect, it } from 'vitest';

import {
  CHILD_ID_SEPARATOR,
  STATUS_LABELS,
  STATUS_TITLE_LABELS,
  SIDE_EFFECT_COMMANDS,
  isSideEffectCommand,
} from './constants.js';

const COMMAND_VALUES = Object.values(SIDE_EFFECT_COMMANDS);

describe('web UI constants', () => {
  it('maps every conversation status to a badge label', () => {
    expect(STATUS_LABELS).toEqual({
      idle: 'Idle',
      busy: 'Running',
      waiting: 'Waiting',
      finished: 'Done',
    });
    expect(Object.isFrozen(STATUS_LABELS)).toBe(true);
  });

  it('maps busy/waiting/finished to their tooltip wording', () => {
    expect(STATUS_TITLE_LABELS).toEqual({
      busy: 'busy',
      waiting: 'waiting for answer',
      finished: 'finished',
    });
    expect(Object.isFrozen(STATUS_TITLE_LABELS)).toBe(true);
  });

  it('names each side-effect command exactly once', () => {
    expect(SIDE_EFFECT_COMMANDS).toEqual({
      TERMINAL: '/terminal',
      RETITLE: '/retitle',
      BANG_RETITLE: '!retitle',
      UPDATE: '/update',
      BANG_UPDATE: '!update',
    });
    expect(Object.isFrozen(SIDE_EFFECT_COMMANDS)).toBe(true);
    expect(new Set(COMMAND_VALUES).size).toBe(COMMAND_VALUES.length);
  });

  it('exposes the child-id separator', () => {
    expect(CHILD_ID_SEPARATOR).toBe('--child-');
  });

  it('matches side-effect commands only at the start, with optional arguments', () => {
    for (const command of COMMAND_VALUES) {
      expect(isSideEffectCommand(command)).toBe(true);
      expect(isSideEffectCommand(`  ${command} --flag value  `)).toBe(true);
    }
    expect(isSideEffectCommand('/terminalfoo')).toBe(false);
    expect(isSideEffectCommand('run /terminal')).toBe(false);
    expect(isSideEffectCommand('')).toBe(false);
  });
});
