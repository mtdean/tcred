// Analyst page: monthly macro/credit briefing + chat-with-briefing interface.
// Chat history is ephemeral (held in component state) — refresh = new session.

import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { Send, Sparkles, RefreshCw } from 'lucide-react';
import {
  chatWithBriefing,
  generateBriefing,
  getBriefing,
  getLatestBriefing,
  listBriefings,
  type Briefing,
  type BriefingChatMessage,
} from '../lib/api';
import { qk } from '../lib/queryKeys';
import { staticDisabledProps } from '../lib/staticMode';
import { fmtDateTime } from '../lib/utils';
import Panel from '../components/shared/Panel';
import LoadingCursor from '../components/shared/LoadingCursor';

function errMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (err.response?.status === 404) return 'No briefing yet — generate one first.';
    if (err.response?.status) return `Request failed (${err.response.status}).`;
  }
  return 'Something went wrong.';
}

const SEVERITY_COLOR: Record<string, string> = {
  warn: 'var(--warning)',
  watch: 'var(--neutral)',
  info: 'var(--text-secondary)',
};

export default function AnalystPage() {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const { data: list } = useQuery({
    queryKey: qk.briefings,
    queryFn: () => listBriefings(30).then((r) => r.data.items),
  });

  // Default to the most recent briefing in the list.
  useEffect(() => {
    if (!selectedId && list && list.length > 0) setSelectedId(list[0].id);
  }, [list, selectedId]);

  const briefingQuery = useQuery({
    queryKey: selectedId ? qk.briefing(selectedId) : qk.latestBriefing,
    queryFn: () =>
      (selectedId ? getBriefing(selectedId) : getLatestBriefing()).then((r) => r.data),
    enabled: !!list, // wait for the list to resolve before fetching the body
    retry: false,
  });

  const generate = useMutation<Briefing, unknown, void>({
    mutationFn: () => generateBriefing().then((r) => r.data),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: qk.briefings });
      setSelectedId(data.id);
    },
  });

  const briefing = briefingQuery.data;

  return (
    <div className="stack">
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '240px 1fr',
          gap: 12,
          minHeight: 0,
        }}
      >
        <BriefingArchive
          list={list ?? []}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onGenerate={() => generate.mutate()}
          generating={generate.isPending}
        />

        <Panel
          title="Analyst Briefing"
          subtitle={
            briefing
              ? `${briefing.period_label} · ${fmtDateTime(briefing.generated_at)} · ${briefing.model}`
              : 'No briefing loaded'
          }
        >
          {generate.isPending && (
            <span className="loading-cursor muted">SYNTHESIZING BRIEFING</span>
          )}

          {!generate.isPending && generate.isError && (
            <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
              <span style={{ color: 'var(--warning)' }}>⚠ </span>
              {errMessage(generate.error)}
            </div>
          )}

          {!generate.isPending && briefingQuery.isLoading && (
            <span className="loading-cursor muted">LOADING</span>
          )}

          {!generate.isPending && !briefingQuery.isLoading && !briefing && (
            <div className="muted" style={{ fontSize: 12 }}>
              No briefings yet. Click GENERATE to build the first one.
            </div>
          )}

          {!generate.isPending && briefing && (
            <BriefingBody briefing={briefing} />
          )}
        </Panel>
      </div>

      {briefing && <ChatPanel briefingId={briefing.id} />}
    </div>
  );
}

