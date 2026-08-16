import { Inbox } from "lucide-react";

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="state-panel state-panel--empty">
      <Inbox aria-hidden="true" size={22} />
      <p>{message}</p>
    </div>
  );
}
