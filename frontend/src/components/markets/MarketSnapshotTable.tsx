// Market snapshot: price + 1d/5d/30d % + 90d sparkline, grouped by category.

import { Fragment } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getMarketHistory } from '../../lib/api';
import { qk } from '../../lib/queryKeys';
import type { MarketRow } from '../../lib/types';
import { num, pct, signClass } from '../../lib/utils';
import Sparkline from '../shared/Sparkline';

const CATEGORY_LABEL: Record<string, string> = {
  credit_etf: 'CREDIT ETFs',
  structured_etf: 'STRUCTURED / MBS',
  macro: 'MACRO / EQUITY',
  rates: 'RATES',
  consumer_credit_proxy: 'CONSUMER CREDIT PROXIES',
};

function SparklineCell({ ticker }: { ticker: string }) {
  const { data } = useQuery({
    queryKey: qk.marketHistory(ticker),
    queryFn: () => getMarketHistory(ticker, 90).then((r) => r.data),
    staleTime: 5 * 60_000,
  });
  return <Sparkline data={data ?? []} />;
}

function Row({ r }: { r: MarketRow }) {
  return (
    <tr>
      <td style={{ color: 'var(--text-accent)' }}>{r.ticker}</td>
      <td className="muted">{r.label}</td>
      <td className="num">{num(r.price)}</td>
      <td className="num"><span className={signClass(r.chg_1d)}>{pct(r.chg_1d)}</span></td>
      <td className="num"><span className={signClass(r.chg_5d)}>{pct(r.chg_5d)}</span></td>
      <td className="num"><span className={signClass(r.chg_30d)}>{pct(r.chg_30d)}</span></td>
      <td><SparklineCell ticker={r.ticker} /></td>
    </tr>
  );
}

function GroupHeader({ cat }: { cat: string }) {
  return (
    <tr>
      <td
        colSpan={7}
        style={{
          color: 'var(--text-secondary)',
          background: 'var(--bg-panel-alt)',
          fontSize: 10,
          letterSpacing: '0.1em',
          textTransform: 'uppercase',
          borderBottom: '1px solid var(--border-bright)',
        }}
      >
        {CATEGORY_LABEL[cat] ?? cat}
      </td>
    </tr>
  );
}

interface Props {
  rows: MarketRow[];
  grouped?: boolean;
}

export default function MarketSnapshotTable({ rows, grouped = true }: Props) {
  const groups = grouped ? [...new Set(rows.map((r) => r.category))] : null;

  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Ticker</th>
          <th>Name</th>
          <th style={{ textAlign: 'right' }}>Price</th>
          <th style={{ textAlign: 'right' }}>1D</th>
          <th style={{ textAlign: 'right' }}>5D</th>
          <th style={{ textAlign: 'right' }}>30D</th>
          <th>90D</th>
        </tr>
      </thead>
      <tbody>
        {groups
          ? groups.map((cat) => (
              <Fragment key={cat}>
                <GroupHeader cat={cat} />
                {rows.filter((r) => r.category === cat).map((r) => <Row key={r.ticker} r={r} />)}
              </Fragment>
            ))
          : rows.map((r) => <Row key={r.ticker} r={r} />)}
      </tbody>
    </table>
  );
}