// ─── Archive sidebar ────────────────────────────────────────────────────────
function BriefingArchive({
  list,
  selectedId,
  onSelect,
  onGenerate,
  generating,
}: {
  list: { id: string; period_label: string; generated_at: string; preview: string }[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onGenerate: () => void;
  generating: boolean;
}) {
  return (
    <Panel
      title="Archive"
      subtitle={`${list.length} BRIEFINGS`}
      actions={
        <button
          className="btn"
          onClick={onGenerate}
          disabled={generating}
          title="Build a new briefing (uses Claude Opus 4.7 tokens)"
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          {...staticDisabledProps()}
        >
          <Sparkles size={12} />
          {generating ? 'GENERATING' : 'GENERATE'}
        </button>
      }
      bodyStyle={{ padding: 0 }}
    >
      <div style={{ display: 'flex', flexDirection: 'column' }}>
        {list.length === 0 && (
          <div className="muted" style={{ padding: 12, fontSize: 12 }}>
            No briefings yet.
          </div>
        )}
        {list.map((b) => {
          const active = b.id === selectedId;
          return (
            <button
              key={b.id}
              onClick={() => onSelect(b.id)}
              className="archive-item"
              style={{
                textAlign: 'left',
                padding: '8px 12px',
                border: 'none',
                borderBottom: '1px solid var(--border)',
                background: active ? 'var(--bg-panel-alt)' : 'transparent',
                color: 'var(--text-primary)',
                cursor: 'pointer',
                fontFamily: 'inherit',
              }}
            >
              <div style={{ fontSize: 11, fontWeight: 'bold' }}>{b.period_label}</div>
              <div className="muted" style={{ fontSize: 10 }}>
                {fmtDateTime(b.generated_at)}
              </div>
              <div
                className="muted"
                style={{
                  fontSize: 10,
                  marginTop: 4,
                  display: '-webkit-box',
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: 'vertical',
                  overflow: 'hidden',
                }}
              >
                {b.preview}…
              </div>
            </button>
          );
        })}
      </div>
    </Panel>
  );
}

// ─── Briefing body + watch list ─────────────────────────────────────────────
function BriefingBody({ briefing }: { briefing: Briefing }) {
  return (
    <div>
      <div
        style={{
          whiteSpace: 'pre-wrap',
          fontSize: 13,
          lineHeight: 1.55,
          marginBottom: briefing.watch_items?.length ? 16 : 0,
        }}
      >
        {briefing.briefing_md}
      </div>

      {briefing.watch_items && briefing.watch_items.length > 0 && (
        <div
          style={{
            borderTop: '1px solid var(--border)',
            paddingTop: 10,
            marginTop: 12,
          }}
        >
          <div
            className="muted"
            style={{ fontSize: 10, marginBottom: 6, letterSpacing: 1 }}
          >
            WATCH LIST
          </div>
          {briefing.watch_items.map((w, i) => (
            <div
              key={i}
              style={{
                display: 'flex',
                gap: 8,
                fontSize: 12,
                marginBottom: 6,
                alignItems: 'baseline',
              }}
            >
              <span
                style={{
                  color: SEVERITY_COLOR[w.severity] || 'var(--text-secondary)',
                  fontSize: 10,
                  textTransform: 'uppercase',
                  letterSpacing: 1,
                  minWidth: 48,
                }}
              >
                {w.severity}
              </span>
              <span style={{ fontWeight: 'bold' }}>{w.title}</span>
              <span className="muted">— {w.why}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Chat panel ──────────────────────────────────────────────────────────────
function ChatPanel({ briefingId }: { briefingId: string }) {
  const [history, setHistory] = useState<BriefingChatMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const scrollerRef = useRef<HTMLDivElement>(null);

  // Reset chat when the user picks a different briefing.
  useEffect(() => {
    setHistory([]);
    setError(null);
  }, [briefingId]);

  useEffect(() => {
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [history.length]);

  const chat = useMutation({
    mutationFn: (message: string) => chatWithBriefing(briefingId, message, history).then((r) => r.data),
    onSuccess: (data, message) => {
      setHistory((prev) => [
        ...prev,
        { role: 'user', content: message },
        { role: 'assistant', content: data.reply },
      ]);
      setDraft('');
      setError(null);
    },
    onError: (err) => setError(errMessage(err)),
  });

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const text = draft.trim();
    if (!text || chat.isPending) return;
    chat.mutate(text);
  };

  return (
    <Panel
      title="Chat with the Analyst"
      subtitle={history.length > 0 ? `${history.length / 2} TURNS · EPHEMERAL` : 'EPHEMERAL — no history persisted'}
      actions={
        history.length > 0 ? (
          <button
            className="btn"
            onClick={() => {
              setHistory([]);
              setError(null);
            }}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            <RefreshCw size={12} />
            RESET
          </button>
        ) : null
      }
    >
      <div
        ref={scrollerRef}
        style={{
          maxHeight: 420,
          overflowY: 'auto',
          marginBottom: 10,
          paddingRight: 4,
        }}
      >
        {history.length === 0 && !chat.isPending && (
          <div className="muted" style={{ fontSize: 12 }}>
            Ask about anything in the briefing or the underlying data — recession
            signals, ABS spread trends by segment, BDC nonaccrual moves, specific
            indicator histories, recent filings, news themes.
          </div>
        )}

        {history.map((m, i) => (
          <div
            key={i}
            style={{
              marginBottom: 12,
              fontSize: 12,
              lineHeight: 1.5,
              whiteSpace: 'pre-wrap',
            }}
          >
            <div
              className="muted"
              style={{ fontSize: 10, letterSpacing: 1, marginBottom: 2 }}
            >
              {m.role === 'user' ? 'YOU' : 'ANALYST'}
            </div>
            <div>{m.content}</div>
          </div>
        ))}

        {chat.isPending && (
          <div style={{ fontSize: 12 }}>
            <div
              className="muted"
              style={{ fontSize: 10, letterSpacing: 1, marginBottom: 2 }}
            >
              ANALYST
            </div>
            <LoadingCursor />
          </div>
        )}

        {error && (
          <div style={{ fontSize: 12, color: 'var(--warning)' }}>⚠ {error}</div>
        )}
      </div>

      <form onSubmit={onSubmit} style={{ display: 'flex', gap: 8 }}>
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Ask the analyst…"
          disabled={chat.isPending}
          style={{
            flex: 1,
            background: 'var(--bg-panel-alt)',
            color: 'var(--text-primary)',
            border: '1px solid var(--border-bright)',
            padding: '6px 10px',
            fontSize: 12,
            fontFamily: 'inherit',
            borderRadius: 2,
          }}
          {...staticDisabledProps()}
        />
        <button
          type="submit"
          className="btn"
          disabled={chat.isPending || !draft.trim()}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          {...staticDisabledProps()}
        >
          <Send size={12} />
          SEND
        </button>
      </form>
    </Panel>
  );
}
