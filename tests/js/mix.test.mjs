/* effectiveGain tests, run by tests/test_mix.py via `node`. */
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { effectiveGain } = require("../../earforge/static/player.js");

const base = (over) => Object.assign({
  original: { gain: 0.8, mute: false, solo: false },
  electric_bass: { gain: 1, mute: false, solo: false },
  drums: { gain: 1, mute: false, solo: false },
}, over);

// plain mixing: each channel plays at its own gain
let ch = base({});
assert.equal(effectiveGain(ch, "electric_bass"), 1);
assert.equal(effectiveGain(ch, "original"), 0.8);

// mute silences one channel only
ch = base({ drums: { gain: 1, mute: true, solo: false } });
assert.equal(effectiveGain(ch, "drums"), 0);
assert.equal(effectiveGain(ch, "electric_bass"), 1);

// soloing the bass silences everything else, including the original audio
ch = base({ electric_bass: { gain: 1, mute: false, solo: true } });
assert.equal(effectiveGain(ch, "electric_bass"), 1);
assert.equal(effectiveGain(ch, "drums"), 0);
assert.equal(effectiveGain(ch, "original"), 0);

// solo wins over mute on the same channel
ch = base({ electric_bass: { gain: 1, mute: true, solo: true } });
assert.equal(effectiveGain(ch, "electric_bass"), 1);

// the last solo standing respects its own gain slider
ch = base({ drums: { gain: 0.5, mute: false, solo: true } });
assert.equal(effectiveGain(ch, "drums"), 0.5);

// unknown channel id is silence, not a crash
assert.equal(effectiveGain(base({}), "nope"), 0);

console.log("effectiveGain: 7 assertions ok");
