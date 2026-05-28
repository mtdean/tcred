import { NavLink } from 'react-router-dom';

const TABS: { to: string; label: string }[] = [
  { to: '/', label: 'Home' },
  { to: '/news', label: 'News' },
  { to: '/markets', label: 'Markets' },
  { to: '/macro', label: 'Macro' },
  { to: '/private-credit', label: 'Private Credit' },
  { to: '/regulatory', label: 'Regulatory' },
  { to: '/abs', label: 'ABS/EDGAR' },
  { to: '/deals', label: 'Deals' },
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
