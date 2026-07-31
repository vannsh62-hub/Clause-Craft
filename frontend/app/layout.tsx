import type { Metadata } from "next";
import { Inter, Space_Grotesk, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";
import { ThemeToggle } from "@/components/ThemeToggle";
import { UiChrome } from "@/components/UiChrome";
import { Toaster } from "sonner";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });
const spaceGrotesk = Space_Grotesk({ subsets: ["latin"], variable: "--font-space-grotesk" });
const plexMono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-plex-mono" });

export const metadata: Metadata = {
  title: "P2P-Clausecraft | AI Contract Drafting",
  description: "A deep agent that drafts contracts from counsel-approved clauses.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${spaceGrotesk.variable} ${plexMono.variable}`}>
      <body className="min-h-screen font-sans">
        <Toaster position="bottom-right" theme="system" richColors />
        <UiChrome>
          <div className="app-shell">
            <div className="theme-shell">
              <div className="layout-grid">
                <Sidebar />
                <main className="content-shell" style={{ position: "relative" }}>
                  <div style={{ position: "absolute", top: "-0.5rem", right: "0.5rem", zIndex: 50 }}>
                    <ThemeToggle />
                  </div>
                  <div style={{ marginTop: "1rem" }}>
                    {children}
                  </div>
                </main>
              </div>
            </div>
          </div>
        </UiChrome>
      </body>
    </html>
  );
}