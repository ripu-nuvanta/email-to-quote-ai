import type { Metadata } from "next";
import type { ReactNode } from "react";
import { AppShell } from "@/components/AppShell";
import { SessionProvider } from "@/components/Session";
import "./globals.css";

export const metadata: Metadata = {
  title: "Quote approvals",
  description: "Review, edit and approve quotations created from customer emails",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <SessionProvider>
          <AppShell>{children}</AppShell>
        </SessionProvider>
      </body>
    </html>
  );
}
