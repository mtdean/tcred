// Consumer Credit Loss Forecast — macrobot `consumer_credit_cycle`.
//
// Credit-card charge-off path (logit hazard on SLOOS card tightening, slope,
// unemployment, saving rate) for consumer-ABS / lending exposure, with its
// backtest record vs persistence so the forecast's trustworthiness is visible.

import { useQuery } from '@tanstack/react-query';
import { getMacroViews } from '../../lib/api';
import { COLORS } from '../../lib/colors';
import Panel from '../shared/Panel';
import { CreditPathChart } from '../macro/CreditForecastsPanel';

const pct = (x: unknown) => (typeof x === 'number' ? `${x.toFixed(2)}%` : '—');

function Tile({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div style={{ border: `1px solid ${COLORS.border}`, padding: '8px 12px', flex: '1 1 170px',
      background: COLORS.bgPanel }}>
      <div className="muted" style={{ fontSize: 9, letterSpacing: '0.12em', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2, color: color ?? COLORS.textPrimary }}>{value}</div>
      {sub && <div className="muted" style={{ fontSize: 10, marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

export default function ConsumerCreditForecastPanel() {
  const { data } = useQuery({
    queryKey: ['macroViews'],
    queryFn: () => getMacroViews().then((r) => r.data),
    staleTime: 15 * 60_000,
  });
  const ev = data?.regime?.evidence ?? {};
  const concept = data?.concepts?.find((c) => c.key === 'consumer_loss_rate');
  if (!concept && ev.consumer_loss_pct == null) return null;
  const chg = ev.consumer_loss_4q_change_pct as number | undefined;
  const skill = data?.forecast_skill?.find((r) => r.concept === 'consumer_loss_rate');
  return (
    <div className="stack">
      <Panel title="Consumer Credit Loss Forecast"
        subtitle="credit-card charge-off rate, all banks · logit hazard on SLOOS card tightening, slope, unemployment, saving rate">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
          <Tile label="Card charge-offs now" value={pct(ev.consumer_loss_pct)} />
          <Tile label="4q-ahead forecast" value={pct(ev.consumer_loss_4q_fcst_pct)}
            sub={chg == null ? undefined : `${chg >= 0 ? '+' : ''}${(chg * 100).toFixed(0)}bp vs now`}
            color={chg == null ? undefined : chg > 0 ? COLORS.negative : COLORS.positive} />
          <Tile label="Backtest vs persistence"
            value={skill ? skill.verdict.toUpperCase() : '—'}
            sub={skill ? `RMSE ${skill.best_rmse?.toFixed(2)} vs ${skill.benchmark_rmse?.toFixed(2)} (4q, 24 origins)` : undefined}
            color={skill?.verdict === 'beats' ? COLORS.positive : skill?.verdict === 'loses' ? COLORS.negative : undefined} />
        </div>
      </Panel>
      <CreditPathChart concept={concept} color={COLORS.accent} decimals={2}
        unitLabel="% · card charge-off path · shaded = forecast band" />
    </div>
  );
}
