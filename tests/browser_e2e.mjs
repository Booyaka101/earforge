/* Real Chrome + real local server. Run explicitly, not as a unit-test stub.
   CDP_DRIVER points to the installed cdp-chrome skill's cdp.mjs.
   EARFORGE_E2E_AUDIO must name an actual audio input; server model is real. */
import assert from 'node:assert/strict';
import {writeFileSync, mkdirSync} from 'node:fs';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const {connectByUrl, listTabs} = await import(pathToFileURL(process.env.CDP_DRIVER).href);
const base = process.env.EARFORGE_E2E_URL || 'http://127.0.0.1:8418';
const savedId = process.env.EARFORGE_E2E_JOB;
const audio = savedId ? null : resolve(process.env.EARFORGE_E2E_AUDIO);
if (!(await listTabs()).some(t => t.url.startsWith(base))) {
  await fetch(`http://127.0.0.1:${process.env.CDP_PORT || 9222}/json/new?${encodeURIComponent(base)}`, {method: 'PUT'});
}
const c = await connectByUrl(base);
const errors = [];
c.on(event => { if (event.method === 'Runtime.exceptionThrown') errors.push(event.params); });
const wait = async (expression, label, attempts = 90) => {
  for (let i = 0; i < attempts; i++) {
    if (await c.evaluate(expression)) return;
    await c.sleep(500);
  }
  throw new Error(`Timed out: ${label}`);
};
try {
  // Revalidation alone can retain fresh cached assets from a previous local build.
  await c.send('Page.reload', {ignoreCache:true});
  await c.sleep(1800);
  await c.navigate(base, 1800);
  assert.equal(await c.evaluate('Player.prototype._scheduleTake.toString().includes("queued")'), true, 'current player build loaded');
  await c.evaluate(`window.__errors=[]; window.addEventListener('error',e=>window.__errors.push(e.message)); window.addEventListener('unhandledrejection',e=>window.__errors.push(String(e.reason)));`);
  await wait('earforgeState.config && earforgeState.config.ffmpeg', 'configuration');
  const openLibraryJob = async id => {
    await c.clickSelector('#tabLibrary');
    await wait('document.querySelector("#libraryList .row")', 'library');
    const jobs = (await (await fetch(`${base}/api/library`)).json()).jobs;
    const index = jobs.findIndex(job => job.id === id);
    assert.ok(index >= 0);
    await c.evaluate(`document.querySelectorAll('#libraryList .row')[${index}].click()`);
  };
  if (savedId) {
    await openLibraryJob(savedId);
  } else {
  const {root} = await c.send('DOM.getDocument');
  const {nodeId} = await c.send('DOM.querySelector', {nodeId: root.nodeId, selector: '#fileInput'});
  await c.send('DOM.setFileInputFiles', {nodeId, files: [audio]});
  assert.equal(await c.evaluate('document.querySelector("#goBtn").disabled'), false);
  await c.clickSelector('#goBtn');
  }
  await wait('earforgeState.view === "workspace" && earforgeState.jobStatus === "complete"', 'real transcription');
  await wait('earforgeState.originalBuffer && Object.keys(earforgeState.stems).length === earforgeState.instruments.length', 'real playback buffers');
  const initial = await c.evaluate(`(async()=>{
    const s=earforgeState, job=await (await fetch('/api/jobs/'+s.jobId)).json();
    window.__before={original:s.originalBuffer,stems:s.stems,audioCache:s.audioCache,stemCache:s.stemCache,notes:JSON.stringify(Array.from(s.notes))};
    const old=Player.prototype.setChannels; Player.prototype.setChannels=function(ch){window.__player=this;window.__channels=ch;return old.call(this,ch);};
    return {id:s.jobId,notes:s.notes.size,instruments:s.instruments,detected:job.detected_grid,revision:s.exportRevision};
  })()`);
  assert.ok(initial.notes > 0);
  console.log(`${savedId ? 'REUSED REAL TRANSCRIPTION' : 'REAL TRANSCRIPTION'}: ${JSON.stringify({notes: initial.notes, instruments: initial.instruments})}`);
  await c.setReactValue('#gridBpm', '120');
  await c.setReactValue('#gridMeter', '4');
  await c.setReactValue('#gridDownbeat', '2');
  assert.deepEqual(await c.evaluate('["gridBpm","gridMeter","gridDownbeat"].map(id=>document.getElementById(id).value)'), ['120', '4', '2']);
  await c.clickSelector('#playBtn');
  await c.sleep(600);
  const beforeTime = await c.evaluate('document.getElementById("timeDisplay").textContent');
  await c.clickSelector('#gridSave');
  await wait(`earforgeState.exportRevision === ${initial.revision + 1} && !earforgeState.gridSaving`, 'save revision');
  assert.equal(await c.evaluate(`(()=>{const s=earforgeState,b=window.__before;return s.originalBuffer===b.original && s.stems===b.stems && s.audioCache===b.audioCache && s.stemCache===b.stemCache && JSON.stringify(Array.from(s.notes))===b.notes;})()`), true);
  await c.sleep(600);
  assert.notEqual(await c.evaluate('document.getElementById("timeDisplay").textContent'), beforeTime);
  await c.clickSelector('#playBtn');
  assert.deepEqual(await c.evaluate('EarforgeGrid.lines(earforgeState.grid,4).filter(l=>l.bar).map(l=>l.time)'), [2, 4]);
  assert.deepEqual(await c.evaluate('EarforgeGrid.lines(earforgeState.grid,3.5).filter(l=>l.time>=2).map(l=>l.time)'), [2, 2.5, 3, 3.5]);
  const inst = initial.instruments[0];
  await c.clickSelector('#solo' + inst);
  assert.equal(await c.evaluate(`window.__channels[${JSON.stringify(inst)}].solo`), true);
  await c.clickSelector('#solo' + inst);
  mkdirSync('docs', {recursive: true});
  await c.send('Page.bringToFront');
  await c.evaluate('window.scrollTo(0,0)');
  writeFileSync('docs/14-saved-grid.png', Buffer.from(await c.screenshot(), 'base64'));
  const midi1 = Buffer.from(await (await fetch(`${base}/api/jobs/${initial.id}/export/midi`)).arrayBuffer());
  await c.clickSelector('#gridSave');
  await wait(`earforgeState.exportRevision === ${initial.revision + 2}`, 'identical repeated save');
  const midi2 = Buffer.from(await (await fetch(`${base}/api/jobs/${initial.id}/export/midi`)).arrayBuffer());
  assert.deepEqual(midi1, midi2);
  assert.equal(midi1.subarray(0, 4).toString(), 'MThd');
  const stale = await fetch(`${base}/api/jobs/${initial.id}/grid`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({expected_revision:0,bpm:140})});
  assert.equal(stale.status, 409);
  // A second tab's successful write should recover through Reload saved, without audio reload.
  const current = await (await fetch(`${base}/api/jobs/${initial.id}`)).json();
  const external = await fetch(`${base}/api/jobs/${initial.id}/grid`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:current.export_revision,...current.manual_grid})});
  assert.equal(external.status, 200);
  await c.clickSelector('#gridSave');
  await wait('!earforgeState.gridSaving && document.getElementById("gridStatus").textContent.includes("Grid not saved")', 'stale conflict UI');
  await c.clickSelector('#gridReload');
  await wait(`!earforgeState.gridSaving && earforgeState.exportRevision === ${initial.revision + 3}`, 'reload saved recovery');
  assert.equal(await c.evaluate('earforgeState.originalBuffer === window.__before.original'), true);
  await openLibraryJob(initial.id);
  await wait(`earforgeState.view === "workspace" && earforgeState.exportRevision === ${initial.revision + 3} && earforgeState.notes.size > 0`, 'persisted reopen');
  await wait('window.__player && earforgeState.originalBuffer', 'reopened audio');
  await c.evaluate('window.__player.seek(3.25);window.__player.play()');
  await c.sleep(250);
  const paused = await c.evaluate('window.__player.pause();window.__player.position()');
  await c.sleep(1200);
  await c.evaluate('window.__player.play()');
  await c.sleep(200);
  const resumed = await c.evaluate('window.__player.position()');
  assert.ok(resumed >= paused && resumed < paused + 0.8, `resume jumped: ${paused} -> ${resumed}`);
  await c.evaluate('window.__player.pause();window.__player.setLoop({a:1,b:2});window.__player.seek(1);window.__player.play()');
  await c.sleep(1400);
  const looped = await c.evaluate('window.__player.position()');
  assert.ok(looped >= 1 && looped < 2);
  await c.evaluate('window.__player.pause();window.__player.setLoop(null)');
  assert.deepEqual(await c.evaluate('["gridBpm","gridMeter","gridDownbeat"].map(id=>document.getElementById(id).value)'), ['120', '4', '2']);
  assert.equal(await c.evaluate('earforgeState.grid.first_downbeat'), 2);
  await c.clickSelector('#gridAuto');
  assert.deepEqual(await c.evaluate('["gridBpm","gridMeter","gridDownbeat"].map(id=>document.getElementById(id).value)'), ['', '', '']);
  assert.equal(await c.evaluate('earforgeState.grid.bpm'), 120); // Auto is an unsaved edit.
  await c.clickSelector('#gridReset');
  await wait(`earforgeState.exportRevision === ${initial.revision + 4}`, 'reset');
  const job = await (await fetch(`${base}/api/jobs/${initial.id}`)).json();
  assert.deepEqual(job.manual_grid, {});
  assert.deepEqual(job.detected_grid, initial.detected);
  await c.evaluate('window.scrollTo(0,0)');
  writeFileSync('docs/15-reset-grid.png', Buffer.from(await c.screenshot(), 'base64'));
  for (const endpoint of ['midi','jsonl',`stem/${inst}.mid`]) {
    assert.equal((await fetch(`${base}/api/jobs/${initial.id}/export/${endpoint}`)).status, 200);
  }
  const cfg = await c.evaluate('earforgeState.config');
  if (!cfg.musescore) assert.equal((await fetch(`${base}/api/jobs/${initial.id}/export/sheets`,{method:'POST'})).status, 503);
  assert.deepEqual(await c.evaluate('window.__errors'), []);
  assert.deepEqual(errors, []);
  writeFileSync(savedId ? '.e2e/v020-review-result.json' : '.e2e/v020-result.json', JSON.stringify({job:initial.id, notes:initial.notes, instruments:initial.instruments, revisions:job.export_revision, reused:!!savedId, musescore:cfg.musescore, errors:[]}, null, 2));
  console.log(`BROWSER E2E PASS: ${savedId ? 'reused real job' : 'real upload/transcription'}, playback, 120 BPM / 4 / 2s grid, unchanged buffers/notes, solo, repeated save, stale 409, Reload saved, reopen, pause/resume, loop, Auto, reset, exports; 0 browser errors.`);
  console.log(`SHEETS: ${cfg.musescore ? 'available' : 'real engraving unavailable; 503 install hint verified'}`);
} finally {
  c.close();
}
process.exit(0);
