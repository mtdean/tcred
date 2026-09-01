// In-app reader for full-text articles (newsletters via Meco/RSS bodies).
// Shows the AI summary up top, then the body split into paragraphs.

import * as Dialog from '@radix-ui/react-dialog';
import { useQuery } from '@tanstack/react-query';
import { ExternalLink, X } from 'lucide-react';
import { getArticleContent } from '../../lib/api';
import { fmtRelative } from '../../lib/utils';
import CategoryChip from '../shared/CategoryChip';
import SourceChip from '../shared/SourceChip';
import ScoreDots from '../shared/ScoreDots';
import LoadingCursor from '../shared/LoadingCursor';

interface Props {
  articleId: string | null;
  onClose: () => void;
}

export default function ArticleReaderModal({ articleId, onClose }: Props) {
  const { data, isLoading } = useQuery({
    queryKey: ['article-content', articleId],
    queryFn: () => getArticleContent(articleId!).then((r) => r.data),
    enabled: articleId !== null,
    staleTime: Infinity, // bodies never change once stored
  });

  const paragraphs =
    data?.content_text?.split(/\n{2,}/).filter((p) => p.trim()) ?? [];

  return (
    <Dialog.Root open={articleId !== null} onOpenChange={(o) => !o && onClose()}>
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
            width: 'min(760px, 94vw)',
            maxHeight: '88vh',
            overflow: 'auto',
            background: 'var(--bg-panel)',
            border: '1px solid var(--border-bright)',
            zIndex: 51,
          }}
        >
          <div className="panel-header" style={{ position: 'sticky', top: 0, zIndex: 1 }}>
            <span className="panel-title" style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              Reader{data ? ` — ${data.feed_name}` : ''}
            </span>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexShrink: 0 }}>
              {data && (
                <a href={data.url} target="_blank" rel="noreferrer" title="Open original" className="btn" style={{ padding: '2px 6px', display: 'inline-flex' }}>
                  <ExternalLink size={12} />
                </a>
              )}
              <Dialog.Close asChild>
                <button className="btn" style={{ padding: '2px 6px' }} aria-label="Close">
                  <X size={12} />
                </button>
              </Dialog.Close>
            </div>
          </div>
          <Dialog.Title style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>
            Article reader
          </Dialog.Title>

          <div style={{ padding: '14px 18px 22px' }}>
            {isLoading || !data ? (
              <LoadingCursor />
            ) : (
              <>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <SourceChip feedName={data.feed_name} sourceType={data.source_type} />
                  <CategoryChip category={data.feed_category} />
                  <ScoreDots score={data.relevance_score} />
                  <span className="muted" style={{ fontSize: 11 }}>
                    {fmtRelative(data.published_at ?? data.fetched_at)}
                  </span>
                </div>

                <h2 style={{ fontSize: 17, lineHeight: 1.35, margin: '10px 0 0', color: 'var(--text-primary)' }}>
                  {data.title}
                </h2>

                {data.ai_summary && (
                  <p
                    className="prose"
                    style={{
                      fontSize: 12.5,
                      lineHeight: 1.55,
                      margin: '12px 0 0',
                      padding: '6px 10px',
                      color: 'var(--text-secondary)',
                      borderLeft: '2px solid var(--border-bright)',
                    }}
                    title="AI summary"
                  >
                    {data.ai_summary}
                  </p>
                )}

                {paragraphs.length > 0 ? (
                  <div style={{ marginTop: 14 }}>
                    {paragraphs.map((p, i) => (
                      <p
                        key={i}
                        className="prose"
                        style={{ fontSize: 13.5, lineHeight: 1.65, margin: '0 0 12px', color: 'var(--text-primary)' }}
                      >
                        {p}
                      </p>
                    ))}
                  </div>
                ) : (
                  <p className="muted" style={{ fontSize: 12, marginTop: 14 }}>
                    No stored full text for this article — use the link above to open the
                    original.
                  </p>
                )}
              </>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
