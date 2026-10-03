"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const GROUPS: { label: string; items: { href: string; label: string; ico: string; count?: () => number }[] }[] = [
  {
    label: "YOUR WORKSPACE",
    items: [
      { href: "/", label: "Discover", ico: "⌕" },
      { href: "/script", label: "Script studio", ico: "▤" },
      { href: "/story", label: "Storyboard", ico: "▦" },
      { href: "/explore", label: "Explore galaxy", ico: "✦" },
      { href: "/history", label: "Search history", ico: "◷" },
    ],
  },
];

export default function Sidebar() {
  const path = usePathname();
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">▸</div>
        <div className="brand-text">
          <div className="b1">frames</div>
          <div className="b2">STUDIO</div>
        </div>
      </div>
      <div className="brand-sub">/01</div>

      <div className="workspace-card">
        <div className="avatar">a.</div>
        <div>
          <div className="t1">Personal workspace</div>
          <div className="t2">Good stories start here</div>
        </div>
      </div>

      {GROUPS.map((g) => (
        <div key={g.label}>
          <div className="nav-section">{g.label}</div>
          {g.items.map((l) => (
            <Link key={l.href} href={l.href} className={`nav-link ${path === l.href ? "active" : ""}`}>
              <span className="ico">{l.ico}</span>
              {l.label}
            </Link>
          ))}
        </div>
      ))}

      <div className="sidebar-promo">
        <div className="big">
          Less searching.
          <br />
          <span>More creating.</span>
        </div>
        <div className="small">Your ideas deserve a little room to grow.</div>
        <Link href="/story">Start with a story ↗</Link>
      </div>
    </aside>
  );
}
