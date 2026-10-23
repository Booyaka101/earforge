import assert from 'node:assert/strict';
import grid from '../../earforge/static/grid.js';

const fixed = {bpm: 120, beats_per_bar: 4, first_downbeat: 2, steady: true};
assert.deepEqual(grid.lines(fixed, 4).filter(l => l.bar).map(l => l.time), [2, 4]);
assert.deepEqual(grid.lines(fixed, 3.5).filter(l => l.time >= 2).map(l => l.time), [2, 2.5, 3, 3.5]);
const note = {start_time: 3.25, end_time: 3.5};
grid.lines(fixed, 10);
assert.equal(note.start_time, 3.25);
assert.equal(grid.lines({...fixed, beats_per_bar: null}, 4).filter(l => l.bar).length, 0);
assert.deepEqual(grid.lines({...fixed, steady: false, first_downbeat: 1, beats: [0, .45, 1, 1.65, 2.4, 3.2, 4.1]}, 5).filter(l => l.bar).map(l => l.time), [1, 4.1]);
assert.equal(grid.acceptRevision(3, 0), false);
assert.equal(grid.acceptRevision(3, null), false);
assert.equal(grid.acceptRevision(3, 3), true);
assert.equal(grid.acceptRevision(3, 4), true);
assert.deepEqual(grid.lines(null, 10), []);
console.log('10 grid assertions passed: worked timing, nullable meter, wandering tempo, stale replay');
