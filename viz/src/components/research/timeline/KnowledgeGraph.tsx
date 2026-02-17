'use client';

import { useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { colors, sourceColors, sourceLabels } from '@/lib/colors';

// ── Layout constants ──────────────────────────────────────────────────────────

const DURATION = 189;
const SVG_W = 1000;
const SVG_H = 480;
const LANE_START_X = 100;
const LANE_END_X = 970;

function tx(t: number): number {
  return LANE_START_X + (t / DURATION) * (LANE_END_X - LANE_START_X);
}

const LANE_Y: Record<string, number> = {
  'claude-web':    75,
  'claude-code':   150,
  'claude-cowork': 225,
  gmail:           300,
  chatgpt:         375,
  github:          450,
};

const SOURCES = ['claude-web', 'claude-code', 'claude-cowork', 'gmail', 'chatgpt', 'github'];

// ── Node / edge definitions ───────────────────────────────────────────────────

interface Node {
  id: string; label: string; x: number; y: number; r: number;
  color: string; firstSeen: number; isSynthesis?: boolean;
}

interface Edge {
  id: string; fromId: string; toId: string;
  type: 'discovery' | 'cross-ref' | 'synthesis';
  firstSeen: number; color: string;
}

// Source "browse" events — dots that appear on lane lines when agent visits a source
interface LaneEvent {
  source: string; x: number; firstSeen: number; isRevisit: boolean;
}

// Realistic daily activity: treat DURATION=189 as ~5 weeks
// claude-web ~3–4x/week, code ~4–5x/week, gmail ~daily, cowork ~2x/week
function laneEvts(source: string, times: number[]): LaneEvent[] {
  return times.map((t, i) => ({ source, x: tx(t), firstSeen: t, isRevisit: i > 0 }));
}

const LANE_EVENTS: LaneEvent[] = [
  ...laneEvts('claude-web',    [4,   18,  30,  44,  58,  75,  93,  108, 122, 138, 155, 172]),
  ...laneEvts('claude-code',   [5,   15,  26,  38,  50,  62,  75,  88,  100, 109, 122, 136, 150, 163, 178]),
  ...laneEvts('claude-cowork', [24,  36,  50,  65,  80,  95,  110, 124, 138, 151, 164, 178]),
  ...laneEvts('gmail',         [44,  51,  59,  67,  75,  84,  93,  102, 111, 120, 130, 140, 150, 160, 170, 180]),
  ...laneEvts('chatgpt',       [58,  72,  88,  104, 120, 138, 156, 174]),
  ...laneEvts('github',        [72,  84,  96,  109, 122, 135, 148, 162, 176]),
];

const NODES: Node[] = [
  // claude-web lane — research, reading, hobby dives
  { id: 'human memory',      label: 'human memory',      x: tx(11),    y: LANE_Y['claude-web'] - 2, r: 8,  color: sourceColors['claude-web'],    firstSeen: 11  },
  { id: 'spaced repetition', label: 'spaced repetition', x: tx(11)+14, y: LANE_Y['claude-web'] + 9, r: 6,  color: sourceColors['claude-web'],    firstSeen: 11  },
  { id: 'founder essays',    label: 'founder essays',    x: tx(102),   y: LANE_Y['claude-web'] - 2, r: 6,  color: sourceColors['claude-web'],    firstSeen: 102 },
  { id: 'PKM systems',       label: 'PKM systems',        x: tx(102)+14,y: LANE_Y['claude-web'] + 9, r: 5,  color: sourceColors['claude-web'],    firstSeen: 102 },

  // claude-code lane
  { id: 'context-engine',    label: 'context-engine',    x: tx(14),    y: LANE_Y['claude-code'] - 2,r: 7,  color: sourceColors['claude-code'],   firstSeen: 14  },
  // vector search discovered mid-timeline in a second code sprint
  { id: 'vector search',     label: 'vector search',     x: tx(62),    y: LANE_Y['claude-code'] + 9,r: 6,  color: sourceColors['claude-code'],   firstSeen: 62  },

  // Between web + code: ALMA (multi-source synthesis)
  { id: 'ALMA',              label: 'ALMA',              x: tx(26),    y: 110,                      r: 12, color: '#a78bfa',                     firstSeen: 26, isSynthesis: true },
  { id: 'meta-learning',     label: 'meta-learning',     x: tx(26)+16, y: 122,                      r: 7,  color: sourceColors['claude-code'],   firstSeen: 26  },

  // claude-cowork lane — strategy, marketing, founder ops
  { id: 'go-to-market',      label: 'go-to-market',      x: tx(34),    y: LANE_Y['claude-cowork']-2,r: 8,  color: sourceColors['claude-cowork'], firstSeen: 34  },
  { id: 'positioning',       label: 'positioning',        x: tx(34)+16, y: LANE_Y['claude-cowork']+9,r: 6,  color: sourceColors['claude-cowork'], firstSeen: 34  },

  // Between code + cowork: Persona project
  { id: 'Persona project',   label: 'Persona project',   x: tx(37),    y: 185,                      r: 10, color: sourceColors['claude-cowork'], firstSeen: 37  },

  // gmail lane — investor, ops, visa
  { id: 'visa status',       label: 'visa status',       x: tx(52),    y: LANE_Y.gmail - 2,         r: 6,  color: sourceColors.gmail,            firstSeen: 52  },
  { id: 'investor pipeline', label: 'investor pipeline', x: tx(52)+14, y: LANE_Y.gmail + 9,         r: 5,  color: sourceColors.gmail,            firstSeen: 52  },
  { id: 'user feedback',     label: 'user feedback',     x: tx(128),   y: LANE_Y.gmail - 2,         r: 6,  color: sourceColors.gmail,            firstSeen: 128 },
  { id: 'fundraising deck',  label: 'fundraising deck',  x: tx(155),   y: LANE_Y.gmail + 9,         r: 5,  color: sourceColors.gmail,            firstSeen: 155 },

  // chatgpt lane — planning, marketing copy, stress
  { id: 'runway anxiety',    label: 'runway anxiety',    x: tx(66),    y: LANE_Y.chatgpt - 2,       r: 7,  color: sourceColors.chatgpt,          firstSeen: 66  },
  { id: 'content strategy',  label: 'content strategy',  x: tx(95),    y: LANE_Y.chatgpt + 9,       r: 6,  color: sourceColors.chatgpt,          firstSeen: 95  },
  { id: 'pitch script',      label: 'pitch script',      x: tx(145),   y: LANE_Y.chatgpt - 2,       r: 6,  color: sourceColors.chatgpt,          firstSeen: 145 },

  // Between gmail + chatgpt: knowledge layer (cross-source insight — memory research meets product)
  { id: 'knowledge layer',   label: 'knowledge layer',   x: tx(69),    y: 338,                      r: 10, color: '#f59e0b',                     firstSeen: 69, isSynthesis: true },

  // github lane
  { id: 'open source',       label: 'open source',       x: tx(80),    y: LANE_Y.github - 2,        r: 6,  color: sourceColors.github,           firstSeen: 80  },
  { id: 'dev experience',    label: 'dev experience',    x: tx(80)+16, y: LANE_Y.github + 10,       r: 8,  color: sourceColors.github,           firstSeen: 80  },
  { id: 'technical depth',   label: 'technical depth',  x: tx(80)+8,  y: LANE_Y.github + 1,        r: 6,  color: sourceColors.github,           firstSeen: 80  },
  { id: 'API design',        label: 'API design',        x: tx(148),   y: LANE_Y.github - 2,        r: 6,  color: sourceColors.github,           firstSeen: 148 },

  // Floating — cross-source insights
  { id: 'systems thinking',  label: 'systems thinking',  x: tx(83),    y: 420,                      r: 7,  color: sourceColors.github,           firstSeen: 83  },
  // growth loops: reading (web) + GTM (cowork) + content (chatgpt) → doesn't belong to one lane
  { id: 'growth loops',      label: 'growth loops',      x: tx(130),   y: 390,                      r: 8,  color: '#f59e0b',                     firstSeen: 130, isSynthesis: true },

  // Central synthesis node — PERCEPTION SYSTEMS
  { id: 'perception systems', label: 'perception systems', x: tx(115), y: 265,                      r: 14, color: '#f59e0b',                     firstSeen: 115, isSynthesis: true },

  // cowork mid-timeline (t=90) — fills gap in cowork lane
  { id: 'product analytics', label: 'product analytics', x: tx(90),    y: LANE_Y['claude-cowork']-2,r: 6,  color: sourceColors['claude-cowork'], firstSeen: 90  },

  // Late-timeline nodes (t=155–175) — fills top-right of graph
  { id: 'user interviews',   label: 'user interviews',   x: tx(157),   y: LANE_Y['claude-cowork']+9,r: 7,  color: sourceColors['claude-cowork'], firstSeen: 157 },
  { id: 'launch checklist',  label: 'launch checklist',  x: tx(170),   y: LANE_Y['claude-cowork']-2,r: 5,  color: sourceColors['claude-cowork'], firstSeen: 170 },
  { id: 'data pipeline',     label: 'data pipeline',     x: tx(163),   y: LANE_Y['claude-code'] - 2,r: 6,  color: sourceColors['claude-code'],   firstSeen: 163 },
  { id: 'attention economy', label: 'attention economy', x: tx(172),   y: LANE_Y['claude-web'] - 2, r: 6,  color: sourceColors['claude-web'],    firstSeen: 172 },
];

const EDGES: Edge[] = [
  // ── Discovery: source lane → topic ──
  { id: 'ed-cw-hm',   fromId: 'claude-web',    toId: 'human memory',          type: 'discovery', firstSeen: 11,  color: sourceColors['claude-web']    },
  { id: 'ed-cw-sr',   fromId: 'claude-web',    toId: 'spaced repetition',     type: 'discovery', firstSeen: 11,  color: sourceColors['claude-web']    },
  { id: 'ed-cc-ctx',  fromId: 'claude-code',   toId: 'context-engine',        type: 'discovery', firstSeen: 14,  color: sourceColors['claude-code']   },
  { id: 'ed-cc-vs',   fromId: 'claude-code',   toId: 'vector search',         type: 'discovery', firstSeen: 62,  color: sourceColors['claude-code']   },
  { id: 'ed-cw-alma', fromId: 'claude-web',    toId: 'ALMA',                  type: 'discovery', firstSeen: 26,  color: sourceColors['claude-web']    },
  { id: 'ed-cc-alma', fromId: 'claude-code',   toId: 'ALMA',                  type: 'discovery', firstSeen: 26,  color: sourceColors['claude-code']   },
  { id: 'ed-cc-ml',   fromId: 'claude-code',   toId: 'meta-learning',         type: 'discovery', firstSeen: 26,  color: sourceColors['claude-code']   },
  { id: 'ed-cow-gtm', fromId: 'claude-cowork', toId: 'go-to-market',          type: 'discovery', firstSeen: 34,  color: sourceColors['claude-cowork'] },
  { id: 'ed-cow-pos', fromId: 'claude-cowork', toId: 'positioning',           type: 'discovery', firstSeen: 34,  color: sourceColors['claude-cowork'] },
  { id: 'ed-cow-per', fromId: 'claude-cowork', toId: 'Persona project',       type: 'discovery', firstSeen: 37,  color: sourceColors['claude-cowork'] },
  { id: 'ed-gm-vis',  fromId: 'gmail',         toId: 'visa status',           type: 'discovery', firstSeen: 52,  color: sourceColors.gmail            },
  { id: 'ed-gm-inv',  fromId: 'gmail',         toId: 'investor pipeline',     type: 'discovery', firstSeen: 52,  color: sourceColors.gmail            },
  { id: 'ed-cgpt-run',fromId: 'chatgpt',       toId: 'runway anxiety',        type: 'discovery', firstSeen: 66,  color: sourceColors.chatgpt          },
  { id: 'ed-cgpt-kl', fromId: 'chatgpt',       toId: 'knowledge layer',       type: 'discovery', firstSeen: 69,  color: sourceColors.chatgpt          },
  { id: 'ed-cow-pa',  fromId: 'claude-cowork', toId: 'product analytics',     type: 'discovery', firstSeen: 90,  color: sourceColors['claude-cowork'] },
  { id: 'ed-cgpt-cs', fromId: 'chatgpt',       toId: 'content strategy',      type: 'discovery', firstSeen: 95,  color: sourceColors.chatgpt          },
  { id: 'ed-gh-os',   fromId: 'github',        toId: 'open source',           type: 'discovery', firstSeen: 80,  color: sourceColors.github           },
  { id: 'ed-gh-dx',   fromId: 'github',        toId: 'dev experience',        type: 'discovery', firstSeen: 80,  color: sourceColors.github           },
  { id: 'ed-gh-td',   fromId: 'github',        toId: 'technical depth',       type: 'discovery', firstSeen: 80,  color: sourceColors.github           },
  { id: 'ed-gh-sys',  fromId: 'github',        toId: 'systems thinking',      type: 'discovery', firstSeen: 83,  color: sourceColors.github           },
  { id: 'ed-cw-fe',   fromId: 'claude-web',    toId: 'founder essays',        type: 'discovery', firstSeen: 102, color: sourceColors['claude-web']    },
  { id: 'ed-cw-pkm',  fromId: 'claude-web',    toId: 'PKM systems',           type: 'discovery', firstSeen: 102, color: sourceColors['claude-web']    },
  { id: 'ed-gm-uf',   fromId: 'gmail',         toId: 'user feedback',         type: 'discovery', firstSeen: 128, color: sourceColors.gmail            },
  { id: 'ed-cow-gl',  fromId: 'claude-cowork', toId: 'growth loops',          type: 'discovery', firstSeen: 130, color: sourceColors['claude-cowork'] },
  { id: 'ed-cgpt-ps', fromId: 'chatgpt',       toId: 'pitch script',          type: 'discovery', firstSeen: 145, color: sourceColors.chatgpt          },
  { id: 'ed-gh-api',  fromId: 'github',        toId: 'API design',            type: 'discovery', firstSeen: 148, color: sourceColors.github           },
  { id: 'ed-gm-fd',   fromId: 'gmail',         toId: 'fundraising deck',      type: 'discovery', firstSeen: 155, color: sourceColors.gmail            },
  { id: 'ed-cow-ui',  fromId: 'claude-cowork', toId: 'user interviews',       type: 'discovery', firstSeen: 157, color: sourceColors['claude-cowork'] },
  { id: 'ed-cc-dp',   fromId: 'claude-code',   toId: 'data pipeline',         type: 'discovery', firstSeen: 163, color: sourceColors['claude-code']   },
  { id: 'ed-cow-lc',  fromId: 'claude-cowork', toId: 'launch checklist',      type: 'discovery', firstSeen: 170, color: sourceColors['claude-cowork'] },
  { id: 'ed-cw-ae',   fromId: 'claude-web',    toId: 'attention economy',     type: 'discovery', firstSeen: 172, color: sourceColors['claude-web']    },

  // ── Cross-ref: arcs reaching back in time (DAG memory effect) ──
  // perception systems fan-out (t=115)
  { id: 'xr-ps-hm',   fromId: 'perception systems', toId: 'human memory',     type: 'cross-ref', firstSeen: 115, color: '#f59e0b' },
  { id: 'xr-ps-alma', fromId: 'perception systems', toId: 'ALMA',             type: 'cross-ref', firstSeen: 115, color: '#f59e0b' },
  { id: 'xr-ps-ctx',  fromId: 'perception systems', toId: 'context-engine',   type: 'cross-ref', firstSeen: 115, color: '#a78bfa' },
  { id: 'xr-ps-gtm',  fromId: 'perception systems', toId: 'go-to-market',     type: 'cross-ref', firstSeen: 115, color: '#f59e0b' },
  { id: 'xr-ps-td',   fromId: 'perception systems', toId: 'technical depth',  type: 'cross-ref', firstSeen: 115, color: '#f59e0b' },

  // early concept links — progressive t=14 → t=102
  { id: 'xr-hm-ctx',   fromId: 'human memory',      toId: 'context-engine',    type: 'cross-ref', firstSeen: 14,  color: '#a78bfa' },
  { id: 'xr-alma-ml',  fromId: 'ALMA',              toId: 'meta-learning',     type: 'cross-ref', firstSeen: 26,  color: '#a78bfa' },
  { id: 'xr-alma-hm',  fromId: 'ALMA',              toId: 'human memory',      type: 'cross-ref', firstSeen: 26,  color: '#a78bfa' },
  { id: 'xr-alma-ctx', fromId: 'ALMA',              toId: 'context-engine',    type: 'cross-ref', firstSeen: 26,  color: '#a78bfa' },
  { id: 'xr-gtm-pos',  fromId: 'go-to-market',      toId: 'positioning',       type: 'cross-ref', firstSeen: 34,  color: sourceColors['claude-cowork'] },
  { id: 'xr-per-alma', fromId: 'Persona project',   toId: 'ALMA',              type: 'cross-ref', firstSeen: 37,  color: sourceColors['claude-cowork'] },
  { id: 'xr-per-ctx',  fromId: 'Persona project',   toId: 'context-engine',    type: 'cross-ref', firstSeen: 37,  color: '#a78bfa' },
  { id: 'xr-per-gtm',  fromId: 'Persona project',   toId: 'go-to-market',      type: 'cross-ref', firstSeen: 37,  color: sourceColors['claude-cowork'] },
  { id: 'xr-vis-gtm',  fromId: 'visa status',       toId: 'go-to-market',      type: 'cross-ref', firstSeen: 52,  color: '#a78bfa' },
  { id: 'xr-inv-gtm',  fromId: 'investor pipeline', toId: 'go-to-market',      type: 'cross-ref', firstSeen: 52,  color: '#a78bfa' },
  { id: 'xr-hm-sr',    fromId: 'human memory',      toId: 'spaced repetition', type: 'cross-ref', firstSeen: 55,  color: sourceColors['claude-web'] },
  { id: 'xr-run-vis',  fromId: 'runway anxiety',     toId: 'visa status',       type: 'cross-ref', firstSeen: 66,  color: '#a78bfa' },
  { id: 'xr-run-inv',  fromId: 'runway anxiety',     toId: 'investor pipeline', type: 'cross-ref', firstSeen: 66,  color: '#a78bfa' },
  { id: 'xr-vs-ctx',   fromId: 'vector search',      toId: 'context-engine',    type: 'cross-ref', firstSeen: 62,  color: sourceColors['claude-code'] },
  { id: 'xr-cs-gtm',   fromId: 'content strategy',  toId: 'go-to-market',      type: 'cross-ref', firstSeen: 95,  color: '#a78bfa' },
  { id: 'xr-pa-gtm',   fromId: 'product analytics', toId: 'go-to-market',      type: 'cross-ref', firstSeen: 90,  color: sourceColors['claude-cowork'] },
  { id: 'xr-pa-per',   fromId: 'product analytics', toId: 'Persona project',   type: 'cross-ref', firstSeen: 90,  color: sourceColors['claude-cowork'] },
  { id: 'xr-kl-alma',  fromId: 'knowledge layer',   toId: 'ALMA',              type: 'cross-ref', firstSeen: 69,  color: '#f59e0b' },
  { id: 'xr-kl-per',   fromId: 'knowledge layer',   toId: 'Persona project',   type: 'cross-ref', firstSeen: 69,  color: '#f59e0b' },
  { id: 'xr-os-td',    fromId: 'open source',        toId: 'technical depth',   type: 'cross-ref', firstSeen: 80,  color: sourceColors.github },
  { id: 'xr-sys-gtm',  fromId: 'systems thinking',  toId: 'go-to-market',      type: 'cross-ref', firstSeen: 83,  color: '#a78bfa' },
  { id: 'xr-sys-alma', fromId: 'systems thinking',  toId: 'ALMA',              type: 'cross-ref', firstSeen: 83,  color: '#a78bfa' },
  { id: 'xr-td-ctx',   fromId: 'technical depth',   toId: 'context-engine',    type: 'cross-ref', firstSeen: 83,  color: '#a78bfa' },
  { id: 'xr-fe-hm',    fromId: 'founder essays',    toId: 'human memory',      type: 'cross-ref', firstSeen: 102, color: sourceColors['claude-web'] },
  { id: 'xr-pkm-sr',   fromId: 'PKM systems',       toId: 'spaced repetition', type: 'cross-ref', firstSeen: 102, color: sourceColors['claude-web'] },
  { id: 'xr-pkm-kl',   fromId: 'PKM systems',       toId: 'knowledge layer',   type: 'cross-ref', firstSeen: 102, color: '#a78bfa' },

  // knowledge layer late synthesis (t=148)
  { id: 'xr-kl-ps',    fromId: 'knowledge layer',   toId: 'perception systems',type: 'cross-ref', firstSeen: 148, color: '#f59e0b' },

  // growth loops cross-refs (t=130) — ties marketing + product + content together
  { id: 'xr-gl-gtm',   fromId: 'growth loops',      toId: 'go-to-market',      type: 'cross-ref', firstSeen: 130, color: sourceColors['claude-cowork'] },
  { id: 'xr-gl-cs',    fromId: 'growth loops',      toId: 'content strategy',  type: 'cross-ref', firstSeen: 130, color: '#a78bfa' },

  // ALMA cross-ref (t=161) — tri-source
  { id: 'xr-alma-kl',  fromId: 'ALMA',              toId: 'knowledge layer',   type: 'cross-ref', firstSeen: 161, color: '#a78bfa' },
  { id: 'xr-alma-ps',  fromId: 'ALMA',              toId: 'perception systems',type: 'cross-ref', firstSeen: 161, color: '#a78bfa' },

  // late cross-refs — new nodes reach back to earlier concepts
  { id: 'xr-uf-per',   fromId: 'user feedback',     toId: 'Persona project',   type: 'cross-ref', firstSeen: 128, color: sourceColors.gmail            },
  { id: 'xr-uf-kl',    fromId: 'user feedback',     toId: 'knowledge layer',   type: 'cross-ref', firstSeen: 128, color: '#a78bfa'                     },
  { id: 'xr-ps-gl',    fromId: 'pitch script',      toId: 'go-to-market',      type: 'cross-ref', firstSeen: 145, color: sourceColors.chatgpt          },
  { id: 'xr-ps-inv',   fromId: 'pitch script',      toId: 'investor pipeline', type: 'cross-ref', firstSeen: 145, color: '#a78bfa'                     },
  { id: 'xr-api-ctx',  fromId: 'API design',        toId: 'context-engine',    type: 'cross-ref', firstSeen: 148, color: sourceColors.github           },
  { id: 'xr-api-dx',   fromId: 'API design',        toId: 'dev experience',    type: 'cross-ref', firstSeen: 148, color: sourceColors.github           },
  { id: 'xr-fd-inv',   fromId: 'fundraising deck',  toId: 'investor pipeline', type: 'cross-ref', firstSeen: 155, color: sourceColors.gmail            },
  { id: 'xr-fd-gl',    fromId: 'fundraising deck',  toId: 'growth loops',      type: 'cross-ref', firstSeen: 155, color: '#a78bfa'                     },
  { id: 'xr-ui-per',   fromId: 'user interviews',   toId: 'Persona project',   type: 'cross-ref', firstSeen: 157, color: sourceColors['claude-cowork'] },
  { id: 'xr-ui-pa',    fromId: 'user interviews',   toId: 'product analytics', type: 'cross-ref', firstSeen: 157, color: sourceColors['claude-cowork'] },
  { id: 'xr-dp-ctx',   fromId: 'data pipeline',     toId: 'context-engine',    type: 'cross-ref', firstSeen: 163, color: sourceColors['claude-code']   },
  { id: 'xr-dp-vs',    fromId: 'data pipeline',     toId: 'vector search',     type: 'cross-ref', firstSeen: 163, color: sourceColors['claude-code']   },
  { id: 'xr-lc-gtm',   fromId: 'launch checklist',  toId: 'go-to-market',      type: 'cross-ref', firstSeen: 170, color: sourceColors['claude-cowork'] },
  { id: 'xr-lc-ui',    fromId: 'launch checklist',  toId: 'user interviews',   type: 'cross-ref', firstSeen: 170, color: sourceColors['claude-cowork'] },
  { id: 'xr-ae-hm',    fromId: 'attention economy', toId: 'human memory',      type: 'cross-ref', firstSeen: 172, color: sourceColors['claude-web']    },
  { id: 'xr-ae-kl',    fromId: 'attention economy', toId: 'knowledge layer',   type: 'cross-ref', firstSeen: 172, color: '#a78bfa'                     },

  // ── Synthesis edges (glowing, final connections) ──
  { id: 'syn-ps-hm',    fromId: 'perception systems',toId: 'human memory',      type: 'synthesis', firstSeen: 115, color: '#f59e0b' },
  { id: 'syn-ps-alma',  fromId: 'perception systems',toId: 'ALMA',              type: 'synthesis', firstSeen: 115, color: '#f59e0b' },
  { id: 'syn-kl-per',   fromId: 'knowledge layer',   toId: 'Persona project',   type: 'synthesis', firstSeen: 148, color: '#f59e0b' },
  { id: 'syn-kl-ps',    fromId: 'knowledge layer',   toId: 'perception systems',type: 'synthesis', firstSeen: 169, color: '#f59e0b' },
  { id: 'syn-alma-ps',  fromId: 'ALMA',              toId: 'perception systems',type: 'synthesis', firstSeen: 169, color: '#a78bfa' },
];

// ── Path helpers ──────────────────────────────────────────────────────────────

function edgePath(fromNode: Node | { x: number; y: number }, toNode: Node, type: Edge['type']): string {
  const fx = fromNode.x, fy = fromNode.y;
  const tx2 = toNode.x, ty = toNode.y;

  if (type === 'discovery') {
    // Short straight line from lane Y to node
    return `M ${fx} ${fy} L ${tx2} ${ty}`;
  }

  // Cross-ref & synthesis: bow downward for backward-in-time arcs (memory reaching back),
  // bow upward for forward-in-time arcs (anticipating future connections).
  const goingBack = fx > tx2;
  const span = Math.abs(fx - tx2);
  const bow = Math.min(span * 0.14, 55);

  if (goingBack) {
    // Arc bows BELOW the nodes — visual "memory reach-back"
    const midX = (fx + tx2) / 2;
    const bowY = Math.max(fy, ty) + bow;
    return `M ${fx} ${fy} C ${fx + 10} ${bowY}, ${tx2 - 10} ${bowY}, ${tx2} ${ty}`;
  } else {
    // Arc bows ABOVE for forward links
    const midX = (fx + tx2) / 2;
    const bowY = Math.min(fy, ty) - bow * 0.6;
    return `M ${fx} ${fy} C ${fx + 10} ${bowY}, ${tx2 - 10} ${bowY}, ${tx2} ${ty}`;
  }
}

// ── Sub-components ────────────────────────────────────────────────────────────

function EdgePath({ edge, nodeMap }: { edge: Edge; nodeMap: Map<string, Node> }) {
  const toNode = nodeMap.get(edge.toId);
  if (!toNode) return null;

  // fromId may be a source name or a node id
  const fromNode = nodeMap.get(edge.fromId);
  if (!fromNode) {
    // It's a source lane — use lane Y at the node's x position
    const laneY = LANE_Y[edge.fromId];
    if (laneY === undefined) return null;
    const fakeSrc = { x: toNode.x, y: laneY };
    const d = edgePath(fakeSrc, toNode, edge.type);
    return (
      <motion.path d={d} fill="none" stroke={edge.color}
        strokeWidth={0.8} strokeOpacity={0.22} strokeDasharray="3 4"
        initial={{ pathLength: 0, opacity: 0 }}
        animate={{ pathLength: 1, opacity: 1 }}
        transition={{ duration: 0.5, ease: 'easeOut' }} />
    );
  }

  const isSynth = edge.type === 'synthesis';
  const isCross = edge.type === 'cross-ref';
  const d = edgePath(fromNode, toNode, edge.type);

  return (
    <motion.path
      d={d}
      fill="none"
      stroke={edge.color}
      strokeWidth={isSynth ? 2.5 : isCross ? 1.5 : 0.8}
      strokeOpacity={isSynth ? 0.75 : isCross ? 0.45 : 0.25}
      strokeDasharray={edge.type === 'discovery' ? '3 4' : 'none'}
      filter={isSynth ? 'url(#glow)' : undefined}
      initial={{ pathLength: 0, opacity: 0 }}
      animate={{ pathLength: 1, opacity: 1 }}
      transition={{ duration: isSynth ? 1.0 : isCross ? 0.65 : 0.4, ease: 'easeOut' }}
    />
  );
}

function TopicNode({ node }: { node: Node }) {
  return (
    <motion.g
      initial={{ scale: 0, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ duration: 0.35, type: 'spring', stiffness: 220, damping: 18 }}
    >
      {node.isSynthesis && (
        <circle cx={node.x} cy={node.y} r={node.r + 6}
          fill="none" stroke={node.color} strokeWidth={1}
          strokeOpacity={0.18} filter="url(#glow)" />
      )}
      <circle cx={node.x} cy={node.y} r={node.r}
        fill={node.color}
        fillOpacity={node.isSynthesis ? 0.2 : 0.1}
        stroke={node.color}
        strokeWidth={node.isSynthesis ? 2 : 1.5}
        strokeOpacity={node.isSynthesis ? 1 : 0.6}
      />
      <text
        x={node.x}
        y={node.y + node.r + 9}
        textAnchor="middle"
        fontSize={node.isSynthesis ? 8.5 : 7.5}
        fontWeight={node.isSynthesis ? '600' : '400'}
        fill={node.isSynthesis ? node.color : colors.dim}
      >
        {node.label}
      </text>
    </motion.g>
  );
}

function LaneEventDot({ ev, isActive }: { ev: LaneEvent; isActive: boolean }) {
  const color = sourceColors[ev.source];
  return (
    <motion.circle
      cx={ev.x}
      cy={LANE_Y[ev.source]}
      r={ev.isRevisit ? 3 : 4}
      fill={color}
      fillOpacity={isActive ? (ev.isRevisit ? 0.5 : 0.85) : 0}
      initial={{ scale: 0 }}
      animate={{ scale: 1 }}
      transition={{ duration: 0.25, type: 'spring' }}
    />
  );
}

// ── Main component ────────────────────────────────────────────────────────────

interface KnowledgeGraphProps {
  currentTime: number;
}

export default function KnowledgeGraph({ currentTime }: KnowledgeGraphProps) {
  const nodeMap = useMemo(() => new Map(NODES.map((n) => [n.id, n])), []);

  const visibleNodes  = useMemo(() => NODES.filter((n) => n.firstSeen <= currentTime), [currentTime]);
  const visibleEdges  = useMemo(() => EDGES.filter((e) => e.firstSeen <= currentTime), [currentTime]);
  const visibleLaneEvts = useMemo(() => LANE_EVENTS.filter((e) => e.firstSeen <= currentTime), [currentTime]);

  const activeSources = useMemo(
    () => new Set(visibleLaneEvts.map((e) => e.source)),
    [visibleLaneEvts]
  );

  // Active lane fill: how far along the timeline the lane is "lit" up to
  const currentX = tx(Math.min(currentTime, DURATION));

  return (
    <div className="w-full">
      <svg viewBox={`0 0 ${SVG_W} ${SVG_H}`} className="w-full" style={{ backgroundColor: colors.bg }}>
        <defs>
          <filter id="glow" x="-60%" y="-60%" width="220%" height="220%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="4" result="blur" />
            <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
          <filter id="softglow" x="-40%" y="-40%" width="180%" height="180%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="2" result="blur" />
            <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
        </defs>

        {/* Source lane lines + labels */}
        {SOURCES.map((src) => {
          const color = sourceColors[src];
          const y = LANE_Y[src];
          const isActive = activeSources.has(src);
          return (
            <g key={src}>
              {/* Dim full lane */}
              <line x1={LANE_START_X} y1={y} x2={LANE_END_X} y2={y}
                stroke={color} strokeWidth={1} strokeOpacity={0.12} />
              {/* Active portion up to current time */}
              {isActive && (
                <line x1={LANE_START_X} y1={y} x2={Math.min(currentX, LANE_END_X)} y2={y}
                  stroke={color} strokeWidth={1.5} strokeOpacity={0.35} />
              )}
              {/* Source label */}
              <text x={LANE_START_X - 8} y={y + 1}
                textAnchor="end" dominantBaseline="middle"
                fontSize="9" fontWeight={isActive ? '600' : '400'}
                fill={isActive ? color : colors.muted}>
                {sourceLabels[src]}
              </text>
              {/* Active dot at lane start */}
              {isActive && (
                <circle cx={LANE_START_X} cy={y} r={3} fill={color} fillOpacity={0.6} />
              )}
            </g>
          );
        })}

        {/* Current time cursor */}
        <line x1={currentX} y1={40} x2={currentX} y2={SVG_H - 20}
          stroke={colors.accent} strokeWidth={1} strokeOpacity={0.2} strokeDasharray="3 3" />

        {/* Lane browse events (dots on lanes) */}
        <AnimatePresence>
          {visibleLaneEvts.map((ev) => (
            <LaneEventDot key={`${ev.source}-${ev.firstSeen}`} ev={ev} isActive={true} />
          ))}
        </AnimatePresence>

        {/* Edges: discovery → cross-ref → synthesis (layered) */}
        {(['discovery', 'cross-ref', 'synthesis'] as const).map((etype) =>
          visibleEdges.filter((e) => e.type === etype).map((edge) => (
            <EdgePath key={edge.id} edge={edge} nodeMap={nodeMap} />
          ))
        )}

        {/* Topic nodes (on top) */}
        <AnimatePresence>
          {visibleNodes.map((node) => (
            <TopicNode key={node.id} node={node} />
          ))}
        </AnimatePresence>
      </svg>
    </div>
  );
}
