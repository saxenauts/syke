const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../syke/runtime/web/index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/\bboot\(\);\s*$/, '');
const decode = value => value.replace(/&(amp|lt|gt|quot|#39);/g, (_, name) => ({ amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" })[name]);
const escape = value => value.replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);

function page() {
  let document;
  const listeners = target => {
    target.listeners = new Map();
    target.addEventListener = (type, handler, capture = false) => {
      const handlers = target.listeners.get(type) || [];
      handlers.push({ handler, capture });
      target.listeners.set(type, handlers);
    };
    return target;
  };
  class Node {
    constructor(tagName, text = '') {
      this.tagName = tagName;
      this.nodeType = tagName === '#text' ? 3 : tagName === '#fragment' ? 11 : 1;
      this.nodeValue = this.nodeType === 3 ? text : null;
      this.childNodes = [];
      this.parentNode = null;
      this.attributes = {};
      this.dataset = {};
      this.style = {};
      this.className = '';
      this.scrollTop = 0;
      this.scrollLeft = 0;
      this.clientWidth = 900;
      this.clientHeight = 300;
      this.scrollHeight = 10000;
      listeners(this);
      this.classList = {
        contains: name => this.className.split(/\s+/).includes(name),
        add: name => { this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), name])].join(' '); },
        remove: name => { this.className = this.className.split(/\s+/).filter(value => value !== name).join(' '); },
        toggle: (name, force) => {
          const on = force === undefined ? !this.classList.contains(name) : force;
          this.classList[on ? 'add' : 'remove'](name);
          return on;
        },
      };
    }
    get parentElement() { return this.parentNode; }
    get isConnected() { return this === document.documentElement || !!this.parentNode?.isConnected; }
    get textContent() { return this.nodeType === 3 ? this.nodeValue : this.childNodes.map(child => child.textContent).join(''); }
    set textContent(value) { this.childNodes.forEach(child => { child.parentNode = null; }); this.childNodes = []; this.appendChild(new Node('#text', String(value))); }
    get innerHTML() {
      return this.childNodes.map(child => child.nodeType === 3 ? escape(child.nodeValue) : `<${child.tagName.toLowerCase()}${Object.entries({ ...child.attributes, ...(child.className ? { class: child.className } : {}) }).map(([key, value]) => ` ${key}="${escape(value)}"`).join('')}>${child.innerHTML}</${child.tagName.toLowerCase()}>`).join('');
    }
    set innerHTML(value) {
      this.childNodes.forEach(child => { child.parentNode = null; });
      this.childNodes = [];
      const stack = [this];
      for (const token of value.match(/<[^>]+>|[^<]+/g) || []) {
        if (token.startsWith('</')) { stack.pop(); continue; }
        if (token.startsWith('<')) {
          if (token.startsWith('<!--')) continue;
          const tagName = token.match(/^<([\w-]+)/)[1].toUpperCase();
          const child = new Node(tagName);
          for (const match of token.matchAll(/([\w-]+)(?:="([^"]*)")?/g)) {
            if (match.index === 1) continue;
            child.setAttribute(match[1], decode(match[2] || ''));
          }
          stack.at(-1).appendChild(child);
          if (!['BR', 'HR', 'INPUT', 'META', 'LINK'].includes(tagName)) stack.push(child);
        } else stack.at(-1).appendChild(new Node('#text', decode(token)));
      }
    }
    appendChild(child) {
      if (child.nodeType === 11) { [...child.childNodes].forEach(node => this.appendChild(node)); return child; }
      if (child.parentNode) child.parentNode.childNodes.splice(child.parentNode.childNodes.indexOf(child), 1);
      child.parentNode = this;
      this.childNodes.push(child);
      return child;
    }
    replaceWith(replacement) {
      const parent = this.parentNode;
      const index = parent.childNodes.indexOf(this);
      const nodes = replacement.nodeType === 11 ? [...replacement.childNodes] : [replacement];
      nodes.forEach(node => { node.parentNode = parent; });
      parent.childNodes.splice(index, 1, ...nodes);
      this.parentNode = null;
    }
    normalize() {
      this.childNodes.forEach(child => child.normalize());
      for (let index = this.childNodes.length - 1; index > 0; index--) {
        const child = this.childNodes[index];
        const previous = this.childNodes[index - 1];
        if (child.nodeType === 3 && previous.nodeType === 3) {
          previous.nodeValue += child.nodeValue;
          this.childNodes.splice(index, 1);
          child.parentNode = null;
        }
      }
    }
    setAttribute(name, value) {
      this.attributes[name] = String(value);
      if (name === 'class') this.className = value;
      if (name === 'hidden') this.hidden = true;
      if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, character) => character.toUpperCase())] = value;
    }
    removeAttribute(name) { delete this.attributes[name]; }
    matches(selector) {
      if (selector.includes(',')) return selector.split(',').some(part => this.matches(part.trim()));
      if (selector === '*') return this.nodeType === 1;
      const id = selector.match(/#([\w-]+)/);
      if (id && this.attributes.id !== id[1]) return false;
      for (const match of selector.matchAll(/\.([\w-]+)/g)) if (!this.classList.contains(match[1])) return false;
      for (const match of selector.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)) {
        const value = match[1].startsWith('data-') ? this.dataset[match[1].slice(5).replace(/-([a-z])/g, (_, character) => character.toUpperCase())] : this.attributes[match[1]];
        if (value === undefined || (match[2] !== undefined && value !== match[2])) return false;
      }
      return this.nodeType === 1;
    }
    querySelectorAll(selector) { return this.childNodes.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    closest(selector) { return this.matches(selector) ? this : this.parentNode?.closest(selector) || null; }
    contains(node) { return this === node || this.childNodes.some(child => child.contains(node)); }
    focus() { document.activeElement = this; }
  }
  document = {
    createTextNode: value => new Node('#text', value),
    createElement: tag => new Node(tag.toUpperCase()),
    createDocumentFragment: () => new Node('#fragment'),
    querySelector: selector => document.documentElement.querySelector(selector),
    querySelectorAll: selector => document.documentElement.querySelectorAll(selector),
    getElementById: id => document.querySelector(`#${id}`),
  };
  document.documentElement = new Node('HTML');
  document.body = document.documentElement.appendChild(new Node('BODY'));
  document.body.innerHTML = html.match(/<body>([\s\S]*?)<script>/)[1];
  document.activeElement = document.body;
  const window = listeners({ devicePixelRatio: 1, matchMedia: () => ({ matches: false }), getSelection: () => ({ isCollapsed: !selected }) });
  let selected = false;
  let response = () => ({ memories: [], links: [], db_present: true });
  let fetchCount = 0;
  document.querySelector('#timeline-canvas').getContext = () => ({ setTransform() {}, clearRect() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, fillText() {}, fillRect() {}, strokeRect() {} });
  const context = vm.createContext({ window, document, CSS: { escape: value => value }, AbortController,
    localStorage: { getItem: () => null, setItem() {} }, performance: { now: () => 1000 },
    setTimeout: () => 1, clearTimeout() {}, setInterval() {}, clearInterval() {}, requestAnimationFrame: () => 1,
    getComputedStyle: () => ({ getPropertyValue: () => '#fff' }),
    fetch: async url => { fetchCount++; return { ok: true, json: async () => response(url) }; },
  });
  vm.runInContext(script, context);
  const dispatch = (target, type, options = {}) => {
    const event = { target, type, key: '', detail: 1, ...options, prevented: false, stopped: false,
      preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; } };
    const route = [];
    for (let node = target; node; node = node.parentNode) route.push(node);
    route.push(window);
    for (const node of [...route].reverse()) {
      for (const listener of node.listeners.get(type) || []) if (listener.capture) listener.handler(event);
      if (event.stopped) return event;
    }
    for (const node of route) {
      for (const listener of node.listeners.get(type) || []) if (!listener.capture) listener.handler(event);
      if (node[`on${type}`]) node[`on${type}`](event);
      if (event.stopped) break;
    }
    return event;
  };
  return {
    run: code => vm.runInContext(code, context), element: selector => document.querySelector(selector), document, dispatch,
    respondWith: callback => { response = callback; }, select: value => { selected = value; },
    get fetchCount() { return fetchCount; },
    references: selector => document.querySelector(selector).querySelectorAll('.memory-reference'),
  };
}

