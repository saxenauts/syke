import { sourceColors } from '@/lib/colors';

// ── Knowledge Graph types ──────────────────────────────────────────────────

export interface GraphNode {
  id: string;
  label: string;
  type: 'source' | 'topic';
  x: number;
  y: number;
  firstSeen: number;
  color: string;
  /** radius of circle; source nodes use rect rendering instead */
  r: number;
}

export interface GraphEdge {
  id: string;
  fromId: string;
  toId: string;
  /** discovery = source found this topic; cross-ref = topic↔topic connection; synthesis = insight arc */
  edgeType: 'discovery' | 'cross-ref' | 'synthesis';
  firstSeen: number;
  color: string;
}

// ── Static graph layout ────────────────────────────────────────────────────

// Source nodes — row at top of SVG (1000 × 560)
const SOURCE_NODES: GraphNode[] = [
  { id: 'claude-web',    label: 'Claude Web',    type: 'source', x: 90,  y: 55, firstSeen: 4,  color: sourceColors['claude-web'],    r: 0 },
  { id: 'claude-code',   label: 'Claude Code',   type: 'source', x: 230, y: 55, firstSeen: 5,  color: sourceColors['claude-code'],   r: 0 },
  { id: 'claude-cowork', label: 'Claude Cowork', type: 'source', x: 370, y: 55, firstSeen: 24, color: sourceColors['claude-cowork'], r: 0 },
  { id: 'gmail',         label: 'Gmail',         type: 'source', x: 510, y: 55, firstSeen: 44, color: sourceColors['gmail'],         r: 0 },
  { id: 'chatgpt',       label: 'ChatGPT',       type: 'source', x: 650, y: 55, firstSeen: 58, color: sourceColors['chatgpt'],       r: 0 },
  { id: 'github',        label: 'GitHub',        type: 'source', x: 830, y: 55, firstSeen: 72, color: sourceColors['github'],        r: 0 },
];

