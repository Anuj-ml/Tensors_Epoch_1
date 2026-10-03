import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/Sidebar";
import TopBar from "@/components/TopBar";

export const metadata: Metadata = {
  title: "Frames Studio — Local AI B-Roll Discovery",
  description: "Semantic discovery, story editing and rough-cut — fully local",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="shell">
          <Sidebar />
          <main className="main">
            <TopBar />
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
