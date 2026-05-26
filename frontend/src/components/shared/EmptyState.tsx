interface Props {
  message?: string;
}

export default function EmptyState({ message = 'NO DATA' }: Props) {
  return <div className="empty-state">{message}</div>;
}
