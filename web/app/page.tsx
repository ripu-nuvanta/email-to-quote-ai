import { Suspense } from "react";
import { QuotesWorkspace } from "@/components/QuotesWorkspace";

export default function QuotesPage() {
  return (
    <Suspense fallback={<p className="muted pad">Loading…</p>}>
      <QuotesWorkspace />
    </Suspense>
  );
}
