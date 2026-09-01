// Watchlists: persisted saved searches across news + EDGAR + regulatory.

import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus, Save, ShieldCheck, Trash2, Eye, X } from 'lucide-react';
import {
  createWatchlist,
  deleteWatchlist,
  getWatchlistResults,
  listWatchlists,
  markWatchlistViewed,
  updateWatchlist,
  verifyWatchlist,
  type Watchlist,
  type WatchlistArticleMatch,
  type WatchlistCreate,
} from '../lib/api';
import { qk } from '../lib/queryKeys';
import { staticDisabledProps } from '../lib/staticMode';
import { fmtDateTime, fmtRelative } from '../lib/utils';
import Panel from '../components/shared/Panel';
import LoadingCursor from '../components/shared/LoadingCursor';
import ScoreDots from '../components/shared/ScoreDots';

// Categories we already filter elsewhere; kept here as a hint list.
const NEWS_CATEGORIES = [
  'macro', 'credit', 'structured_finance', 'fintech', 'regulation', 'data_science',
];

const REG_AGENCIES = ['CFPB', 'OCC', 'FDIC', 'Fed', 'SEC'];

export default function WatchlistsPage() {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editing, setEditing] = useState<Watchlist | null>(null);
  const [creating, setCreating] = useState(false);

  const { data: lists } = useQuery({
    queryKey: qk.watchlists,
    queryFn: () => listWatchlists().then((r) => r.data.items),
  });

  // Default selection: the most recently updated.
  useEffect(() => {
    if (!selectedId && lists && lists.length > 0) setSelectedId(lists[0].id);
  }, [lists, selectedId]);

  // Bump last_viewed_at the first time a user opens a list.
  const viewed = useMutation({
    mutationFn: markWatchlistViewed,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: qk.watchlists }),
  });
  useEffect(() => {
    if (selectedId) viewed.mutate(selectedId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  return (
    <div className="sidebar-grid sidebar-grid--wide stack">
      <WatchlistSidebar
        lists={lists ?? []}
        selectedId={selectedId}
        onSelect={setSelectedId}
        onCreate={() => {
          setEditing(null);
          setCreating(true);
        }}
        onEdit={(w) => {
          setCreating(false);
          setEditing(w);
        }}
      />

      <div className="stack">
        {creating && (
          <WatchlistForm
            mode="create"
            onCancel={() => setCreating(false)}
            onSubmitted={(w) => {
              setCreating(false);
              setSelectedId(w.id);
            }}
          />
        )}
        {editing && (
          <WatchlistForm
            mode="edit"
            initial={editing}
            onCancel={() => setEditing(null)}
            onSubmitted={() => setEditing(null)}
          />
        )}

        {selectedId ? (
          <WatchlistResultsPanel watchlistId={selectedId} />
        ) : (
          <Panel title="Watchlists" subtitle="NONE SELECTED">
            <div className="muted" style={{ fontSize: 12 }}>
              {lists?.length
                ? 'Select a watchlist on the left.'
                : 'Create a watchlist to track keywords across news + EDGAR + regulatory.'}
            </div>
          </Panel>
        )}
      </div>
    </div>
  );
}

// ─── Sidebar ────────────────────────────────────────────────────────────────
function WatchlistSidebar({
  lists,
  selectedId,
  onSelect,
  onCreate,
  onEdit,
}: {
  lists: Watchlist[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onEdit: (w: Watchlist) => void;
}) {
  return (
    <Panel
      title="Watchlists"
      subtitle={`${lists.length} SAVED`}
      actions={
        <button
          className="btn"
          onClick={onCreate}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          {...staticDisabledProps()}
        >
          <Plus size={12} /> NEW
        </button>
      }
      bodyStyle={{ padding: 0 }}
    >
      <div style={{ display: 'flex', flexDirection: 'column' }}>
        {lists.length === 0 && (
          <div className="muted" style={{ padding: 12, fontSize: 12 }}>
            No watchlists yet.
          </div>
        )}
        {lists.map((w) => {
          const active = w.id === selectedId;
          return (
            <div
              key={w.id}
              style={{
                display: 'flex',
                flexDirection: 'column',
                borderBottom: '1px solid var(--border)',
                background: active ? 'var(--bg-panel-alt)' : 'transparent',
              }}
            >
              <button
                onClick={() => onSelect(w.id)}
                style={{
                  textAlign: 'left',
                  padding: '8px 12px',
                  border: 'none',
                  background: 'transparent',
                  color: 'var(--text-primary)',
                  cursor: 'pointer',
                  fontFamily: 'inherit',
                }}
              >
                <div style={{ fontSize: 11, fontWeight: 'bold' }}>{w.name}</div>
                <div className="muted" style={{ fontSize: 10, marginTop: 2 }}>
                  {w.keywords.slice(0, 3).join(' · ')}
                  {w.keywords.length > 3 && '…'}
                </div>
                <div className="dim" style={{ fontSize: 10, marginTop: 2 }}>
                  Updated {fmtRelative(w.updated_at)}
                </div>
              </button>
              {active && (
                <button
                  onClick={() => onEdit(w)}
                  className="btn"
                  style={{
                    margin: '0 12px 8px',
                    fontSize: 9,
                    padding: '2px 6px',
                  }}
                  {...staticDisabledProps()}
                >
                  EDIT
                </button>
              )}
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

// ─── Create / edit form ─────────────────────────────────────────────────────
type FormMode = 'create' | 'edit';

function WatchlistForm({
  mode,
  initial,
  onCancel,
  onSubmitted,
}: {
  mode: FormMode;
  initial?: Watchlist;
  onCancel: () => void;
  onSubmitted: (w: Watchlist) => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(initial?.name ?? '');
  const [description, setDescription] = useState(initial?.description ?? '');
  const [keywordsText, setKeywordsText] = useState(
    (initial?.keywords ?? []).join(', '),
  );
  const [newsCats, setNewsCats] = useState<string[]>(initial?.news_categories ?? []);
  const [edgarClasses, setEdgarClasses] = useState(
    (initial?.edgar_asset_classes ?? []).join(', '),
  );
  const [edgarForms, setEdgarForms] = useState(
    (initial?.edgar_form_types ?? []).join(', '),
  );
  const [agencies, setAgencies] = useState<string[]>(initial?.regulatory_agencies ?? []);
  const [minScore, setMinScore] = useState(initial?.min_score ?? 3);

  const parseList = (s: string) =>
    s.split(',').map((t) => t.trim()).filter(Boolean);

  const toggle = (arr: string[], v: string, set: (a: string[]) => void) => {
    set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  };

  const save = useMutation({
    mutationFn: async () => {
      const payload: WatchlistCreate = {
        name: name.trim(),
        description: description.trim() || null,
        keywords: parseList(keywordsText),
        news_categories: newsCats.length ? newsCats : null,
        edgar_asset_classes: parseList(edgarClasses).length
          ? parseList(edgarClasses)
          : null,
        edgar_form_types: parseList(edgarForms).length
          ? parseList(edgarForms)
          : null,
        regulatory_agencies: agencies.length ? agencies : null,
        min_score: minScore,
      };
      if (mode === 'create') {
        return (await createWatchlist(payload)).data;
      }
      return (await updateWatchlist(initial!.id, payload)).data;
    },
    onSuccess: (w) => {
      queryClient.invalidateQueries({ queryKey: qk.watchlists });
      if (initial) {
        queryClient.invalidateQueries({ queryKey: qk.watchlistResults(initial.id) });
      }
      onSubmitted(w);
    },
  });

  const remove = useMutation({
    mutationFn: () => deleteWatchlist(initial!.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: qk.watchlists });
      onCancel();
    },
  });

  const disabled = !name.trim() || !parseList(keywordsText).length || save.isPending;

  return (
    <Panel
      title={mode === 'create' ? 'New Watchlist' : `Edit · ${initial?.name}`}
      actions={
        <button
          className="btn"
          onClick={onCancel}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
        >
          <X size={12} /> CLOSE
        </button>
      }
    >
      <div style={{ display: 'grid', gap: 10, fontSize: 12 }}>
        <FieldLabel label="Name">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            style={inputStyle}
            placeholder="e.g. Subprime Auto"
          />
        </FieldLabel>

        <FieldLabel label="Description (optional)">
          <input
            type="text"
            value={description ?? ''}
            onChange={(e) => setDescription(e.target.value)}
            style={inputStyle}
          />
        </FieldLabel>

        <FieldLabel label="Keywords (comma-separated; OR match, case-insensitive)">
          <input
            type="text"
            value={keywordsText}
            onChange={(e) => setKeywordsText(e.target.value)}
            style={inputStyle}
            placeholder="Carvana, Exeter, subprime auto"
          />
        </FieldLabel>

        <FieldLabel label="Min news relevance score">
          <select
            value={minScore}
            onChange={(e) => setMinScore(parseInt(e.target.value, 10))}
            style={inputStyle}
          >
            {[1, 2, 3, 4, 5].map((s) => (
              <option key={s} value={s}>{s}+</option>
            ))}
          </select>
        </FieldLabel>

        <FieldLabel label="News categories (optional — narrows to these)">
          <ChipPicker
            options={NEWS_CATEGORIES}
            selected={newsCats}
            onToggle={(v) => toggle(newsCats, v, setNewsCats)}
          />
        </FieldLabel>

        <FieldLabel label="EDGAR asset classes (optional, comma-separated)">
          <input
            type="text"
            value={edgarClasses}
            onChange={(e) => setEdgarClasses(e.target.value)}
            style={inputStyle}
            placeholder="auto loan, credit card, CLO"
          />
        </FieldLabel>

        <FieldLabel label="EDGAR form types (optional)">
          <input
            type="text"
            value={edgarForms}
            onChange={(e) => setEdgarForms(e.target.value)}
            style={inputStyle}
            placeholder="424B5, ABS-15G, ABS-EE"
          />
        </FieldLabel>

        <FieldLabel label="Regulatory agencies (optional)">
          <ChipPicker
            options={REG_AGENCIES}
            selected={agencies}
            onToggle={(v) => toggle(agencies, v, setAgencies)}
          />
        </FieldLabel>

        {save.isError && (
          <div className="muted" style={{ color: 'var(--warning)' }}>
            ⚠ {String(save.error)}
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
          <button
            className="btn"
            onClick={() => save.mutate()}
            disabled={disabled}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
            {...staticDisabledProps()}
          >
            <Save size={12} />
            {save.isPending ? 'SAVING' : mode === 'create' ? 'CREATE' : 'SAVE'}
          </button>
          {mode === 'edit' && (
            <button
              className="btn"
              onClick={() => {
                if (confirm(`Delete watchlist "${initial!.name}"?`)) remove.mutate();
              }}
              disabled={remove.isPending}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                color: 'var(--warning)',
              }}
              {...staticDisabledProps()}
            >
              <Trash2 size={12} />
              {remove.isPending ? 'DELETING' : 'DELETE'}
            </button>
          )}
        </div>
      </div>
    </Panel>
  );
}

const inputStyle: React.CSSProperties = {
  width: '100%',
  background: 'var(--bg-panel-alt)',
  color: 'var(--text-primary)',
  border: '1px solid var(--border-bright)',
  padding: '5px 8px',
  fontSize: 12,
  fontFamily: 'inherit',
  borderRadius: 2,
};

function FieldLabel({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'block' }}>
      <span
        className="muted"
        style={{ display: 'block', fontSize: 10, letterSpacing: 1, marginBottom: 3 }}
      >
        {label}
      </span>
      {children}
    </label>
  );
}

function ChipPicker({
  options,
  selected,
  onToggle,
}: {
  options: string[];
  selected: string[];
  onToggle: (v: string) => void;
}) {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
      {options.map((o) => {
        const active = selected.includes(o);
        return (
          <button
            key={o}
            type="button"
            onClick={() => onToggle(o)}
            className="cat-chip"
            style={{
              cursor: 'pointer',
              border: `1px solid ${active ? 'var(--text-primary)' : 'var(--border)'}`,
              color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
              background: active ? 'var(--bg-panel-alt)' : 'transparent',
              padding: '1px 6px',
              fontSize: 10,
            }}
          >
            {o}
          </button>
        );
      })}
    </div>
  );
}

// ─── Results panel ──────────────────────────────────────────────────────────

function TierBadge({ a }: { a: WatchlistArticleMatch }) {
  if (a.publisher_tier === 'junk') {
    return (
      <span
        className="mono"
        title="Press-release wire / stock-promo aggregator (publisher_tiers in data_sources.yaml)"
        style={{ color: 'var(--warning)', fontSize: 9, marginLeft: 6 }}
      >
        [LOW-CRED]
      </span>
    );
  }
  return null;
}

function VerificationBadge({ a }: { a: WatchlistArticleMatch }) {
  const v = a.verification;
  if (!v) return null;
  const reject = v.verdict === 'reject';
  return (
    <span
      className="mono"
      title={v.reason ?? undefined}
      style={{
        color: reject ? 'var(--negative)' : 'var(--positive)',
        fontSize: 9,
        marginLeft: 6,
      }}
    >
      {reject ? '✗ OFF-TOPIC' : '✓ VERIFIED'}
    </span>
  );
}

function WatchlistResultsPanel({ watchlistId }: { watchlistId: string }) {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: qk.watchlistResults(watchlistId),
    queryFn: () => getWatchlistResults(watchlistId).then((r) => r.data),
  });

  const verify = useMutation({
    mutationFn: () => verifyWatchlist(watchlistId),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: qk.watchlistResults(watchlistId) }),
  });

  if (isLoading || !data) {
    return (
      <Panel title="Matches">
        <LoadingCursor />
      </Panel>
    );
  }

  const { watchlist: w, matches, counts } = data;
  const subtitle = `${counts.total} MATCHES · ${counts.articles} NEWS · ${counts.edgar_filings} EDGAR · ${counts.regulatory_actions} REG`;

  return (
    <div className="stack">
      <Panel title={w.name} subtitle={subtitle}>
        <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>
          {w.description && <div>{w.description}</div>}
          <div style={{ marginTop: 4 }}>
            KEYWORDS:{' '}
            {w.keywords.map((k) => (
              <span key={k} className="cat-chip" style={{ marginRight: 4 }}>
                {k}
              </span>
            ))}
          </div>
          {w.last_viewed_at && (
            <div className="dim" style={{ marginTop: 4, fontSize: 10 }}>
              <Eye size={9} style={{ display: 'inline', verticalAlign: 'middle' }} />{' '}
              Last viewed {fmtRelative(w.last_viewed_at)}
            </div>
          )}
        </div>
      </Panel>

      {matches.articles.length > 0 && (
        <Panel
          title="News"
          subtitle={`${matches.articles.length} MATCHES · TRUSTED SOURCES FIRST`}
          actions={
            <button
              className="btn"
              onClick={() => verify.mutate()}
              disabled={verify.isPending}
              title="Claude checks each match is genuinely about this watchlist's subject (cached per article)"
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
              {...staticDisabledProps()}
            >
              <ShieldCheck size={12} />
              {verify.isPending ? 'VERIFYING' : 'VERIFY'}
            </button>
          }
        >
          {verify.isError && (
            <div className="muted" style={{ color: 'var(--warning)', fontSize: 11, marginBottom: 6 }}>
              ⚠ Verification failed: {String(verify.error)}
            </div>
          )}
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: 60 }}>Score</th>
                <th>Title</th>
                <th style={{ width: 150 }}>Source</th>
                <th style={{ width: 140 }}>Published</th>
              </tr>
            </thead>
            <tbody>
              {matches.articles.map((a) => {
                const rejected = a.verification?.verdict === 'reject';
                return (
                  <tr key={a.id} style={rejected ? { opacity: 0.45 } : undefined}>
                    <td><ScoreDots score={a.relevance_score} /></td>
                    <td>
                      <a
                        href={a.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={rejected ? { textDecoration: 'line-through' } : undefined}
                      >
                        {a.title}
                      </a>
                      <TierBadge a={a} />
                      <VerificationBadge a={a} />
                    </td>
                    <td className="muted">{a.publisher ?? a.feed_name}</td>
                    <td className="num dim">
                      {fmtDateTime(a.published_at ?? a.fetched_at)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Panel>
      )}

      {matches.edgar_filings.length > 0 && (
        <Panel title="EDGAR Filings" subtitle={`${matches.edgar_filings.length} MATCHES`}>
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: 90 }}>Form</th>
                <th>Issuer</th>
                <th style={{ width: 140 }}>Asset Class</th>
                <th style={{ width: 110 }}>Filed</th>
              </tr>
            </thead>
            <tbody>
              {matches.edgar_filings.map((f) => (
                <tr key={f.accession_no}>
                  <td className="mono">{f.form_type}</td>
                  <td>
                    {f.url ? (
                      <a href={f.url} target="_blank" rel="noopener noreferrer">
                        {f.company_name}
                      </a>
                    ) : (
                      f.company_name
                    )}
                  </td>
                  <td className="muted">{f.asset_class}</td>
                  <td className="num dim">{f.filed_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}

      {matches.regulatory_actions.length > 0 && (
        <Panel
          title="Regulatory Actions"
          subtitle={`${matches.regulatory_actions.length} MATCHES`}
        >
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: 70 }}>Agency</th>
                <th>Title</th>
                <th style={{ width: 110 }}>Published</th>
              </tr>
            </thead>
            <tbody>
              {matches.regulatory_actions.map((r) => (
                <tr key={r.id}>
                  <td className="mono">{r.agency}</td>
                  <td>
                    {r.html_url ? (
                      <a href={r.html_url} target="_blank" rel="noopener noreferrer">
                        {r.title}
                      </a>
                    ) : (
                      r.title
                    )}
                  </td>
                  <td className="num dim">{r.publication_date}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}

      {counts.total === 0 && (
        <Panel title="Matches">
          <div className="muted" style={{ fontSize: 12 }}>
            No matches yet for these keywords + filters.
          </div>
        </Panel>
      )}
    </div>
  );
}