// Topic nodes — clustered by semantic proximity in the canvas body
const TOPIC_NODES: GraphNode[] = [
  // Philosophy cluster (under claude-web, left)
  { id: 'consciousness',    label: 'consciousness',    type: 'topic', x: 90,  y: 175, firstSeen: 11,  color: sourceColors['claude-web'],    r: 9 },
  { id: 'emergence',        label: 'emergence',        type: 'topic', x: 38,  y: 265, firstSeen: 11,  color: sourceColors['claude-web'],    r: 7 },
  { id: 'emergence theory', label: 'emergence theory', type: 'topic', x: 130, y: 300, firstSeen: 102, color: sourceColors['claude-web'],    r: 6 },
  { id: 'psychonaut',       label: 'psychonaut',       type: 'topic', x: 50,  y: 365, firstSeen: 102, color: sourceColors['claude-web'],    r: 6 },

  // Code / ALMA cluster (under claude-code, bridging center)
  { id: 'context-engine',   label: 'context-engine',   type: 'topic', x: 220, y: 170, firstSeen: 14,  color: sourceColors['claude-code'],   r: 7 },
  { id: 'agent SDK',        label: 'agent SDK',        type: 'topic', x: 175, y: 260, firstSeen: 14,  color: sourceColors['claude-code'],   r: 7 },
  { id: 'ALMA',             label: 'ALMA',             type: 'topic', x: 300, y: 155, firstSeen: 26,  color: sourceColors['claude-code'],   r: 12 }, // large — cross-source
  { id: 'meta-learning',    label: 'meta-learning',    type: 'topic', x: 340, y: 245, firstSeen: 26,  color: sourceColors['claude-code'],   r: 8 },

  // Strategy cluster (under cowork)
  { id: 'startup strategy', label: 'startup strategy', type: 'topic', x: 365, y: 170, firstSeen: 34,  color: sourceColors['claude-cowork'], r: 8 },
  { id: 'product vision',   label: 'product vision',   type: 'topic', x: 410, y: 255, firstSeen: 34,  color: sourceColors['claude-cowork'], r: 7 },
  { id: 'Persona project',  label: 'Persona project',  type: 'topic', x: 460, y: 162, firstSeen: 37,  color: sourceColors['claude-cowork'], r: 10 },

  // Central synthesis (companion + perception systems)
  { id: 'companion AI',        label: 'companion AI',        type: 'topic', x: 530, y: 175, firstSeen: 69,  color: '#f59e0b', r: 11 }, // amber — cross-source synthesis
  { id: 'perception systems',  label: 'perception systems',  type: 'topic', x: 310, y: 395, firstSeen: 115, color: '#f59e0b', r: 13 }, // amber — THE synthesis node

  // Email cluster
  { id: 'visa status',              label: 'visa status',          type: 'topic', x: 490, y: 265, firstSeen: 52,  color: sourceColors['gmail'], r: 6 },
  { id: 'partnership discussions',  label: 'partnerships',         type: 'topic', x: 545, y: 360, firstSeen: 52,  color: sourceColors['gmail'], r: 6 },
  { id: 'investor updates',         label: 'investor updates',     type: 'topic', x: 700, y: 175, firstSeen: 130, color: sourceColors['gmail'], r: 7 },

  // Personal / ChatGPT cluster
  { id: 'runway anxiety',    label: 'runway anxiety',    type: 'topic', x: 640, y: 270, firstSeen: 66,  color: sourceColors['chatgpt'],  r: 7 },
  { id: 'career transitions',label: 'career transitions',type: 'topic', x: 670, y: 370, firstSeen: 66,  color: sourceColors['chatgpt'],  r: 6 },

  // GitHub cluster
  { id: 'open source',    label: 'open source',    type: 'topic', x: 820, y: 170, firstSeen: 80,  color: sourceColors['github'],   r: 7 },
  { id: '12-year arc',    label: '12-year arc',    type: 'topic', x: 875, y: 260, firstSeen: 80,  color: sourceColors['github'],   r: 9 },
  { id: 'technical depth',label: 'technical depth',type: 'topic', x: 835, y: 360, firstSeen: 80,  color: sourceColors['github'],   r: 7 },
  { id: 'systems thinking',label: 'systems thinking',type: 'topic', x: 730, y: 270, firstSeen: 83,  color: sourceColors['github'],   r: 7 },
];

