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
