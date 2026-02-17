/* ── Dark mode colors (used by research page + Recharts) ── */
export const colors = {
  bg: '#0f1117',
  surface: '#1a1d27',
  surface2: '#242836',
  text: '#e4e4e7',
  dim: '#8b8b94',
  muted: '#5a5b65',
  border: '#2a2e3d',
  accent: '#60a5fa',
  accentDim: '#60a5fa33',
  success: '#34d399',
  warning: '#fbbf24',
  error: '#f87171',
  v0: '#8b8b94',
  v1: '#34d399',
  v2: '#60a5fa',
  v3: '#f472b6',
} as const;

/* ── Light mode colors (used by product page) ── */
export const lightColors = {
  bg: '#FAFAFA',
  surface: '#FFFFFF',
  surface2: '#F5F5F7',
  text: '#111111',
  dim: '#525252',
  muted: '#9CA3AF',
  border: '#E5E7EB',
  accent: '#6C5CE7',
  accentDim: '#6C5CE720',
} as const;

export const sourceColors: Record<string, string> = {
  'claude-web': '#a78bfa',
  'claude-code': '#8b7bd8',
  'claude-cowork': '#6b5ec7',
  gmail: '#ef4444',
  chatgpt: '#34d399',
  github: '#fbbf24',
};

export const sourceLabels: Record<string, string> = {
  'claude-web': 'Claude Web',
  'claude-code': 'Claude Code',
  'claude-cowork': 'Claude Cowork',
  gmail: 'Gmail',
  chatgpt: 'ChatGPT',
  github: 'GitHub',
};

/* ── Product page platform labels (no ChatGPT) ── */
export const productPlatforms = [
  { id: 'claude-code', name: 'Claude Code', description: 'Sessions, debugging, architecture decisions', status: 'available' as const },
  { id: 'claude-desktop', name: 'Claude Desktop Cowork', description: 'Extended conversations, research, strategy', status: 'available' as const },
  { id: 'claude-web', name: 'Claude Web', description: 'Browser-based Claude conversations', status: 'available' as const },
  { id: 'github', name: 'GitHub', description: 'Repos, PRs, commits, issues', status: 'available' as const },
  { id: 'gmail', name: 'Gmail', description: 'Email threads and correspondence', status: 'available' as const },
  { id: 'other-ai', name: 'Other AI Platforms', description: 'Any platform via export', status: 'coming-soon' as const },
  { id: 'other-chat', name: 'Other Chat Exports', description: 'ZIP/JSON exports from any tool', status: 'available' as const },
] as const;

export const strategyColors: Record<number, string> = {
  0: colors.v0,
  1: colors.v1,
  2: colors.v2,
  3: colors.v3,
};

export const strategyLabels: Record<number, string> = {
  0: 'v0: Baseline',
  1: 'v1: Concept Search',
  2: 'v2: Topic Expansion',
  3: 'v3: Entity Discovery',
};

export const intensityColors: Record<string, string> = {
  high: '#a78bfa',
  medium: '#34d399',
  low: '#8b8b94',
};
