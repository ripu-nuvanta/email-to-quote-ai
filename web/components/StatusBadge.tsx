const STATUSES: Record<string, [label: string, tone: string]> = {
  received: ["Received", ""],
  pending_approval: ["Pending approval", "warn"],
  approved: ["Approved", "info"],
  sending: ["Sending…", "info"],
  sent: ["Sent", "ok"],
  rejected: ["Rejected", "bad"],
  ignored: ["Ignored", ""],
};

export function StatusBadge({ status }: { status: string }) {
  const [label, tone] = STATUSES[status] ?? [status, ""];
  return <span className={`badge ${tone}`}>{label}</span>;
}
