// Reusable panel wrapper: ALL-CAPS accent header, optional subtitle/actions, sharp borders.

import type { CSSProperties, ReactNode } from 'react';

interface Props {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
  bodyStyle?: CSSProperties;
}

export default function Panel({
  title,
  subtitle,
  actions,
  children,
  className,
  style,
  bodyStyle,
}: Props) {
  return (
    <section className={`panel${className ? ` ${className}` : ''}`} style={style}>
      <header className="panel-header">
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
          <span className="panel-title">{title}</span>
          {subtitle != null && <span className="panel-subtitle">{subtitle}</span>}
        </div>
        {actions != null && <div className="panel-actions">{actions}</div>}
      </header>
      <div className="panel-body" style={bodyStyle}>
        {children}
      </div>
    </section>
  );
}