function graph(memories, links = []) {
  return { db_present: true, as_of: '2026-10-23T00:00:00Z', memories: memories.map(memory => ({ created_at: '2026-10-01T00:00:00Z', ...memory })), links };
}

async function loadGraph(page, memories, links = []) {
  page.respondWith(() => graph(memories, links));
  await page.run('loadCurrentGraph()');
}

test('literal full IDs use Unicode boundaries, longest matches, and path and URL exclusions', () => {
  const browser = page();
  browser.run('S.memoryMatcher = compileMemoryMatcher(["memory-a", "memory-a-long", "memory.v1", "mémoire-α", "folder/memory-a", "a+b", "<tag>", "ABC", "𐐀-memory", "https://known.test/memory"])');
  const cases = [
    ['memory-a memory-a-long', ['memory-a', 'memory-a-long']],
    ['(memory-a). "memory.v1", mémoire-α; a+b <tag> ABC', ['memory-a', 'memory.v1', 'mémoire-α', 'a+b', '<tag>', 'ABC']],
    ['folder/memory-a. https://known.test/memory', ['folder/memory-a', 'https://known.test/memory']],
    ['memory-a.md memory-a..md prefix.memory-a prefix..memory-a memory-a-longer MEMORY-A abc unknown-uuid 67e8d15a-73f5-456f-b069-d253686170f1', []],
    ['é memory-aé memory-a\u0301 _memory-a memory-a_ memory-a- 𐐀memory-a memory-a𐐀', []],
    ['mémoire-α me\u0301moire-α 𐐀-memory', ['mémoire-α', '𐐀-memory']],
    ['https://example.test/memory-a /memory-a dir/memory-a?x=1 memory-a/path file:///memory-a C:\\memory-a memory-a?query=1 memory-a#fragment mailto:memory-a@example.test', []],
    ['label/path→memory-a+memory-a-long; [memory-a] {memory.v1}', ['memory-a', 'memory-a-long', 'memory-a', 'memory.v1']],
  ];
  for (const [text, expected] of cases) {
    assert.deepEqual(JSON.parse(browser.run(`JSON.stringify(findMemoryReferences(${JSON.stringify(text)}).map(reference => reference.id))`)), expected, text);
  }
});

