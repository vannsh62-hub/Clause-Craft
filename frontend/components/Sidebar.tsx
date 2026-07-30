"use client";

import { useState } from "react";
import { useUiChrome } from "@/components/UiChrome";

// 12-segment spoke wheel SVG that spins
const BrandLogo = ({ className = "h-6 w-6" }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" className={className}>
    {[...Array(12)].map((_, i) => {
      const angle = (i * 30 * Math.PI) / 180;
      const x1 = (12 + 4 * Math.cos(angle)).toFixed(4);
      const y1 = (12 + 4 * Math.sin(angle)).toFixed(4);
      const x2 = (12 + 9 * Math.cos(angle)).toFixed(4);
      const y2 = (12 + 9 * Math.sin(angle)).toFixed(4);
      return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} />;
    })}
  </svg>
);

// Split-screen sidebar collapse icon [|]
const SplitIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
    <rect x="3" y="3" width="18" height="18" rx="2" />
    <line x1="9" y1="3" x2="9" y2="21" />
  </svg>
);

// Navigation icons as SVG
const ChatIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  </svg>
);

const PlusIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
    <line x1="12" y1="5" x2="12" y2="19" />
    <line x1="5" y1="12" x2="19" y2="12" />
  </svg>
);

const LibraryIcon = () => (
  <span className="text-lg">📚</span>
);

const TableIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
    <rect x="3" y="3" width="18" height="18" rx="2" />
    <line x1="3" y1="9" x2="21" y2="9" />
    <line x1="3" y1="15" x2="21" y2="15" />
    <line x1="10" y1="9" x2="10" y2="21" />
  </svg>
);

const PlaybookIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-5 w-5">
    <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
    <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
  </svg>
);

const SelectorIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
    <polyline points="7 15 12 20 17 15" />
    <polyline points="7 9 12 4 17 9" />
  </svg>
);

const ChevronDownIcon = ({ className = "h-4 w-4" }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
    <polyline points="6 9 12 15 18 9" />
  </svg>
);

const ThreeDotsIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
    <circle cx="12" cy="12" r="1" />
    <circle cx="19" cy="12" r="1" />
    <circle cx="5" cy="12" r="1" />
  </svg>
);

function titleOf(request: string): string {
  const first = request.trim().split("\n")[0];
  return first.length > 40 ? `${first.slice(0, 40)}…` : first || "Untitled draft";
}

function SidebarEntry({
  title,
  active,
  badge,
  onClick,
}: {
  title: string;
  active: boolean;
  badge?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`sidebar-entry ${active ? "sidebar-entry-active" : ""}`}
      title={title}
    >
      <span className="sidebar-entry-icon">
        <ChatIcon />
      </span>
      <span className="sidebar-entry-title">{title}</span>
      {badge && <span className="sidebar-entry-badge">{badge}</span>}
    </button>
  );
}

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const [assistantHistoryOpen, setAssistantHistoryOpen] = useState(true);
  const { openLibrary, openPlaybook, openTabularReview, contracts, openContract, openTarget, startNewChat } =
    useUiChrome();

  const history = contracts;

  // `onClick` wires an item to an action. Items without one are placeholders for now.
  // Library opens the clause library; Playbook (which replaces Workflows) opens the rule
  // editor.
  const items: {
    label: string;
    icon: React.ReactNode;
    active: boolean;
    onClick?: () => void;
  }[] = [
    { label: "Assistant", icon: <ChatIcon />, active: true },
    { label: "Library", icon: <LibraryIcon />, active: false, onClick: openLibrary },
    { label: "Tabular Review", icon: <TableIcon />, active: false, onClick: openTabularReview },
    { label: "Playbook", icon: <PlaybookIcon />, active: false, onClick: openPlaybook },
  ];

  return (
    <aside className={`sidebar ${collapsed ? "sidebar-collapsed" : ""}`} style={{ minHeight: "calc(100vh - 3rem)" }}>
      <div className="sidebar-header">
        <div className="sidebar-brand">
          <div className="sidebar-brand-icon" style={{ background: "transparent", width: "auto", height: "auto" }}>
            <BrandLogo className="h-7 w-7 text-[color:var(--text)]" />
          </div>
          {!collapsed && (
            <span className="text-xl font-bold text-[color:var(--text)] tracking-tight">
              P2P-Clausecraft
            </span>
          )}
        </div>
        <button
          type="button"
          className="sidebar-collapse-toggle"
          onClick={() => setCollapsed((current) => !current)}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          style={{ background: "transparent", border: "none" }}
        >
          <SplitIcon />
        </button>
      </div>

      <button
        type="button"
        className="sidebar-new-chat"
        onClick={startNewChat}
        title="New chat"
        style={{ justifyContent: collapsed ? "center" : "flex-start" }}
      >
        <PlusIcon />
        {!collapsed && <span>New chat</span>}
      </button>

      <nav className="sidebar-nav" style={{ marginTop: "1rem" }}>
        {items.map((item) => (
          <div
            key={item.label}
            className={`sidebar-item ${item.active ? "active" : ""}`}
            onClick={item.onClick}
            role={item.onClick ? "button" : undefined}
            title={item.label}
            style={{
              padding: "0.65rem 0.85rem",
              borderRadius: "1rem",
              cursor: "pointer",
              gridTemplateColumns: collapsed ? "1fr" : "auto 1fr"
            }}
          >
            <span className="sidebar-item-icon" style={{ display: "flex", alignItems: "center", color: item.active ? "var(--accent)" : "var(--text)" }}>
              {item.icon}
            </span>
            {!collapsed && (
              <span className="sidebar-item-label" style={{ fontWeight: 500, fontSize: "0.925rem" }}>
                {item.label}
              </span>
            )}
          </div>
        ))}
      </nav>

      {!collapsed && (
        <div className="sidebar-sections" style={{ marginTop: "1rem", display: "flex", flexDirection: "column", gap: "0.5rem", minHeight: 0, flex: 1, overflow: "hidden" }}>
          {/* Assistant History — every past request, newest first. */}
          <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
            <div
              className="sidebar-submenu-title"
              onClick={() => setAssistantHistoryOpen(!assistantHistoryOpen)}
            >
              <span>Assistant History</span>
              <ChevronDownIcon className={`h-3 w-3 transition-transform ${assistantHistoryOpen ? "" : "-rotate-90"}`} />
            </div>
            {assistantHistoryOpen && (
              <div className="sidebar-list">
                {history.length === 0 ? (
                  <p className="text-xs text-[color:var(--text-muted)] px-4 py-1">No history yet</p>
                ) : (
                  history.map((c) => (
                    <SidebarEntry
                      key={c.id}
                      title={titleOf(c.request)}
                      active={openTarget?.id === c.id}
                      onClick={() => openContract(c)}
                    />
                  ))
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Profile footer */}
      <div className="sidebar-profile-footer">
        <div className="sidebar-profile-avatar">
          V
        </div>
        {!collapsed && (
          <>
            <div className="sidebar-profile-info">
              <h4 className="sidebar-profile-name">Vishal</h4>
              <p className="sidebar-profile-tier">Free</p>
            </div>
            <div className="sidebar-profile-selector">
              <SelectorIcon />
            </div>
          </>
        )}
      </div>
    </aside>
  );
}
