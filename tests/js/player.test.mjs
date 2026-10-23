/* Headless Player tests: a fake AudioContext records every scheduled source
   so the loop/seek/rate math can be asserted without a browser.
   Run by tests/test_mix.py via `node`. */
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { Player } = require("../../earforge/static/player.js");

// the end-of-take watchdog polls with setTimeout; neutralize it so node exits
global.setTimeout = (fn, ms) => 0;
let scheduler;
global.setInterval = fn => { scheduler = fn; return 1; };
global.clearInterval = () => {};

let now = 0;
const scheduled = [];

class FakeSource {
  constructor() {
    this.playbackRate = { value: 1 };
    this.stoppedAt = null;
  }
  connect() {}
  start(when, offset) {
    this.startWhen = when;
    this.startOffset = offset;
    scheduled.push(this);
  }
  stop(t) { this.stoppedAt = t; }
  disconnect() {}
}

class FakeCtx {
  constructor() { this.currentTime = 0; this.destination = {}; }
  resume() {}
  createGain() { return { connect() {}, gain: { value: 1 } }; }
  createBufferSource() { return new FakeSource(); }
}

function makePlayer(buffersById, duration, rate) {
  const map = {};
  for (const [id, seconds] of Object.entries(buffersById)) {
    map[id] = { id, duration: seconds };
  }
  global.AudioContext = FakeCtx;
  const p = new Player();
  p._ensureCtx();
  p.ctx.currentTime = now;
  p.buffers = map;
  p.duration = duration;
  p.rate = rate;
  return p;
}

let p;

// ---------- 1. plain playback schedules every source from position 0 ----------
p = makePlayer({ original: 24, bass: 24 }, 24, 1);
scheduled.length = 0;
p.play();
assert.equal(scheduled.length, 2, "one source per buffer");
for (const s of scheduled) {
  assert.ok(Math.abs(s.startOffset) < 1e-9, "starts at offset 0");
  assert.ok(s.startWhen >= now, "scheduled in the future");
}
assert.ok(p.playing);

// ---------- 2. position advances with wall time, freezes on pause ----------
p.anchorCtxTime = now;
p.anchorPos = 0;
now += 3;
p.ctx.currentTime = now;
assert.ok(Math.abs(p.position() - 3) < 1e-9, "raw position tracks wall time");
p.pause();
assert.ok(Math.abs(p.position() - 3) < 1e-9, "frozen while paused");
p.ctx.currentTime += 20;
p.play();
assert.ok(Math.abs(scheduled.at(-1).startOffset - 3) < 1e-9, "resume ignores elapsed paused time");
p.pause();

// ---------- 3. seek offset and stop time live in the stretched domain ----------
p = makePlayer({ original: 32, bass: 32 }, 24, 0.75); // buffers stretched to 32s
scheduled.length = 0;
p.playing = true;
p._scheduleTake(now + 0.02, 6); // to the end: span = 24 - 6 = 18 raw seconds
const first = scheduled[0];
assert.ok(Math.abs(first.startOffset - 8) < 1e-9, "offset = raw/rate (6/0.75)");
assert.ok(
  Math.abs(first.stoppedAt - (now + 0.02 + 18 / 0.75)) < 1e-6,
  "stop = start + span/rate"
);

// ---------- 4. loop scheduling wraps to loop start ----------
p = makePlayer({ original: 24 }, 24, 1);
p.loop = { a: 2, b: 6 };
scheduled.length = 0;
p.playing = true;
p._scheduleTake(now + 0.02, 4); // 2s before the loop end
assert.ok(p.nextTake, "next take armed");
assert.ok(Math.abs(p.nextTake.atCtx - (now + 0.02 + 2)) < 1e-6, "next take at span end");
assert.equal(p.nextTake.fromRaw, 2, "next take from loop start");

// ---------- 5. solo silences the original ----------
p = makePlayer({ original: 10, bass: 10 }, 10, 1);
p.setChannels({
  original: { gain: 0.8, mute: false, solo: false },
  bass: { gain: 1, mute: false, solo: true },
});
assert.equal(p.gains.original.gain.value, 0, "solo mutes original");
assert.equal(p.gains.bass.gain.value, 1);

// ---------- 6. stale channels are dropped ----------
p.setChannels({ original: { gain: 1, mute: false, solo: false } });
assert.ok(!p.gains.bass, "old lane gain node removed");

// Look-ahead queues the next loop without stopping or re-anchoring this one.
p = makePlayer({original:10}, 10, 1);
p.loop = {a:2, b:4};
scheduled.length = 0;
p.playing = true;
p._scheduleTake(now, 2);
p._startScheduler();
const current = scheduled[0];
p.ctx.currentTime = now + 1.7;
scheduler();
assert.equal(current.stoppedAt, now + 2, "current loop plays to its scheduled end");
assert.ok(Math.abs(p.position() - 3.7) < 1e-9, "look-ahead does not move the playhead early");
p.ctx.currentTime = now + 2.1;
scheduler();
assert.ok(Math.abs(p.position() - 2.1) < 1e-9, "playhead wraps when the next loop actually starts");
p.ctx.currentTime = now + 8.1;
scheduler();
assert.ok(Math.abs(p.position() - 2.1) < 1e-9, "scheduler catches up after a delayed timer");
p.pause();

console.log("player: playback, pause/resume, rate, seek, solo and gap-free loop assertions ok");