test('URL and filename context survives punctuation around an otherwise exact ID', () => {
  const browser = page();
  browser.run('S.memoryMatcher = compileMemoryMatcher(["memory-a", "folder/memory-a", "https://known.test/(memory-a)"])');
  const rejected = [
    'https://example.test/(memory-a)',
    'https://example.test/!memory-a',
    'https://example.test/?id=(memory-a)',
    'dir/(memory-a).md',
    '(memory-a).md',
    '[memory-a].json',
    '«memory-a».txt',
  ];
  for (const punctuation of ['!', '(', '[', '{', '"', "'", '`', '$', '«', '%28']) {
    rejected.push(`https://example.test/${punctuation}memory-a`);
    rejected.push(`dir/${punctuation}memory-a`);
  }
  for (const text of rejected) {
    assert.equal(browser.run(`findMemoryReferences(${JSON.stringify(text)}).length`), 0, text);
  }
  const accepted = [
    ['(memory-a).', 'memory-a'],
    ['[memory-a]!', 'memory-a'],
    ['label/path→memory-a', 'memory-a'],
    ['label/path→`memory-a`', 'memory-a'],
    ['(folder/memory-a).', 'folder/memory-a'],
    ['"https://known.test/(memory-a)"', 'https://known.test/(memory-a)'],
  ];
  for (const [text, id] of accepted) {
    assert.deepEqual(JSON.parse(browser.run(`JSON.stringify(findMemoryReferences(${JSON.stringify(text)}).map(reference => reference.id))`)), [id], text);
  }
});

