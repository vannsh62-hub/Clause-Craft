"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { ClauseLibrary } from "@/components/ClauseLibrary";
import { PlaybookEditor } from "@/components/PlaybookEditor";
import { TabularReview } from "@/components/TabularReview";
import { listContracts, type ContractSummary } from "@/lib/api";

/**
 * Shared UI chrome and session state.
 *
 * The sidebar and the page are siblings, so this provider owns what they share: the Clause
 * Library and Playbook modals, and the list of past contracts that becomes the assistant
 * history and the projects list. The sidebar reads the list and asks to open a contract; the
 * page opens it and refreshes the list after a run.
 */
type Chrome = {
  openLibrary: () => void;
  openPlaybook: () => void;
  openTabularReview: () => void;

  contracts: ContractSummary[];
  refreshContracts: () => void;

  /** The contract the sidebar asked to open; the page consumes and clears it. */
  openTarget: ContractSummary | null;
  openContract: (c: ContractSummary) => void;
  clearOpenTarget: () => void;

  /** Bumped when the user asks for a fresh conversation; the page resets on change. */
  newChatSignal: number;
  startNewChat: () => void;
};

const ChromeContext = createContext<Chrome>({
  openLibrary: () => {},
  openPlaybook: () => {},
  openTabularReview: () => {},
  contracts: [],
  refreshContracts: () => {},
  openTarget: null,
  openContract: () => {},
  clearOpenTarget: () => {},
  newChatSignal: 0,
  startNewChat: () => {},
});

export function useUiChrome(): Chrome {
  return useContext(ChromeContext);
}

export function UiChrome({ children }: { children: React.ReactNode }) {
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [playbookOpen, setPlaybookOpen] = useState(false);
  const [tabularOpen, setTabularOpen] = useState(false);
  const [contracts, setContracts] = useState<ContractSummary[]>([]);
  const [openTarget, setOpenTarget] = useState<ContractSummary | null>(null);
  const [newChatSignal, setNewChatSignal] = useState(0);

  const refreshContracts = useCallback(() => {
    listContracts()
      .then(setContracts)
      .catch(() => {
        /* history is best-effort; a failed load should not break the app */
      });
  }, []);

  useEffect(() => {
    refreshContracts();
  }, [refreshContracts]);

  return (
    <ChromeContext.Provider
      value={{
        openLibrary: () => setLibraryOpen(true),
        openPlaybook: () => setPlaybookOpen(true),
        openTabularReview: () => setTabularOpen(true),
        contracts,
        refreshContracts,
        openTarget,
        openContract: (c) => setOpenTarget(c),
        clearOpenTarget: () => setOpenTarget(null),
        newChatSignal,
        // Clear any opened past contract and bump the signal so the page resets to a
        // fresh conversation.
        startNewChat: () => {
          setOpenTarget(null);
          setNewChatSignal((n) => n + 1);
        },
      }}
    >
      {children}
      {libraryOpen && <ClauseLibrary onClose={() => setLibraryOpen(false)} />}
      {playbookOpen && <PlaybookEditor onClose={() => setPlaybookOpen(false)} />}
      {tabularOpen && <TabularReview onClose={() => setTabularOpen(false)} />}
    </ChromeContext.Provider>
  );
}
