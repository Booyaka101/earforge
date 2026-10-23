import assert from 'node:assert/strict';
import test from 'node:test';
import {appHarness, reply} from './app-harness.mjs';

const detected = {bpm:100, beats_per_bar:4, first_downbeat:0, onset_delay:0, steady:true};
const job = {id:'saved', status:'complete', note_count:0, duration_s:12, instruments_found:[],
  export_revision:0, manual_grid:{}, resolved_grid:detected};

async function workspace() {
  const pending = {grid:[], sheets:[], metadata:[], uploads:[], deferUploads:false};
  const h = await appHarness((url, options) => {
    if (url === '/api/jobs' && !pending.deferUploads) return Promise.resolve(reply({id:'saved'}));
    if (url === '/api/library') return Promise.resolve(reply({jobs:[{...job, source_name:'saved.wav', model:'medium', created_at:'today'}]}));
    const key = url === '/api/jobs' ? 'uploads' : url.endsWith('/export/sheets') ? 'sheets'
      : url.endsWith('/grid') ? 'grid' : url === '/api/jobs/saved' ? 'metadata' : null;
    if (key) return new Promise(resolve => pending[key].push({resolve, options}));
  }, {musescore:true});
  h.state.file = new Blob(['audio']);
  h.element('goBtn').onclick();
  await h.flush();
  h.emit({type:'job_start', duration_s:12});
  h.emit({type:'job_complete', instruments:[], ...job});
  return {h, pending};
}

test('new upload clears the previous job, buffers and playhead immediately', async () => {
  const {h, pending} = await workspace();
  h.player.pos = 8;
  h.player.buffers = {original:{old:true}};
  pending.deferUploads = true;
  h.element('goBtn').onclick();
  assert.equal(h.state.jobId, null);
  assert.equal(h.player.position(), 0);
  assert.equal(Object.keys(h.player.buffers).length, 0);
});

test('sheet completion cannot download or re-enable exports during a grid save', async () => {
  const {h, pending} = await workspace();
  h.element('expSheets').click();
  h.element('gridForm').onsubmit({preventDefault(){}});
  pending.sheets[0].resolve(reply({}));
  await h.flush();
  assert.equal(h.downloads, 0);
  assert.equal(h.element('expSheets').disabled, true);
  pending.grid[0].resolve(reply({...job, export_revision:1}));
  await h.flush();
  assert.equal(h.element('expSheets').disabled, false);
});

test('a late sheet result cannot affect a new workspace', async () => {
  const {h, pending} = await workspace();
  h.element('expSheets').click();
  pending.deferUploads = true;
  h.element('goBtn').onclick();
  pending.sheets[0].resolve(reply({}));
  await h.flush();
  assert.equal(h.downloads, 0);
  assert.equal(h.element('expSheets').disabled, true);
});

test('reload saved grid resolves a conflict without resetting playback', async () => {
  const {h, pending} = await workspace();
  const original = {}, stems = {}, notes = h.state.notes;
  h.state.originalBuffer = original;
  h.state.stems = stems;
  h.player.pos = 3.25;
  const buffers = h.bufferChanges, entries = h.workspaceEntries;
  h.element('gridReload').click();
  assert.equal(pending.metadata.length, 1);
  assert.equal(h.element('gridSave').disabled, true);
  pending.metadata[0].resolve(reply({...job, export_revision:2, manual_grid:{bpm:130}, resolved_grid:{...detected,bpm:130}}));
  await h.flush();
  assert.equal(h.state.exportRevision, 2);
  assert.equal(h.element('gridBpm').value, 130);
  assert.equal(h.state.originalBuffer, original);
  assert.equal(h.state.stems, stems);
  assert.equal(h.state.notes, notes);
  assert.equal(h.player.position(), 3.25);
  assert.equal(h.bufferChanges, buffers);
  assert.equal(h.workspaceEntries, entries);
});

test('failed library reopen stays in the library with an actionable error', async () => {
  const {h, pending} = await workspace();
  h.element('tabLibrary').onclick();
  await h.flush();
  h.element('libraryList').children[0].onclick({target:{tagName:'DIV'}});
  const previous = h.stream;
  pending.metadata[0].resolve(reply({detail:'no job saved'}, false));
  await h.flush();
  assert.equal(h.stream, previous);
  assert.equal(h.state.jobId, null);
  assert.equal(h.state.view, 'library');
  assert.ok(h.element('banners').children.some(el => /Could not open job.*no job saved/.test(el.textContent)));
});

test('zero-note replay builds its workspace only once', async () => {
  const {h, pending} = await workspace();
  h.element('tabLibrary').onclick();
  await h.flush();
  h.element('libraryList').children[0].onclick({target:{tagName:'DIV'}});
  pending.metadata[0].resolve(reply(job));
  await h.flush();
  h.emit({type:'job_complete', instruments:[], ...job});
  const entries = h.workspaceEntries;
  await new Promise(resolve => setTimeout(resolve, 450));
  assert.equal(h.workspaceEntries, entries);
});