test('compact JSON values use their own literal context in matching, MEMEX and Trace results', async () => {
  const browser = page();
  await loadGraph(browser, [{ id: 'memory-a', content: 'destination' }, { id: 'folder/memory-a', content: 'exact path ID' }]);
  const fixtures = [
    ['{"path":"/tmp/report.md","memory_id":"memory-a"}', ['memory-a']],
    ['["memory-a","/tmp/report.md"]', ['memory-a']],
    ['{"memory_id":"memory-a","url":"https://example.test"}', ['memory-a']],
    ['{"nested":[{"path":"/tmp/report.md","memory_id":"memory-a"}]}', ['memory-a']],
    ['Result: {"path":"/tmp/report.md","memory_id":"memory-a"}.', ['memory-a']],
    ['Result: `{"path":"/tmp/report.md","memory_id":"memory-a"}`.', ['memory-a']],
    ['["https://host/memory-a","memory-a"]', ['memory-a']],
    ['["memory-a.md","https://host/(memory-a)","dir/(memory-a).md"]', []],
    ['https://host/({"memory_id":"memory-a"})', []],
    ['["folder/memory-a","/tmp/report.md"]', ['folder/memory-a']],
    [String.raw`{"memory_id":"memory-\u0061","path":"/tmp/report.md"}`, []],
    [String.raw`["folder\/memory-a"]`, []],
  ];
  for (const [text, expected] of fixtures) {
    assert.deepEqual(JSON.parse(browser.run(`JSON.stringify(findMemoryReferences(${JSON.stringify(text)}).map(reference => reference.id))`)), expected, text);
    browser.run(`S.detail = {kind:"cycle",cycle:{},memex:{content:${JSON.stringify(text)}},prev_memex:{content:""},trace:{transcript:[{role:"assistant",blocks:[{type:"tool_result",content:${JSON.stringify(text)}}]}]}}; renderMemex(); renderTrace()`);
    assert.deepEqual(browser.references('#memex-doc').map(reference => reference.dataset.memoryId), expected, `MEMEX ${text}`);
    assert.deepEqual(browser.references('#trace-panel').map(reference => reference.dataset.memoryId), expected, `Trace ${text}`);
  }
});

test('one idempotent decorator preserves wrappers, text, controls, escaping and inline boundaries', async () => {
  const browser = page();
  await loadGraph(browser, [{ id: 'memory-a', content: '' }, { id: 'memory-b', content: '' }, { id: '<img onerror=evil>', content: '' }]);
  const root = browser.element('#memex-doc');
  root.innerHTML = '<p>plain memory-a <strong>memory-a</strong> <code>memory-b</code></p><pre>memory-a</pre><p>dir/<code>memory-a</code> label/path→<code>memory-b</code> memory-<strong>a</strong></p><p><strong>memory-a</strong>-suffix</p><p><a href="/memory-a">memory-a</a><button>memory-a</button><input value="memory-a"><span title="memory-a">unchanged</span></p><p>&lt;img onerror=evil&gt; &lt;script&gt;evil()&lt;/script&gt;</p>';
  const originalText = root.textContent;
  browser.run('decorateMemoryReferences($("#memex-doc")); decorateMemoryReferences($("#memex-doc"))');
  assert.deepEqual(browser.references('#memex-doc').map(reference => reference.dataset.memoryId), ['memory-a', 'memory-a', 'memory-b', 'memory-a', 'memory-b', '<img onerror=evil>']);
  assert.equal(root.textContent, originalText);
  assert.equal(root.querySelectorAll('script').filter(node => node.tagName === 'SCRIPT').length, 0);
  assert.equal(root.querySelectorAll('img').filter(node => node.tagName === 'IMG').length, 0);
  assert.equal(root.querySelectorAll('strong').filter(node => node.tagName === 'STRONG' && node.querySelector('.memory-reference')).length, 1);
  assert.equal(root.querySelectorAll('a').filter(node => node.tagName === 'A')[0].querySelector('.memory-reference'), null);
});

