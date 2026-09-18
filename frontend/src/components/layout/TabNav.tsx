import { NavLink } from 'react-router-dom';
import { STATIC_MODE_ENABLED } from '../../lib/staticMode';

const TABS: { to: string; label: string }[] = [
  { to: '/', label: 'Home' },
  { to: '/news', label: 'News' },
  { to: '/markets', label: 'Markets' },
  { to: '/macro', label: 'Macro' },
  { to: '/forecasts', label: 'Forecasts' },
  { to: '/private-credit', label: 'Private Credit' },
  { to: '/regulatory', label: 'Regulatory' },
  { to: '/analyst', label: 'Analyst' },
  { to: '/watchlists', label: 'Watchlists' },
  // Lookup needs a live backend to search — hide it on static snapshots.
  ...(STATIC_MODE_ENABLED ? [] : [{ to: '/lookup', label: 'Lookup' }]),
];

export default function TabNav() {
  return (
    <nav className="tabnav">
      {TABS.map((t) => (
        <NavLink
          key={t.to}
          to={t.to}
          end={t.to === '/'}
          className={({ isActive }) => (isActive ? 'active' : undefined)}
        >
          {t.label}
        </NavLink>
      ))}
    </nav>
  );
}
