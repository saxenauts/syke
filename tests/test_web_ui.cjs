const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../syke/runtime/web/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/\bboot\(\);\s*$/, '');

function page() {
  const elements = new Map();
  const frames = [];
  let styleReads = 0;
  let fetchCount = 0;
  let fetchResponse = async () => ({ events: [] });
  const classes = () => {
    const names = new Set();
    return {
      add: name => names.add(name),
      remove: name => names.delete(name),
      contains: name => names.has(name),
      toggle: (name, on) => on ? names.add(name) : names.delete(name),
    };
  };
  const element = key => {
    if (!elements.has(key)) {
      const el = {
        innerHTML: '', textContent: '', style: {}, dataset: {}, classList: classes(),
        scrollTop: 0, clientWidth: 900, clientHeight: 78, parentElement: { clientWidth: 900 },
        addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
        contains() { return true; }, setAttribute() {},
      };
      elements.set(key, el);
    }
    return elements.get(key);
  };
  const ctx = { setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {}, lineTo() {},
    stroke() {}, fillText() {}, fillRect() {}, strokeRect() {} };
  const canvas = element('#timeline-canvas');
  canvas.width = 900;
  canvas.height = 78;
  canvas.getContext = () => ctx;
  const document = {
    documentElement: element('html'), body: element('body'),
    querySelector: element, querySelectorAll: () => [],
    getElementById: id => element(`#${id}`),
  };
  const window = {
    devicePixelRatio: 1, addEventListener() {},
    matchMedia: () => ({ matches: false }),
  };
  const context = vm.createContext({
    window, document, localStorage: { getItem: () => null, setItem() {} },
    performance: { now: () => 1000 }, AbortController, CSS: { escape: s => s },
    setTimeout: () => 1, clearTimeout() {}, setInterval() {},
    requestAnimationFrame: callback => { frames.push(callback); return frames.length; },
    getComputedStyle: () => { styleReads++; return { getPropertyValue: () => '#fff' }; },
    fetch: async url => { fetchCount++; return { ok: true, json: async () => fetchResponse(url) }; },
  });
  vm.runInContext(script, context);
  return {
    context, element, frames,
    get styleReads() { return styleReads; },
    get fetchCount() { return fetchCount; },
    respondWith: fn => { fetchResponse = fn; },
    run: source => vm.runInContext(source, context),
  };
}

function graph(content = 'A') {
  return { db_present: true, as_of: new Date().toISOString(),
    memories: [{ id: 'memory-1', content, created_at: '2026-10-01T00:00:00Z' }], links: [] };
}

test('graph polls preserve an open memory and last good content', async () => {
  const p = page();
  p.run('S.tab = "graph"');
  p.respondWith(async () => graph());
  await p.run('loadCurrentGraph()');
  const grid = p.element('#mem-grid');
  const inspect = p.element('#mem-inspect');
  const block = { dataset: { id: 'memory-1' }, classList: { add() {}, remove() {} } };
  grid.querySelectorAll = selector => selector === '.mem-block' && p.run('S.currentGraph.memories.some(m => m.id === "memory-1")') ? [block] : [];
  grid.onclick({ target: { closest: () => block } });
  inspect.scrollTop = 24;
  grid.scrollTop = 9;
  const originalGrid = grid.innerHTML;
  p.respondWith(async () => graph());
  await p.run('loadCurrentGraph()');
  assert.equal(grid.innerHTML, originalGrid);
  assert.equal(inspect.scrollTop, 24);
  assert.equal(inspect.classList.contains('open'), true);

  p.respondWith(async () => graph('B'));
  await p.run('loadCurrentGraph()');
  assert.match(inspect.innerHTML, /B/);
  assert.equal(inspect.scrollTop, 24);
  assert.equal(grid.scrollTop, 9);

  p.respondWith(async () => { throw new Error('temporary'); });
  await p.run('loadCurrentGraph()');
  assert.match(inspect.innerHTML, /B/);
  assert.equal(inspect.classList.contains('open'), true);
  assert.match(p.element('#graph-refresh-status').textContent, /showing last loaded graph/);

  p.respondWith(async () => ({ ...graph(), memories: [] }));
  await p.run('loadCurrentGraph()');
  assert.match(inspect.innerHTML, /no longer in the current graph/);
  assert.equal(inspect.classList.contains('open'), true);
});