test('late graph loading decorates existing content and error payloads retain the last valid registry', async () => {
  const browser = page();
  browser.element('#memex-doc').innerHTML = '<p>memory-a memory-b</p>';
  await loadGraph(browser, [{ id: 'memory-a', content: 'first' }]);
  assert.deepEqual(browser.references('#memex-doc').map(reference => reference.textContent), ['memory-a']);
  const matcher = browser.run('S.memoryMatcher');
  await loadGraph(browser, [{ id: 'memory-a', content: 'changed' }]);
  assert.equal(browser.run('S.memoryMatcher'), matcher);
  browser.respondWith(() => ({ error: 'temporary', memories: [], links: [], db_present: true }));
  await browser.run('loadCurrentGraph()');
  assert.equal(browser.run('S.memoryRegistry.get("memory-a").content'), 'changed');
  assert.equal(browser.references('#memex-doc').length, 1);
  await loadGraph(browser, [{ id: 'memory-b', content: 'second' }]);
  assert.deepEqual(browser.references('#memex-doc').map(reference => reference.textContent), ['memory-b']);
});

test('MEMEX prose and diff, all Trace blocks, inspector bodies, endpoints and logs share navigation', async () => {
  const browser = page();
  const target = 'memory-target-with-a-long-id';
  await loadGraph(browser, [{ id: 'memory-a', content: `read ${target}` }, { id: target, content: 'destination' }], [{ source_id: 'memory-a', target_id: target, reason: `related ${target}` }]);
  browser.run(`S.detail = {kind:"cycle", cycle:{}, memex:{content:"plain ${target} **${target}** and \u0060${target}\u0060"}, prev_memex:{content:"old"}, trace:{transcript:[{role:"user",blocks:[{type:"text",text:"context ${target}"}]},{role:"assistant",blocks:[{type:"text",text:"${target} text"},{type:"tool_use",name:"read",input:{id:"${target}"}},{type:"tool_result",content:"result ${target}"},{type:"thinking",thinking:"think ${target}"}]}]}}; renderMemex()`);
  assert.equal(browser.references('#memex-doc').length, 3);
  browser.run('S.view = "diff"; renderMemex()');
  assert.equal(browser.references('#memex-doc').length, 3);
  browser.run('renderTrace()');
  assert.equal(browser.references('#trace-doc').length, 3);
  assert.equal(browser.references('#trace-panel').length, 6);
  const headerReference = browser.references('#trace-panel').find(reference => reference.closest('.turn-head'));
  const turn = headerReference.closest('.turn');
  browser.dispatch(headerReference, 'click');
  assert.equal(turn.classList.contains('open'), false);
  assert.match(browser.element('#memory-reader-body').textContent, /destination/);
  browser.dispatch(turn.querySelector('.turn-head'), 'click');
  assert.equal(turn.classList.contains('open'), true);
  browser.run('closeMemoryReader(); renderCurrentGraph()');
  const block = browser.element('#mem-grid').querySelectorAll('.mem-block').find(element => element.dataset.id === 'memory-a');
  browser.dispatch(block, 'click');
  assert.equal(browser.references('#mem-inspect').some(reference => reference.dataset.memoryId === target), true);
  const endpoint = browser.element('#mem-inspect').querySelectorAll('.memory-endpoint').find(element => element.dataset.memoryId === target);
  assert.equal(endpoint.textContent, target.slice(0, 12));
  browser.dispatch(endpoint, 'click');
  assert.equal(browser.run('S.memoryNavigation.entries.at(-1).id'), target);
  browser.run(`closeMemoryReader(); S.detail = {kind:"ask",ask:{input_text:"${target}",output_text:"${target}"},transcript:[]}; renderMemex(); renderTrace()`);
  assert.equal(browser.references('#memex-doc').length, 2);
  assert.equal(browser.references('#trace-doc').length, 2);
  browser.run('closeMemoryReader(); S.log.open = true');
  browser.respondWith(() => ({ lines: [`INFO ${target}`] }));
  await browser.run('pollLog()');
  assert.equal(browser.references('#log-body').length, 1);
  browser.dispatch(browser.references('#log-body')[0], 'click');
  assert.equal(browser.run('S.memoryNavigation.entries.at(-1).id'), target);
});

