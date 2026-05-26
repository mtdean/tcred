// Reusable sortable table. Column-driven, generic over row type.
// Sorting is client-side; pass `sortValue` for custom sort keys (e.g. raw numbers).

import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import EmptyState from './EmptyState';

export interface Column<T> {
  /** Stable key, also used as the default sort accessor. */
  key: string;
  header: ReactNode;
  /** Cell renderer. */
  render: (row: T) => ReactNode;
  /** Value used for sorting; defaults to row[key]. */
  sortValue?: (row: T) => string | number | null | undefined;
  align?: 'left' | 'right' | 'center';
  sortable?: boolean;
  width?: string | number;
}

interface Props<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  /** Initial sort column key. */
  initialSort?: string;
  initialDir?: 'asc' | 'desc';
  emptyMessage?: string;
  onRowClick?: (row: T) => void;
}

export default function DataTable<T>({
  columns,
  rows,
  rowKey,
  initialSort,
  initialDir = 'desc',
  emptyMessage,
  onRowClick,
}: Props<T>) {
  const [sortKey, setSortKey] = useState<string | undefined>(initialSort);
  const [dir, setDir] = useState<'asc' | 'desc'>(initialDir);

  const sorted = useMemo(() => {
    if (!sortKey) return rows;
    const col = columns.find((c) => c.key === sortKey);
    if (!col) return rows;
    const accessor =
      col.sortValue ?? ((row: T) => (row as Record<string, unknown>)[sortKey] as never);
    const factor = dir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = accessor(a);
      const bv = accessor(b);
      if (av == null && bv == null) return 0;
      if (av == null) return 1; // nulls always last
      if (bv == null) return -1;
      if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * factor;
      return String(av).localeCompare(String(bv)) * factor;
    });
  }, [rows, columns, sortKey, dir]);

  function toggleSort(col: Column<T>) {
    if (col.sortable === false) return;
    if (sortKey === col.key) {
      setDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(col.key);
      setDir('desc');
    }
  }

  if (rows.length === 0) {
    return <EmptyState message={emptyMessage} />;
  }

  return (
    <table className="data-table">
      <thead>
        <tr>
          {columns.map((col) => {
            const sortable = col.sortable !== false;
            return (
              <th
                key={col.key}
                className={sortable ? 'sortable' : undefined}
                style={{ textAlign: col.align ?? 'left', width: col.width }}
                onClick={() => toggleSort(col)}
              >
                {col.header}
                {sortKey === col.key && (
                  <span className="sort-ind">{dir === 'asc' ? '▲' : '▼'}</span>
                )}
              </th>
            );
          })}
        </tr>
      </thead>
      <tbody>
        {sorted.map((row) => (
          <tr
            key={rowKey(row)}
            onClick={onRowClick ? () => onRowClick(row) : undefined}
            style={onRowClick ? { cursor: 'pointer' } : undefined}
          >
            {columns.map((col) => (
              <td key={col.key} style={{ textAlign: col.align ?? 'left' }}>
                {col.render(row)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
