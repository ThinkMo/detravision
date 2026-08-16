import { CircleAlert } from "lucide-react";

export function ErrorPanel({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="state-panel state-panel--error" role="alert">
      <CircleAlert aria-hidden="true" size={22} />
      <p>{message}</p>
      <button type="button" className="secondary-button" onClick={onRetry}>
        重新加载
      </button>
    </div>
  );
}
