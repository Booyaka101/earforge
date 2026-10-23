/* Minimal DOM and deferred HTTP for app wiring, not a replacement for CDP. */
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import grid from '../../earforge/static/grid.js';

export const reply = (body, ok = true) => ({ok, json: async () => body, blob: async () => new Blob(['sheets'])});

export async function appHarness(route, config = {}) {
  const h = {bufferChanges: 0, workspaceEntries: 0, downloads: 0};
  const elements = new Map();
  let serial = 0;
  h.element = function element(id) {
    if (elements.has(id)) return elements.get(id);
    const classes = new Set();
    const el = {id, value: '', disabled: false, innerHTML: '', textContent: '', style: {}, dataset: {}, children: [],
      classList: {
        toggle(c, force) { const on = force ?? !classes.has(c); on ? classes.add(c) : classes.delete(c); },
        contains: c => classes.has(c), add: c => classes.add(c), remove: c => classes.delete(c),
      },
      addEventListener() {}, appendChild(child) { this.children.push(child); },
      querySelector() { return element(id + '-body'); },
      insertRow() { return {innerHTML: '', className: ''}; }, clientWidth: 800,
      click() { if (this.disabled) return; if (this.tagName === 'A') h.downloads++; if (this.onclick) this.onclick({target:this}); },
      remove() {},
    };
    elements.set(id, el);
    return el;
  };
  class Player {
    constructor() { h.player = this; this.pos = 0; this.buffers = {}; }
    pause() {} setLoop() {} setChannels() {} updateGains() {} position() { return this.pos; }
    seek(raw) { this.pos = raw; }
    setBuffers(buffers) { h.bufferChanges++; this.buffers = buffers; }
  }
  class Roll {
    setLoop() {} setData(_i, _n, _d, g) { this.grid = g; h.workspaceEntries++; } setGrid(g) { this.grid = g; }
    setPosition() {}
  }
  class EventSource {
    constructor(url) { this.url = url; h.stream = this; } close() { this.closed = true; }
  }
  const context = {
    console, Player, Roll, EventSource, EarforgeGrid: grid, Map, Set, FormData, Blob,
    document: {
      getElementById: h.element, querySelectorAll: () => [], body: h.element('body'),
      createElement(tag) { const el = h.element('created-' + serial++); el.tagName = tag.toUpperCase(); return el; },
    },
    URL: {createObjectURL: () => 'blob:test', revokeObjectURL() {}},
    CSS: {escape: x => x}, rollPalette: [], performance: {now: () => 1},
    requestAnimationFrame: () => 1, cancelAnimationFrame() {}, setTimeout, clearTimeout,
    addEventListener() {},
    fetch(url, options) {
      if (url === '/api/config') return Promise.resolve(reply({ffmpeg:true, fluidsynth:false, musescore:false, ...config}));
      if (url === '/api/instruments') return Promise.resolve(reply({instruments:[]}));
      return route(url, options) || new Promise(() => {});
    },
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(readFileSync('earforge/static/app.js', 'utf8'), context);
  h.flush = async () => { for (let i = 0; i < 16; i++) await Promise.resolve(); };
  h.emit = obj => h.stream.onmessage({data:JSON.stringify(obj)});
  h.state = context.earforgeState;
  await h.flush();
  return h;
}
