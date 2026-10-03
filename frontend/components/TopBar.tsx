"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAMES: Record<string, string> = {
  "/": "Discover",
  "/script": "Script studio",
  "/story": "Storyboard",
  "/explore": "Explore galaxy",
  "/history": "Search history",
};

export default function TopBar({ onExport }: { onExport?: () => void }) {
  const path = usePathname();
  const name = NAMES[path] ?? "Discover";
  return (
    <div className="topbar">
      <div className="crumb">
        <span>Workspace</span>
        <span>›</span>
        <b>{name}</b>
        <span className="beta">BETA</span>
      </div>
      <div className="topbar-right">
        <span className="saved-dot">All changes saved</span>
        {onExport ? (
          <button className="dark small" onClick={onExport}>
            ⬆ Export story ↗
          </button>
        ) : (
          <Link href="/story">
            <button className="dark small">⬆ Export story ↗</button>
          </Link>
        )}
      </div>
    </div>
  );
}
