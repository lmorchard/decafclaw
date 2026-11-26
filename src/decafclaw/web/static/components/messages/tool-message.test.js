import { it, expect, vi } from 'vitest';
import './tool-message.js';

const { copyToClipboard } = vi.hoisted(() => ({ copyToClipboard: vi.fn().mockResolvedValue(undefined) }));
vi.mock('../../lib/utils.js', () => ({ copyToClipboard }));

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

it('renders purpose and approval details when present', async () => {
    const el = document.createElement('tool-message');
    el.tool = 'shell';
    el.command = 'git status';
    el.content = 'clean';
    el.details = {
        purpose: 'check <tree>',
        approval: { decision: 'allow', tier: 'read_only', reason: 'read-only command' },
    };
    document.body.append(el);
    await el.updateComplete;
    el.querySelector('.tool-result-header').click();
    await el.updateComplete;

    const details = el.querySelector('.tool-details');
    expect(details).not.toBeNull();
    expect(details.textContent).toContain('check <tree>');
    expect(details.textContent).toContain('allow');
    expect(details.textContent).toContain('read_only');
    expect(details.textContent).toContain('read-only command');
    expect(el.querySelector('tree')).toBeNull();
    el.remove();
});

it('keeps the row compact when no details are present', async () => {
    const el = document.createElement('tool-message');
    el.tool = 'web_fetch';
    el.content = 'ok';
    document.body.append(el);
    await el.updateComplete;
    el.querySelector('.tool-result-header').click();
    await el.updateComplete;

    expect(el.querySelector('.tool-details')).toBeNull();
    el.remove();
});

it('renders workspace and vault links in the expanded row', async () => {
    const el = document.createElement('tool-message');
    el.tool = 'workspace_write';
    el.content = 'Wrote 5 characters to notes.md';
    el.links = [
        { kind: 'workspace', path: 'notes.md', label: 'notes.md' },
        { kind: 'vault', page: 'agent/pages/Notes', label: 'Notes' },
    ];
    document.body.append(el);
    await el.updateComplete;
    el.querySelector('.tool-result-header').click();
    await el.updateComplete;

    const workspace = el.querySelector('a.workspace-link');
    expect(workspace.getAttribute('data-workspace-path')).toBe('notes.md');
    expect(workspace.getAttribute('href')).toContain('notes.md');
    const wiki = el.querySelector('a.wiki-link');
    expect(wiki.getAttribute('data-wiki-page')).toBe('agent/pages/Notes');
    expect(wiki.getAttribute('href')).toBe('/vault/agent/pages/Notes');
    el.remove();
});

it('copies the raw result from the expanded row', async () => {
    const el = document.createElement('tool-message');
    el.tool = 'workspace_preview_markdown';
    el.content = '# Brief\ncopy me';
    document.body.append(el);
    await el.updateComplete;
    el.querySelector('.tool-result-header').click();
    await el.updateComplete;

    const btn = el.querySelector('.tool-result-detail .copy-btn');
    expect(btn).not.toBeNull();
    btn.click();
    await vi.waitFor(() => expect(copyToClipboard).toHaveBeenCalledWith('# Brief\ncopy me'));
    el.remove();
});

it('copies the raw result from a widget raw pane', async () => {
    const el = document.createElement('tool-message');
    el.tool = 'workspace_preview_markdown';
    el.content = 'raw prompt body';
    el.widget = { widget_type: 'unknown_widget', data: {} };
    document.body.append(el);
    await el.updateComplete;

    const btn = el.querySelector('.tool-result-raw .copy-btn');
    expect(btn).not.toBeNull();
    btn.click();
    await vi.waitFor(() => expect(copyToClipboard).toHaveBeenCalledWith('raw prompt body'));
    el.remove();
});
