import { it, expect } from 'vitest';
import './tool-message.js';

it('shows a shell command and its output together when expanded', async () => {
    const el = document.createElement('tool-message');
    el.tool = 'shell';
    el.command = 'echo <hello>';
    el.content = '<hello>';
    document.body.append(el);
    await el.updateComplete;
    el.querySelector('.tool-result-header').click();
    await el.updateComplete;
    expect(el.textContent).toContain('echo <hello>');
    expect(el.querySelector('.tool-result-detail').textContent).toContain('<hello>');
    expect(el.querySelector('hello')).toBeNull();
    el.remove();
});

it('marks a timed-out tool loudly and auto-expands the error', async () => {
    const el = document.createElement('tool-message');
    el.tool = 'slow_tool';
    el.content = '[error: tool slow_tool timed out after 180s]';
    el.isError = true;
    el.timedOut = true;
    el.timeoutSec = 180;
    document.body.append(el);
    await el.updateComplete;

    expect(el.querySelector('.message.tool').classList.contains('tool-timeout')).toBe(true);
    expect(el.querySelector('.tool-badge').textContent).toBe('Timed Out (180s)');
    expect(el.querySelector('.tool-result-detail').textContent).toContain('timed out after 180s');
    el.remove();
});

it('marks a failed tool and auto-expands the error text', async () => {
    const el = document.createElement('tool-message');
    el.tool = 'web_search';
    el.content = '[error: search backend unavailable]';
    el.isError = true;
    document.body.append(el);
    await el.updateComplete;

    expect(el.querySelector('.message.tool').classList.contains('tool-error')).toBe(true);
    expect(el.querySelector('.tool-badge').textContent).toBe('Error');
    expect(el.querySelector('.tool-result-detail').textContent).toContain('search backend unavailable');
    el.remove();
});