test('reader history restores focus and position without timeline changes or reference requests', async () => {
  const browser = page();
  await loadGraph(browser, [{ id: 'memory-a', content: 'next memory-b' }, { id: 'memory-b', content: 'back memory-a' }]);
  browser.run('S.tab = "trace"; S.view = "diff"; S.memexOnly = true; S.selected = {kind:"cycle",id:"origin"}; S.selectedIdx = 7');
  const source = browser.element('#trace-doc');
  source.innerHTML = '<p>read memory-a</p>';
  source.scrollTop = 123;
  browser.run('decorateMemoryReferences($("#trace-doc"))');
  const origin = browser.references('#trace-doc')[0];
  origin.focus();
  const requests = browser.fetchCount;
  browser.dispatch(origin, 'keydown', { key: 'Enter' });
  assert.equal(browser.element('#memory-reader').hidden, false);
  const body = browser.element('#memory-reader-body');
  body.scrollTop = 81;
  const next = browser.references('#memory-reader-body').find(reference => reference.dataset.memoryId === 'memory-b');
  next.focus();
  browser.dispatch(next, 'click');
  assert.equal(browser.run('S.memoryNavigation.entries.length'), 2);
  body.scrollTop = 41;
  browser.run('openMemoryReference("memory-b")');
  assert.equal(browser.run('S.memoryNavigation.entries.length'), 2);
  assert.equal(body.scrollTop, 41);
  browser.dispatch(browser.element('#memory-reader-back'), 'click');
  assert.equal(browser.run('S.memoryNavigation.entries.at(-1).id'), 'memory-a');
  assert.equal(body.scrollTop, 81);
  assert.equal(browser.document.activeElement.dataset.memoryId, 'memory-b');
  browser.dispatch(browser.document.activeElement, 'keydown', { key: 'ArrowLeft' });
  assert.equal(browser.run('S.selectedIdx'), 7);
  browser.dispatch(browser.element('#memory-reader-close'), 'click');
  assert.equal(browser.run('S.memoryNavigation'), null);
  assert.equal(source.scrollTop, 123);
  assert.equal(browser.document.activeElement, origin);
  assert.equal(browser.run('JSON.stringify([S.tab,S.view,S.memexOnly,S.selected.id,S.selectedIdx])'), '["trace","diff",true,"origin",7]');
  assert.equal(browser.fetchCount, requests);
  browser.dispatch(origin, 'click');
  browser.dispatch(browser.element('#memory-reader-back'), 'click');
  assert.equal(browser.run('S.memoryNavigation'), null);
});

test('selection does not navigate; reference keys and Escape do not drive the timeline', async () => {
  const browser = page();
  await loadGraph(browser, [{ id: 'memory-a', content: 'body' }]);
  browser.element('#memex-doc').innerHTML = '<p>memory-a</p>';
  browser.run('decorateMemoryReferences($("#memex-doc")); S.selectedIdx = 9');
  const reference = browser.references('#memex-doc')[0];
  browser.select(true);
  browser.dispatch(reference, 'click');
  assert.equal(browser.run('S.memoryNavigation'), null);
  browser.select(false);
  browser.dispatch(reference, 'keydown', { key: 'Home' });
  assert.equal(browser.run('S.selectedIdx'), 9);
  assert.equal(browser.dispatch(reference, 'keydown', { key: ' ' }).prevented, true);
  browser.dispatch(browser.element('#memory-reader-back'), 'keydown', { key: 'Escape' });
  assert.equal(browser.run('S.memoryNavigation'), null);
  assert.equal(browser.document.activeElement, reference);
});

