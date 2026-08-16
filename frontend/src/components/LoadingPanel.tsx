export function LoadingPanel({ label = "正在加载" }: { label?: string }) {
  return (
    <div className="state-panel" role="status">
      <span className="state-panel__spinner" aria-hidden="true" />
      <p>{label}</p>
    </div>
  );
}