// Pre-defined edges (static, by firstSeen time)
const STATIC_EDGES: GraphEdge[] = [
  // ── Discovery edges (source → topic, thin, dashed) ──
  { id: 'e-cw-con',    fromId: 'claude-web',    toId: 'consciousness',         edgeType: 'discovery', firstSeen: 11,  color: sourceColors['claude-web'] },
  { id: 'e-cw-eme',    fromId: 'claude-web',    toId: 'emergence',             edgeType: 'discovery', firstSeen: 11,  color: sourceColors['claude-web'] },
  { id: 'e-cc-ctx',    fromId: 'claude-code',   toId: 'context-engine',        edgeType: 'discovery', firstSeen: 14,  color: sourceColors['claude-code'] },
  { id: 'e-cc-sdk',    fromId: 'claude-code',   toId: 'agent SDK',             edgeType: 'discovery', firstSeen: 14,  color: sourceColors['claude-code'] },
  { id: 'e-cw-alma',   fromId: 'claude-web',    toId: 'ALMA',                  edgeType: 'discovery', firstSeen: 26,  color: sourceColors['claude-web'] },
  { id: 'e-cc-alma',   fromId: 'claude-code',   toId: 'ALMA',                  edgeType: 'discovery', firstSeen: 26,  color: sourceColors['claude-code'] },
  { id: 'e-cc-ml',     fromId: 'claude-code',   toId: 'meta-learning',         edgeType: 'discovery', firstSeen: 26,  color: sourceColors['claude-code'] },
  { id: 'e-cow-ss',    fromId: 'claude-cowork', toId: 'startup strategy',      edgeType: 'discovery', firstSeen: 34,  color: sourceColors['claude-cowork'] },
  { id: 'e-cow-pv',    fromId: 'claude-cowork', toId: 'product vision',        edgeType: 'discovery', firstSeen: 34,  color: sourceColors['claude-cowork'] },
  { id: 'e-cow-per',   fromId: 'claude-cowork', toId: 'Persona project',       edgeType: 'discovery', firstSeen: 37,  color: sourceColors['claude-cowork'] },
  { id: 'e-gm-vis',    fromId: 'gmail',         toId: 'visa status',           edgeType: 'discovery', firstSeen: 52,  color: sourceColors['gmail'] },
  { id: 'e-gm-part',   fromId: 'gmail',         toId: 'partnership discussions',edgeType: 'discovery', firstSeen: 52, color: sourceColors['gmail'] },
  { id: 'e-cgpt-run',  fromId: 'chatgpt',       toId: 'runway anxiety',        edgeType: 'discovery', firstSeen: 66,  color: sourceColors['chatgpt'] },
  { id: 'e-cgpt-car',  fromId: 'chatgpt',       toId: 'career transitions',    edgeType: 'discovery', firstSeen: 66,  color: sourceColors['chatgpt'] },
  { id: 'e-cgpt-comp', fromId: 'chatgpt',       toId: 'companion AI',          edgeType: 'discovery', firstSeen: 69,  color: sourceColors['chatgpt'] },
  { id: 'e-gh-os',     fromId: 'github',        toId: 'open source',           edgeType: 'discovery', firstSeen: 80,  color: sourceColors['github'] },
  { id: 'e-gh-12y',    fromId: 'github',        toId: '12-year arc',           edgeType: 'discovery', firstSeen: 80,  color: sourceColors['github'] },
  { id: 'e-gh-td',     fromId: 'github',        toId: 'technical depth',       edgeType: 'discovery', firstSeen: 80,  color: sourceColors['github'] },
  { id: 'e-gh-sys',    fromId: 'github',        toId: 'systems thinking',      edgeType: 'discovery', firstSeen: 83,  color: sourceColors['github'] },
  { id: 'e-cw-psy',    fromId: 'claude-web',    toId: 'psychonaut',            edgeType: 'discovery', firstSeen: 102, color: sourceColors['claude-web'] },
  { id: 'e-cw-et',     fromId: 'claude-web',    toId: 'emergence theory',      edgeType: 'discovery', firstSeen: 102, color: sourceColors['claude-web'] },
  { id: 'e-gm-inv',    fromId: 'gmail',         toId: 'investor updates',      edgeType: 'discovery', firstSeen: 130, color: sourceColors['gmail'] },

  // ── Cross-reference edges (topic ↔ topic — reach back in time) ──
  // ALMA ↔ meta-learning early connection
  { id: 'xr-alma-ml',    fromId: 'ALMA',              toId: 'meta-learning',      edgeType: 'cross-ref', firstSeen: 26,  color: sourceColors['claude-code'] },
  // consciousness search confirms links
  { id: 'xr-con-eme',    fromId: 'consciousness',     toId: 'emergence',          edgeType: 'cross-ref', firstSeen: 55,  color: sourceColors['claude-web'] },
  // perception systems cross-reference (t=115) — edges reach BACK to past discoveries
  { id: 'xr-ps-con',     fromId: 'perception systems',toId: 'consciousness',      edgeType: 'cross-ref', firstSeen: 115, color: '#f59e0b' },
  { id: 'xr-ps-alma',    fromId: 'perception systems',toId: 'ALMA',               edgeType: 'cross-ref', firstSeen: 115, color: '#f59e0b' },
  { id: 'xr-ps-ctx',     fromId: 'perception systems',toId: 'context-engine',     edgeType: 'cross-ref', firstSeen: 115, color: '#f59e0b' },
  { id: 'xr-ps-ss',      fromId: 'perception systems',toId: 'startup strategy',   edgeType: 'cross-ref', firstSeen: 115, color: '#f59e0b' },
  { id: 'xr-ps-td',      fromId: 'perception systems',toId: 'technical depth',    edgeType: 'cross-ref', firstSeen: 115, color: '#f59e0b' },
  // companion AI synthesis (t=148) — connects future (persona) with past (perception)
  { id: 'xr-comp-per',   fromId: 'companion AI',      toId: 'Persona project',    edgeType: 'cross-ref', firstSeen: 148, color: '#f59e0b' },
  { id: 'xr-comp-ps',    fromId: 'companion AI',      toId: 'perception systems', edgeType: 'cross-ref', firstSeen: 148, color: '#f59e0b' },
  // ALMA cross-reference (t=161) — tri-source, reaches across entire graph
  { id: 'xr-alma-comp',  fromId: 'ALMA',              toId: 'companion AI',       edgeType: 'cross-ref', firstSeen: 161, color: sourceColors['claude-code'] },
  { id: 'xr-alma-ps',    fromId: 'ALMA',              toId: 'perception systems', edgeType: 'cross-ref', firstSeen: 161, color: sourceColors['claude-code'] },

  // ── Synthesis edges (thick glow — from synthesized_insight moments) ──
  { id: 'syn-ps-con',    fromId: 'perception systems',toId: 'consciousness',      edgeType: 'synthesis', firstSeen: 115, color: '#f59e0b' },
  { id: 'syn-ps-alma',   fromId: 'perception systems',toId: 'ALMA',               edgeType: 'synthesis', firstSeen: 115, color: '#f59e0b' },
  { id: 'syn-comp-per',  fromId: 'companion AI',      toId: 'Persona project',    edgeType: 'synthesis', firstSeen: 148, color: '#f59e0b' },
  { id: 'syn-comp-ps',   fromId: 'companion AI',      toId: 'perception systems', edgeType: 'synthesis', firstSeen: 169, color: '#f59e0b' },
];