test('reader polling preserves unchanged DOM, updates only active content, and keeps a return after deletion', async () => {
  const browser = page();
  await loadGraph(browser, [{ id: 'memory-a', content: 'next memory-b' }, { id: 'memory-b', content: 'other' }]);
  browser.element('#memex-doc').innerHTML = '<p>memory-a</p>';
  browser.run('decorateMemoryReferences($("#memex-doc"))');
  browser.dispatch(browser.references('#memex-doc')[0], 'click');
  const body = browser.element('#memory-reader-body');
  body.scrollTop = 71;
  const original = body.childNodes[0];
  await loadGraph(browser, [{ id: 'memory-a', content: 'next memory-b' }, { id: 'memory-b', content: 'unrelated change' }]);
  assert.equal(body.childNodes[0], original);
  assert.equal(body.scrollTop, 71);
  const focus = browser.references('#memory-reader-body').find(reference => reference.dataset.memoryId === 'memory-b');
  focus.focus();
  await loadGraph(browser, [{ id: 'memory-a', content: 'updated memory-b', updated_at: '2026-10-23T12:00:00Z' }, { id: 'memory-b', content: 'unrelated change' }]);
  assert.match(body.textContent, /updated memory-b/);
  assert.match(body.textContent, /2026-10-23/);
  assert.equal(body.scrollTop, 71);
  assert.equal(browser.document.activeElement.dataset.memoryId, 'memory-b');
  browser.respondWith(() => { throw new Error('offline'); });
  await browser.run('loadCurrentGraph()');
  assert.match(browser.element('#memory-reader-status').textContent, /last loaded/);
  assert.match(body.textContent, /updated memory-b/);
  await loadGraph(browser, [{ id: 'memory-b', content: 'other' }]);
  assert.match(body.textContent, /no longer available/);
  browser.dispatch(browser.element('#memory-reader-back'), 'click');
  assert.equal(browser.run('S.memoryNavigation'), null);
  assert.equal(browser.element('#memex-doc').textContent, 'memory-a');
});

test('Back resolves current content and restores a zero-scroll source after closing at depth', async () => {
  const browser = page();
  await loadGraph(browser, [{ id: 'memory-a', content: 'next memory-b' }, { id: 'memory-b', content: 'other' }]);
  const source = browser.element('#log-body');
  source.innerHTML = '<div>memory-a</div>';
  browser.run('decorateMemoryReferences($("#log-body"))');
  browser.dispatch(browser.references('#log-body')[0], 'click');
  browser.element('#memory-reader-body').scrollTop = 62;
  browser.dispatch(browser.references('#memory-reader-body').find(reference => reference.dataset.memoryId === 'memory-b'), 'click');
  await loadGraph(browser, [{ id: 'memory-a', content: 'latest memory-b' }, { id: 'memory-b', content: 'other' }]);
  browser.dispatch(browser.element('#memory-reader-back'), 'click');
  assert.match(browser.element('#memory-reader-body').textContent, /latest memory-b/);
  assert.equal(browser.element('#memory-reader-body').scrollTop, 62);
  browser.dispatch(browser.references('#memory-reader-body').find(reference => reference.dataset.memoryId === 'memory-b'), 'click');
  source.scrollTop = 999;
  browser.dispatch(browser.element('#memory-reader-close'), 'click');
  assert.equal(source.scrollTop, 0);
  assert.equal(browser.run('S.memoryNavigation'), null);
});

test('relationship prefixes never resolve by prefix and removed reference controls show unavailable', async () => {
  const browser = page();
  await loadGraph(browser, [{ id: 'memory-a', content: 'source' }, { id: 'abcdefghijkl', content: 'prefix' }, { id: 'abcdefghijkl-first', content: 'first target' }, { id: 'abcdefghijkl-second', content: 'second target' }], [{ source_id: 'memory-a', target_id: 'abcdefghijkl-second', reason: 'related' }]);
  browser.run('openMemoryReference("memory-a")');
  const endpoint = browser.element('#memory-reader-body').querySelectorAll('.memory-endpoint').find(element => element.dataset.memoryId === 'abcdefghijkl-second');
  assert.equal(endpoint.textContent, 'abcdefghijkl');
  browser.dispatch(endpoint, 'click');
  assert.match(browser.element('#memory-reader-body').textContent, /second target/);
  browser.run('closeMemoryReader(); openMemoryReference("unknown-uuid")');
  assert.equal(browser.run('S.memoryNavigation'), null);
  await loadGraph(browser, []);
  const stale = browser.document.createElement('span');
  stale.dataset.memoryId = 'memory-a';
  browser.document.body.appendChild(stale);
  browser.run('globalThis.staleSource = document.body.childNodes.at(-1); openMemoryReference("memory-a", staleSource)');
  assert.match(browser.element('#memory-reader-body').textContent, /no longer available/);
  browser.dispatch(browser.element('#memory-reader-back'), 'click');
  assert.equal(browser.run('S.memoryNavigation'), null);
});
