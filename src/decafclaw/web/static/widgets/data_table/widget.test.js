import { describe, expect, it } from 'vitest';

import { DataTableWidget } from './widget.js';

function makeWidget(data) {
  const el = new DataTableWidget();
  el.data = data;
  return el;
}

describe('DataTableWidget typed sorting/formatting (#1169)', () => {
  it('sorts a bytes column numerically ascending', () => {
    const el = makeWidget({
      columns: [
        { key: 'name', label: 'Name' },
        { key: 'size', label: 'Size (bytes)', type: 'bytes' },
      ],
      rows: [
        { name: 'c', size: 1048576 },
        { name: 'a', size: 9 },
        { name: 'b', size: 20 },
      ],
    });
    el._sortKey = 'size';
    el._sortDir = 'asc';
    const sorted = el._sortedRows(el.data.rows);
    expect(sorted.map((r) => r.size)).toEqual([9, 20, 1048576]);
  });

  it('sorts a duration column numerically ascending', () => {
    const el = makeWidget({
      columns: [
        { key: 'schedule_id', label: 'Schedule ID' },
        { key: 'remaining', label: 'Remaining (s)', type: 'duration' },
      ],
      rows: [
        { schedule_id: 's2', remaining: 120 },
        { schedule_id: 's1', remaining: 5 },
        { schedule_id: 's3', remaining: 30 },
      ],
    });
    el._sortKey = 'remaining';
    el._sortDir = 'asc';
    const sorted = el._sortedRows(el.data.rows);
    expect(sorted.map((r) => r.remaining)).toEqual([5, 30, 120]);
  });

  it('sorts formatted string values in a typed column numerically', () => {
    const el = makeWidget({
      columns: [{ key: 'size', label: 'Size', type: 'bytes' }],
      rows: [
        { size: '100 B' },
        { size: '20 B' },
        { size: '1.2 KB' },
      ],
    });
    el._sortKey = 'size';
    el._sortDir = 'asc';
    const sorted = el._sortedRows(el.data.rows);
    expect(sorted.map((r) => r.size)).toEqual(['20 B', '100 B', '1.2 KB']);
  });

  it('sorts a duration column formatted as "120s" numerically', () => {
    const el = makeWidget({
      columns: [{ key: 'remaining', label: 'Remaining', type: 'duration' }],
      rows: [
        { remaining: '120s' },
        { remaining: '5s' },
      ],
    });
    el._sortKey = 'remaining';
    el._sortDir = 'asc';
    const sorted = el._sortedRows(el.data.rows);
    expect(sorted.map((r) => r.remaining)).toEqual(['5s', '120s']);
  });

  it('sorts decoupled typed cells on the raw value', () => {
    const el = makeWidget({
      columns: [{ key: 'size', label: 'Size', type: 'bytes' }],
      rows: [
        { size: { value: 1048576, display: '1.0 MB' } },
        { size: { value: 9, display: '9 B' } },
      ],
    });
    el._sortKey = 'size';
    el._sortDir = 'asc';
    const sorted = el._sortedRows(el.data.rows);
    expect(sorted[0].size.value).toBe(9);
    expect(sorted[1].size.value).toBe(1048576);
  });

  it('formats bytes display for typed columns', () => {
    const el = makeWidget({ columns: [{ key: 'size', label: 'Size', type: 'bytes' }], rows: [{ size: 1048576 }] });
    el._sortKey = 'size';
    el._sortDir = 'asc';
    expect(el._renderCell(1048576, { key: 'size', label: 'Size', type: 'bytes' })).toBe('1.00 MB');
  });

  it('formats duration display', () => {
    const el = makeWidget({ columns: [{ key: 'remaining', label: 'Remaining', type: 'duration' }], rows: [{ remaining: 120 }] });
    el._sortKey = 'remaining';
    el._sortDir = 'asc';
    expect(el._renderCell(120, { key: 'remaining', label: 'Remaining', type: 'duration' })).toBe('2m 0s');
  });

  it('text columns keep lexicographic sort', () => {
    const el = makeWidget({
      columns: [{ key: 'name', label: 'Name' }],
      rows: [{ name: 'b' }, { name: 'a' }],
    });
    el._sortKey = 'name';
    el._sortDir = 'asc';
    const sorted = el._sortedRows(el.data.rows);
    expect(sorted.map((r) => r.name)).toEqual(['a', 'b']);
  });
});