test('an older graph poll cannot overwrite a newer response', async () => {
  const p = page();
  let first, second;
  let calls = 0;
  p.respondWith(() => new Promise(resolve => {
    if (calls++ === 0) first = resolve;
    else second = resolve;
  }));
  const older = p.run('loadCurrentGraph()');
  const newer = p.run('loadCurrentGraph()');
  await new Promise(resolve => setImmediate(resolve));
  second(graph('new'));
  await newer;
  first(graph('old'));
  await older;
  assert.equal(p.run('S.currentGraph.memories[0].content'), 'new');
});

test('cached detail supersedes a queued step and paints once per frame', () => {
  const p = page();
  p.run('S.tab = "memex"; S.events = [{kind:"cycle",id:"1"},{kind:"cycle",id:"2"},{kind:"cycle",id:"3"}]; S.selected = S.events[0]; S.selectedIdx = 0');
  p.run('requestDetail(S.events[1], {coalesce:true,direction:1})');
  assert.equal(p.run('S.queuedDetail.ev.id'), '2');
  assert.equal(p.run('S.detailPrefetching.size'), 0);
  p.run('S.detailCache.set("memex:cycle:3", {kind:"cycle",summary:true,cycle:{id:"3"},memex:{content:""},prev_memex:{content:""}}); requestDetail(S.events[2], {coalesce:true})');
  assert.equal(p.run('S.detail'), null);
  assert.equal(p.run('S.queuedDetail.ev.id'), '3');
  p.run('flushQueuedDetail()');
  assert.equal(p.run('S.selected.id'), '3');
  assert.equal(p.run('S.detail.cycle.id'), '3');
  assert.equal(p.frames.length, 1);
  p.frames.shift()();
  assert.equal(p.styleReads, 1);
  p.run('drawTimeline(); drawTimeline()');
  assert.equal(p.frames.length, 1);
});

test('timeline refresh rebinds a queued final detail by event identity', async () => {
  const p = page();
  p.run('S.tab = "memex"; S.events = [{kind:"cycle",id:"1"},{kind:"cycle",id:"2"}]; S.selected = S.events[0]; S.selectedIdx = 0');
  p.run('requestDetail(S.events[1], {coalesce:true})');
  p.run('applyTimelinePayload({events:[{kind:"cycle",id:"1"},{kind:"cycle",id:"2"}],window:{start:"2026-10-01T00:00:00Z",end:"2026-10-02T00:00:00Z"}})');
  assert.equal(p.run('S.queuedDetail.ev === S.events[1]'), true);
  p.respondWith(async () => ({ kind: 'cycle', summary: true, cycle: { id: '2' }, memex: { content: '' }, prev_memex: { content: '' } }));
  p.run('flushQueuedDetail()');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(p.run('S.selected.id'), '2');
  assert.equal(p.run('S.detail.cycle.id'), '2');
});

test('graph navigation changes selection without fetching timeline detail', () => {
  const p = page();
  p.run('S.tab = "graph"; S.graphRendered = true; S.events = [{kind:"cycle",id:"1"},{kind:"cycle",id:"2"}]; S.selected = S.events[0]; S.selectedIdx = 0');
  p.run('requestDetail(S.events[1], {coalesce:true,direction:1})');
  assert.equal(p.run('S.selected.id'), '2');
  assert.equal(p.run('S.queuedDetail'), null);
  assert.equal(p.run('S.detailPrefetching.size'), 0);
});

test('a held-key sequence waits for the final detail', async () => {
  const p = page();
  p.run('S.tab = "memex"; S.events = Array.from({length:100}, (_, i) => ({kind:"cycle",id:String(i)})); S.selected = S.events[0]; S.selectedIdx = 0');
  p.respondWith(async () => ({ kind: 'cycle', summary: true, cycle: { id: '99' }, memex: { content: '' }, prev_memex: { content: '' } }));
  p.run('for (let i = 1; i < 100; i++) requestDetail(S.events[i], {coalesce:true,direction:1})');
  assert.equal(p.fetchCount, 0);
  assert.equal(p.frames.length, 1);
  assert.equal(p.run('S.selected.id'), '99');
  p.run('flushQueuedDetail()');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(p.fetchCount, 1);
  assert.equal(p.run('S.detail.cycle.id'), '99');
});