export const GRAPH_NODES: GraphNode[] = [...SOURCE_NODES, ...TOPIC_NODES];
export const GRAPH_EDGES: GraphEdge[] = STATIC_EDGES;

// ── End knowledge graph static data ───────────────────────────────────────

interface ReplayStep {
  time_s: number;
  type: string;
  tool_name?: string;
  args_display?: string;
  result_display?: string;
  thinking_text?: string;
  topics_discovered?: string[];
  synthesized_insight?: string;
  coverage: {
    sources: Record<string, boolean | undefined>;
    pct: number;
  };
}

interface ReplayData {
  duration_s: number;
  total_steps: number;
  steps: ReplayStep[];
}

export interface TimelineEvent {
  time_s: number;
  source: string | null;
  type: string;
  topics: string[];
  coverage: number;
  tool_name?: string;
  args_display?: string;
  result_display?: string;
  thinking_text?: string;
  synthesized_insight?: string;
}

export interface TopicBubble {
  id: string;
  label: string;
  sources: string[];
  firstSeen: number;
  position: { x: number; y: number };
}

export interface ThemeThread {
  id: string;
  label: string;
  fromSource: string;
  fromX: number;
  fromY: number;
  toSource: string;
  toX: number;
  toY: number;
  firstSeen: number;
  color: string;
}

export const LANE_Y_POSITIONS: Record<string, number> = {
  'claude-web': 100,
  'claude-code': 200,
  'claude-cowork': 300,
  gmail: 400,
  chatgpt: 500,
  github: 600,
};

// Hardcoded cross-source thread specs: topics known to span multiple platforms
const CROSS_SOURCE_THREAD_SPECS = [
  { label: 'ALMA', source1: 'claude-web', source2: 'claude-code', topicFirstSeen: 26 },
  { label: 'perception systems', source1: 'claude-code', source2: 'github', topicFirstSeen: 115 },
  { label: 'Persona project', source1: 'claude-web', source2: 'chatgpt', topicFirstSeen: 37 },
  { label: 'startup strategy', source1: 'claude-cowork', source2: 'gmail', topicFirstSeen: 34 },
];

