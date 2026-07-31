export type OssProject = {
  slug: string;
  name: string;
  repo: string;
  categories: string[];
  description: string;
  stars: number;
  forks: number;
  updatedAgo: string;
  language?: string;
};

const LANG_COLORS: Record<string, string> = {
  TypeScript: "#3178c6",
  Python: "#3572A5",
  Ruby: "#701516",
  JavaScript: "#f1e05a",
  Svelte: "#ff3e00",
  HTML: "#e34c26",
  "Jupyter Notebook": "#DA5B0B",
};

export function languageColor(lang?: string): string {
  return lang ? LANG_COLORS[lang] ?? "var(--accent)" : "var(--accent)";
}

export const ossProjects: OssProject[] = [
  {
    slug: "beerbottle90/ArthurLegal",
    name: "ArthurLegal",
    repo: "beerbottle90/ArthurLegal",
    categories: ["Contracts & Analysis", "Legal Research & Search"],
    description:
      "Multi-jurisdiction legal AI assistant packages for Claude.ai Projects — 14 jurisdictions, up to 7 MCP connectors, primary-source citation discipline.",
    stars: 101,
    forks: 23,
    updatedAgo: "3h ago",
  },
  {
    slug: "Open-Legal-Products/mike",
    name: "mike",
    repo: "Open-Legal-Products/mike",
    categories: ["Legal AI & NLP"],
    description: "OSS Legal AI Platform",
    stars: 4000,
    forks: 1300,
    updatedAgo: "5h ago",
    language: "TypeScript",
  },
  {
    slug: "eigenweltlabs/legalwork",
    name: "LegalWork",
    repo: "eigenweltlabs/legalwork",
    categories: ["AI Platforms & Workbenches", "Document Automation"],
    description: "Cowork Alternative for Lawyers",
    stars: 841,
    forks: 71,
    updatedAgo: "1d ago",
    language: "TypeScript",
  },
  {
    slug: "saidsurucu/yargi-mcp",
    name: "yargi-mcp",
    repo: "saidsurucu/yargi-mcp",
    categories: ["MCP Servers"],
    description: "Turkish legal databases",
    stars: 1100,
    forks: 164,
    updatedAgo: "4d ago",
    language: "Python",
  },
  {
    slug: "lawglance/lawglance",
    name: "lawglance",
    repo: "lawglance/lawglance",
    categories: ["AI Platforms & Workbenches", "Retrieval & RAG"],
    description: "Free RAG based AI legal assistant",
    stars: 303,
    forks: 76,
    updatedAgo: "1d ago",
    language: "Jupyter Notebook",
  },
  {
    slug: "Vaquill-AI/awesome-legaltech",
    name: "awesome-legaltech",
    repo: "Vaquill-AI/awesome-legaltech",
    categories: ["MCP Servers", "Benchmarks & Datasets"],
    description:
      "A curated list of awesome LegalTech resources - open source platforms, AI models, MCP servers, companies, datasets, and tools for the global legal ecosystem.",
    stars: 179,
    forks: 74,
    updatedAgo: "8d ago",
  },
  {
    slug: "Vaquill-AI/open-us-law",
    name: "open-us-law",
    repo: "Vaquill-AI/open-us-law",
    categories: ["Retrieval & RAG", "Case Law & Legal Data"],
    description:
      "Open, structured corpus of US primary law: 2M+ sections of state statutory codes, the US Code, and state constitutions, plus the ingestion pipeline that builds it. Data on Hugging Face (CC BY 4.0); scrapers Apache-2.0. Quarterly snapshots.",
    stars: 152,
    forks: 9,
    updatedAgo: "9h ago",
    language: "Python",
  },
  {
    slug: "stefanoamorelli/sec-edgar-mcp",
    name: "sec-edgar-mcp",
    repo: "stefanoamorelli/sec-edgar-mcp",
    categories: ["MCP Servers", "Legal AI & NLP"],
    description: "SEC EDGAR filings access",
    stars: 3379,
    forks: 42,
    updatedAgo: "2d ago",
    language: "Python",
  },
  {
    slug: "Vaquill-AI/ms-word-addin",
    name: "ms-word-addin",
    repo: "Vaquill-AI/ms-word-addin",
    categories: ["Document Automation", "Contracts & Analysis"],
    description:
      "Open Source Legal AI assistant for MS Word as Plugin - contract review, playbooks, drafting, and legal research task-pane add-in.",
    stars: 55,
    forks: 8,
    updatedAgo: "3d ago",
    language: "TypeScript",
  },
  {
    slug: "noslegal/taxonomy",
    name: "taxonomy",
    repo: "noslegal/taxonomy",
    categories: ["Practice & Matter Management", "Legal Research & Search"],
    description: "noslegal taxonomy facets and release notes",
    stars: 447,
    forks: 2,
    updatedAgo: "2mo ago",
  },
];