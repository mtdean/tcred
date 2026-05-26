// Feed health table in a modal dialog. Columns: feed, platform, status, checked, error.

import * as Dialog from '@radix-ui/react-dialog';
import { useQuery } from '@tanstack/react-query';
import { X } from 'lucide-react';
import { getFeedHealth } from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import { fmtDateTime } from '../../lib/utils';
import type { FeedHealth } from '../../lib/types';
import DataTable from '../shared/DataTable';
import type { Column } from '../shared/DataTable';
import LoadingCursor from '../shared/LoadingCursor';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function statusRank(f: FeedHealth): number {
  if (f.needs_url) return 1;
  return f.is_live ? 2 : 0; // dead first, then needs-url, then live (asc)
}

function StatusBadge({ f }: { f: FeedHealth }) {
  if (f.needs_url) return <span style={{ color: 'var(--warning)' }}>[NEEDS URL]</span>;
  if (f.is_live) return <span style={{ color: 'var(--positive)' }}>[LIVE]</span>;
  return <span style={{ color: 'var(--negative)' }}>[DEAD]</span>;
}

const columns: Column<FeedHealth>[] = [
  { key: 'feed_name', header: 'Feed', render: (f) => f.feed_name },
  {
    key: 'platform',
    header: 'Platform',
    render: (f) => <span className="muted">{f.platform ?? '—'}</span>,
  },
  {
    key: 'status',
    header: 'Status',
    sortValue: statusRank,
    render: (f) => <StatusBadge f={f} />,
  },
  {
    key: 'last_checked',
    header: 'Checked',
    render: (f) => <span className="dim">{fmtDateTime(f.last_checked)}</span>,
  },
  {
    key: 'error_msg',
    header: 'Error',
    sortable: false,
    render: (f) => <span className="muted" style={{ fontSize: 11 }}>{f.error_msg ?? ''}</span>,
  },
];

export default function FeedHealthModal({ open, onOpenChange }: Props) {
  const { data, isLoading } = useQuery({
    queryKey: qk.feedHealth,
    queryFn: () => getFeedHealth().then((r) => r.data),
    enabled: open,
  });

  const live = data?.filter((f) => f.is_live).length ?? 0;
  const total = data?.length ?? 0;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 50 }}
        />
        <Dialog.Content
          aria-describedby={undefined}
          style={{
            position: 'fixed',
            top: '50%',
            left: '50%',
            transform: 'translate(-50%, -50%)',
            width: 'min(900px, 92vw)',
            maxHeight: '82vh',
            overflow: 'auto',
            background: 'var(--bg-panel)',
            border: '1px solid var(--border-bright)',
            zIndex: 51,
          }}
        >
          <div
            className="panel-header"
            style={{ position: 'sticky', top: 0, zIndex: 1 }}
          >
            <span className="panel-title">
              Feed Health{total > 0 ? ` — ${live}/${total} LIVE` : ''}
            </span>
            <Dialog.Close asChild>
              <button className="btn" style={{ padding: '2px 6px' }} aria-label="Close">
                <X size={12} />
              </button>
            </Dialog.Close>
          </div>
          <Dialog.Title style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>
            Feed Health
          </Dialog.Title>
          <div style={{ padding: 10 }}>
            {isLoading || !data ? (
              <LoadingCursor />
            ) : (
              <DataTable
                columns={columns}
                rows={data}
                rowKey={(f) => f.feed_name}
                initialSort="status"
                initialDir="asc"
              />
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