export function processReplayData(data: ReplayData): {
  events: TimelineEvent[];
  topics: TopicBubble[];
  threads: ThemeThread[];
  duration: number;
} {
  const events: TimelineEvent[] = [];
  const topicMap = new Map<string, TopicBubble>();

  // Extract events and discover topics
  data.steps.forEach((step) => {
    // Extract source from args_display (e.g., "source=claude-code")
    let source: string | null = null;
    if (step.args_display) {
      const match = step.args_display.match(/source=([a-z-]+)/);
      if (match) {
        source = match[1];
      }
    }

    // Create timeline event
    events.push({
      time_s: step.time_s,
      source,
      type: step.type,
      topics: step.topics_discovered || [],
      coverage: step.coverage.pct,
      tool_name: step.tool_name,
      args_display: step.args_display,
      result_display: step.result_display,
      thinking_text: step.thinking_text,
      synthesized_insight: step.synthesized_insight,
    });

    // Track topic discoveries (only for events with an explicit source lane)
    if (step.topics_discovered && source) {
      step.topics_discovered.forEach((topic) => {
        if (!topicMap.has(topic)) {
          topicMap.set(topic, {
            id: topic,
            label: topic,
            sources: [source],
            firstSeen: step.time_s,
            position: { x: 0, y: 0 }, // calculated in layoutTopics
          });
        } else {
          const existing = topicMap.get(topic)!;
          if (!existing.sources.includes(source)) {
            existing.sources.push(source);
          }
        }
      });
    }
  });

  // Convert topics map to array and layout in grid
  const topics = layoutTopics(Array.from(topicMap.values()));

  // Compute last event time per source for thread anchoring
  const lastSourceEventTime = (source: string): number => {
    const sourceTimes = events
      .filter((e) => e.source === source)
      .map((e) => e.time_s);
    return sourceTimes.length > 0 ? Math.max(...sourceTimes) : 0;
  };

  // Build cross-source stitch threads
  const threads: ThemeThread[] = CROSS_SOURCE_THREAD_SPECS.map((spec) => {
    const t1 = lastSourceEventTime(spec.source1);
    const t2 = lastSourceEventTime(spec.source2);
    const firstSeen = Math.max(t1, t2, spec.topicFirstSeen);
    return {
      id: `${spec.label}-${spec.source1}-${spec.source2}`,
      label: spec.label,
      fromSource: spec.source1,
      fromX: timeToX(t1, data.duration_s),
      fromY: LANE_Y_POSITIONS[spec.source1],
      toSource: spec.source2,
      toX: timeToX(t2, data.duration_s),
      toY: LANE_Y_POSITIONS[spec.source2],
      firstSeen,
      color: sourceColors[spec.source1] || '#8b8b94',
    };
  });

  return {
    events,
    topics,
    threads,
    duration: data.duration_s,
  };
}

function layoutTopics(topics: TopicBubble[]): TopicBubble[] {
  // Sort by first seen time
  topics.sort((a, b) => a.firstSeen - b.firstSeen);

  // 2-3 column grid layout
  const columns = topics.length > 12 ? 3 : 2;
  const colWidth = columns === 3 ? 50 : 65;

  return topics.map((t, i) => ({
    ...t,
    position: {
      x: 1050 + (i % columns) * colWidth,
      y: 120 + Math.floor(i / columns) * 55,
    },
  }));
}

export function timeToX(time_s: number, duration: number): number {
  return 100 + (time_s / duration) * 900;
}

export function createBezierPath(
  fromX: number,
  fromY: number,
  toX: number,
  toY: number
): string {
  const midX = (fromX + toX) / 2;
  return `M ${fromX} ${fromY} Q ${midX} ${fromY}, ${midX} ${(fromY + toY) / 2} T ${toX} ${toY}`;
}
