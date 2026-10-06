/* earforge app glue: upload flow, SSE consumption, mixer wiring, library. */
(function (global) {
  "use strict";

  var state = {
    config: null,
    jobId: null,
    notes: new Map(),        /* note index -> note object */
    eventsSeen: new Set(),
    instruments: [],
    duration: 0,
    grid: null,
    progress: null,
    firstProgressAt: 0,
    es: null,
    file: null,
    stems: {},               /* instrument -> AudioBuffer (at player.rate) */
    originalBuffer: null,
    audioCache: {},          /* rate -> original AudioBuffer at that speed */
    stemCache: {},           /* rate -> {instrument: AudioBuffer} */
    rate: 1,
    view: "upload",
    jobBanners: [],         /* banners from the event stream, re-rendered in the workspace */
  };

  var player = new global.Player();  var roll = null;
  var rafId = 0;

  function $(id) { return document.getElementById(id); }
  function show(el, on) { el.classList.toggle("hidden", !on); }
  function pretty(name) { return String(name).replace(/_/g, " "); }

  function banner(text, kind) {
    var div = document.createElement("div");
    div.className = "banner " + (kind || "info");
    div.textContent = text;
    $("banners").appendChild(div);
    return div;
  }

  /* banners that arrive over the job's event stream (tempo fallback, and any
     future ones) must survive entering the workspace, which clears the
     pre-job banners; they are remembered and re-rendered */
  function jobBanner(text, kind) {
    state.jobBanners.push({ text: text, kind: kind });
    banner(text, kind);
  }

  function renderJobBanners() {
    state.jobBanners.forEach(function (b) { banner(b.text, b.kind); });
  }

  function clearBanners() { $("banners").innerHTML = ""; }

  function switchView(name) {
    state.view = name;
    show($("viewUpload"), name === "upload");
    show($("viewWorkspace"), name === "workspace");
    show($("viewLibrary"), name === "library");
  }

  /* ---------- config + instrument chips ---------- */

  global.fetch("/api/config").then(function (r) { return r.json(); }).then(function (cfg) {
    state.config = cfg;
    if (!cfg.ffmpeg) {
      banner("ffmpeg is not on PATH, so earforge cannot decode audio yet.\n" +
        "Install it from https://ffmpeg.org/download.html, then restart earforge.", "error");
    }
    if (!cfg.fluidsynth) {
      banner("fluidsynth is not on PATH: synthesized instrument stems are disabled.\n" +
        "The piano roll still works and stays in sync with the original audio. Install it from\n" +
        "https://github.com/FluidSynth/fluidsynth/releases to play instruments.", "warn");
    }
    if (cfg.model_default) $("modelSelect").value = cfg.model_default;
    updateSheetsButton();
  });

  function updateSheetsButton() {
    var cfg = state.config;
    if (!cfg) return;
    var btn = $("expSheets");
    if (cfg.musescore) {
      btn.disabled = false;
      btn.title = "";
      $("sheetsHint").textContent = "";
    } else {
      btn.disabled = true;
      btn.title = "Needs MuseScore 4+ installed (musescore.org/download). " +
        "Everything else works without it.";
      $("sheetsHint").textContent = "sheets need MuseScore 4+";
    }
  }

  global.fetch("/api/instruments").then(function (r) { return r.json(); }).then(function (d) {
    var chips = $("chips");
    d.instruments.forEach(function (name) {
      var chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = pretty(name);
      chip.dataset.name = name;
      chip.onclick = function () { chip.classList.toggle("on"); };
      chips.appendChild(chip);
    });
    show($("chipsWrap"), true);
  });

  /* ---------- upload ---------- */

  var drop = $("drop");
  drop.onclick = function () { $("fileInput").click(); };
  ["dragover", "dragenter"].forEach(function (ev) {
    drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("over"); });
  });
  ["dragleave", "drop"].forEach(function (ev) {
    drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove("over"); });
  });
  drop.addEventListener("drop", function (e) {
    if (e.dataTransfer.files.length) setFile(e.dataTransfer.files[0]);
  });
  $("fileInput").onchange = function () { if (this.files.length) setFile(this.files[0]); };

  function setFile(f) {
    state.file = f;
    $("fileLabel").textContent = f.name + " (" + (f.size / 1048576).toFixed(1) + " MB)";
    $("goBtn").disabled = false;
  }

  $("goBtn").onclick = function () {
    if (!state.file) return;
    if (!state.config || !state.config.ffmpeg) {
      banner("Cannot transcribe without ffmpeg on PATH.", "error");
      return;
    }
    var chips = Array.prototype.slice.call(document.querySelectorAll(".chip.on"))
      .map(function (c) { return c.dataset.name; });
    var fd = new FormData();
    fd.append("file", state.file);
    fd.append("model", $("modelSelect").value);
    fd.append("instruments", chips.join(","));
    $("goBtn").disabled = true;
    show($("cancelBtn"), true);
    show($("progressWrap"), true);
    $("progressBar").style.width = "0%";
    $("progressText").textContent = "converting audio...";
    resetWorkspace();
    global.fetch("/api/jobs", { method: "POST", body: fd }).then(function (r) {
      if (!r.ok) return r.json().then(function (e) { throw new Error(e.detail || r.statusText); });
      return r.json();
    }).then(function (d) {
      openJobStream(d.id, false);
    }).catch(function (e) {
      jobFailed("upload failed: " + e.message);
    });
  };

  $("cancelBtn").onclick = function () {
    if (state.jobId) {
      global.fetch("/api/jobs/" + state.jobId + "/cancel", { method: "POST" });
      $("progressText").textContent = "cancelling...";
    }
  };

  function jobFailed(message) {
    banner(message, "error");
    $("goBtn").disabled = !state.file;
    show($("cancelBtn"), false);
    show($("progressWrap"), false);
  }

  function resetWorkspace() {
    if (state.es) { state.es.close(); state.es = null; }
    if (rafId) { cancelAnimationFrame(rafId); rafId = 0; }
    player.pause();
    player.setLoop(null);
    roll.setLoop(null);
    state.notes = new Map();
    state.eventsSeen = new Set();
    state.instruments = [];
    state.duration = 0;
    state.grid = null;
    state.stems = {};
    state.originalBuffer = null;
    state.audioCache = {};
    state.stemCache = {};
    state.rate = 1;
    state.progress = null;
    state.firstProgressAt = 0;
    state.jobBanners = [];
    $("rateSlider").value = 100;
    $("rateLabel").textContent = "100%";
    $("rateNote").textContent = "";
    $("loopBtn").textContent = "Set loop: off";
  }

  /* ---------- SSE stream ---------- */

  function openJobStream(jobId, replay) {
    state.jobId = jobId;
    var es = new EventSource("/api/jobs/" + jobId + "/events");
    state.es = es;
    var first = !replay;
    es.onmessage = function (m) {
      var ev;
      try { ev = JSON.parse(m.data); } catch (e) { return; }
      handleEvent(ev, first);
      first = false;
    };
    es.onerror = function () {
      if (state.jobId !== jobId) { es.close(); return; }
      /* The stream ended. If a live run holds this job, this was a drop:
         let EventSource reconnect (replay + live; the UI dedupes). Anything
         else, a finished job or a worker that died mid-run, would loop
         forever on reconnects: finalize from the job record instead. */
      global.fetch("/api/jobs/" + jobId)
        .then(function (r) { return r.json(); })
        .then(function (job) {
          if (state.jobId !== jobId) { es.close(); return; }
          if (job.active) return;
          es.close();
          if (state.es === es) state.es = null;
          if (job.status === "error" && state.view === "upload") {
            jobFailed(job.error || "transcription failed");
          } else if (state.notes.size > 0) {
            state.duration = job.duration_s || state.duration;
            if (job.instruments_found && job.instruments_found.length) {
              state.instruments = job.instruments_found;
            } else if (!state.instruments.length) {
              state.notes.forEach(function (n) {
                if (state.instruments.indexOf(n.instrument) < 0) {
                  state.instruments.push(n.instrument);
                }
              });
            }
            enterWorkspace();
          }
        })
        .catch(function () { /* server unreachable: EventSource retries */ });
    };
  }

  function closeStream() {
    if (state.es) { state.es.close(); state.es = null; }
  }

  function handleEvent(ev, first) {
    switch (ev.type) {
      case "job_start":
        if (first || state.view === "upload") {
          state.duration = ev.duration_s || 0;
          $("progressText").textContent = "loading model...";
        }
        break;
      case "progress":
        state.progress = ev;
        if (!state.firstProgressAt) state.firstProgressAt = performance.now();
        var pct = ev.total ? (ev.completed / ev.total * 100) : 0;
        $("progressBar").style.width = pct.toFixed(1) + "%";
        var eta = "";
        if (ev.completed > 0) {
          var elapsed = (performance.now() - state.firstProgressAt) / 1000;
          eta = " · eta " + Math.max(0, Math.round(elapsed * (ev.total - ev.completed) / ev.completed)) + "s";
        }
        $("progressText").textContent = "transcribing: chunk " + ev.completed + "/" + ev.total + eta;
        break;
      case "start":
        if (!state.eventsSeen.has(ev.index)) {
          state.eventsSeen.add(ev.index);
          state.notes.set(ev.index, {
            index: ev.index, instrument: ev.instrument,
            pitch: ev.pitch, start_time: ev.start_time, end_time: ev.start_time + 0.1,
          });
        }
        break;
      case "end":
        var n = state.notes.get(ev.start_event_index);
        if (n) n.end_time = ev.end_time;
        break;
      case "banner":
        if (!state.eventsSeen.has("banner:" + ev.kind)) {
          state.eventsSeen.add("banner:" + ev.kind);
          jobBanner(
            ev.message, ev.kind === "tempo_fallback" ? "warn" : "info"
          );
        }
        break;
      case "job_complete":
        state.instruments = ev.instruments || [];
        state.grid = (ev.bpm != null) ? {
          bpm: ev.bpm, beatsPerBar: ev.beats_per_bar, firstDownbeat: ev.first_downbeat || 0,
        } : null;
        state.noteCount = ev.note_count;
        enterWorkspace();
        closeStream();
        break;
      case "job_error":
        jobFailed(ev.message);
        closeStream();
        break;
      case "job_cancelled":
        show($("progressWrap"), false);
        $("goBtn").disabled = !state.file;
        show($("cancelBtn"), false);
        banner("Transcription cancelled.", "info");
        closeStream();
        break;
    }
  }

  /* ---------- workspace ---------- */

  function enterWorkspace() {
    clearBanners();
    renderJobBanners();
    switchView("workspace");
    show($("cancelBtn"), false);
    show($("progressWrap"), false);
    $("goBtn").disabled = !state.file;

    var notes = Array.from(state.notes.values()).sort(function (a, b) {
      return a.start_time - b.start_time;
    });
    roll.setData(state.instruments, notes, state.duration, state.grid);
    buildMixer();
    wireTransport();
    loadPlaybackAt(state.rate);
    tick();
  }

  function channelState() {
    var channels = {
      original: {
        gain: parseFloat($("gain" + CSS.escape("original")).value),
        mute: $("mute" + CSS.escape("original")).checked,
        solo: $("solo" + CSS.escape("original")).checked,
      },
    };
    state.instruments.forEach(function (inst) {
      channels[inst] = {
        gain: parseFloat($("gain" + CSS.escape(inst)).value),
        mute: $("mute" + CSS.escape(inst)).checked,
        solo: $("solo" + CSS.escape(inst)).checked,
      };
    });
    return channels;
  }

  function buildMixer() {
    var tbody = $("mixer").querySelector("tbody");
    tbody.innerHTML = "";
    var head = tbody.insertRow();
    head.innerHTML = "<td style='width:180px'>channel</td><td style='width:120px'>solo / mute</td><td>gain</td>";
    head.className = "hint";

    function row(id, label, color, isOriginal) {
      var tr = tbody.insertRow();
      var dot = color ? "<span class='dot' style='background:" + color + "'></span>" : "<span class='dot' style='background:#8fa0b3'></span>";
      var extra = isOriginal ? "" :
        " <button class='small' id='mid" + CSS.escape(id) + "' title='download this instrument as MIDI'>.mid</button>";
      tr.innerHTML =
        "<td>" + dot + label + "<span class='laneSpin' id='spin" + CSS.escape(id) + "'></span></td>" +
        "<td><button class='small solo' id='solo" + CSS.escape(id) + "'>S</button> " +
        "<button class='small mute' id='mute" + CSS.escape(id) + "'>M</button>" + extra + "</td>" +
        "<td><input type='range' min='0' max='2' step='0.05' value='1' id='gain" + CSS.escape(id) + "'></td>";
    }

    row("original", "original audio", null, true);
    state.instruments.forEach(function (inst, i) {
      row(inst, pretty(inst), global.rollPalette[i % global.rollPalette.length], false);
    });

    ["original"].concat(state.instruments).forEach(function (id) {
      ["solo", "mute"].forEach(function (kind) {
        $(kind + CSS.escape(id)).onclick = function () {
          this.classList.toggle("on");
          player.setChannels(channelState());
          player.updateGains();
        };
      });
      $("gain" + CSS.escape(id)).oninput = function () {
        player.setChannels(channelState());
        player.updateGains();
      };
    });
    state.instruments.forEach(function (inst) {
      $("mid" + CSS.escape(inst)).onclick = function () {
        download("/api/jobs/" + state.jobId + "/export/stem/" + encodeURIComponent(inst) + ".mid");
      };
    });
    player.setChannels(channelState());
  }

  function wireTransport() {
    $("playBtn").onclick = togglePlay;
    $("loopClearBtn").onclick = function () {
      player.setLoop(null);
      roll.setLoop(null);
      updateLoopBtn(null);
    };
    $("loopBtn").onclick = function () {
      /* loop the visible region, or the whole take when nothing is shown */
      var a = 0, b = state.duration;
      var view = $("rollScroll");
      var w = view.clientWidth / roll.pxPerSec;
      var pos = player.position();
      a = Math.max(0, pos - w / 3); b = Math.min(state.duration, a + w * 0.8);
      setLoop({ a: a, b: b });
    };
    $("rateSlider").oninput = function () {
      var pct = parseInt(this.value, 10);
      $("rateLabel").textContent = pct + "%";
      $("rateNote").textContent = pct < 100
        ? "audio re-rendered, pitch kept" : "";
      changeRate(pct / 100);
    };
    roll.onSeek = function (t) { player.seek(t); roll.setPosition(t); };
    roll.onLoop = function (loop) { setLoop(loop, true); };
  }

  function setLoop(loop, fromRoll) {
    player.setLoop(loop);
    roll.setLoop(loop);
    updateLoopBtn(loop);
  }
  function updateLoopBtn(loop) {
    $("loopBtn").textContent = loop
      ? "Loop: " + loop.a.toFixed(1) + "s-" + loop.b.toFixed(1) + "s"
      : "Set loop: off";
  }

  function togglePlay() {
    if (!player.playing) player.play(); else player.pause();
    $("playBtn").textContent = player.playing ? "Pause" : "Play";
  }

  function fmtTime(t) {
    var m = Math.floor(t / 60), s = t - m * 60;
    return m + ":" + (s < 10 ? "0" : "") + s.toFixed(1);
  }

  function tick() {
    if (state.view !== "workspace") { rafId = 0; return; }
    var pos = player.position();
    roll.setPosition(pos);
    $("timeDisplay").textContent = fmtTime(pos) + " / " + fmtTime(state.duration);
    $("playBtn").textContent = player.playing ? "Pause" : "Play";
    rafId = requestAnimationFrame(tick);
  }

  /* ---------- audio loading ---------- */

  function decodeBuffer(buf) {
    return new Promise(function (res, rej) {
      var AC = global.AudioContext || global.webkitAudioContext;
      var tmp = new AC();
      tmp.decodeAudioData(buf, function (audio) { tmp.close(); res(audio); },
        function (e) { tmp.close(); rej(e); });
    });
  }

  /* every playback source is fetched at the current speed: the original
     through the server's pitch-preserving atempo render, stems from scaled
     note times. Both are cached per rate so scrubbing the slider back is
     instant. The original is applied the moment it decodes, so Play works
     while stems are still rendering; the stems join when they land. */
  function loadPlaybackAt(rate) {
    var jobs = [];

    if (!state.audioCache[rate]) {
      jobs.push(global.fetch("/api/jobs/" + state.jobId + "/audio" + (rate === 1 ? "" : "?rate=" + rate))
        .then(function (r) {
          if (!r.ok) throw new Error("original audio: " + r.status);
          return r.arrayBuffer();
        })
        .then(decodeBuffer)
        .then(function (audio) {
          state.audioCache[rate] = audio;
          state.originalBuffer = audio;
          /* stems for this rate are not ready yet; playing none beats
             playing the previous rate's stems against the new original */
          if (!state.stemCache[rate]) state.stems = {};
          applyBuffers();
        }));
    }

    if (state.config && state.config.fluidsynth) {
      state.instruments.forEach(function (inst) {
        $("spin" + CSS.escape(inst)).textContent =
          rate === 1 ? "rendering..." : "re-rendering...";
      });
      if (!state.stemCache[rate]) {
        state.instruments.forEach(function (inst) {
          jobs.push(global.fetch("/api/jobs/" + state.jobId + "/stems/" +
            encodeURIComponent(inst) + "?rate=" + rate)
            .then(function (r) {
              if (!r.ok) throw new Error(inst + ": " + r.status);
              return r.arrayBuffer();
            })
            .then(decodeBuffer)
            .then(function (audio) { return [inst, audio]; }));
        });
      }
    }

    return Promise.all(jobs)
      .then(function (results) {
        if (!state.stemCache[rate]) {
          var stems = {};
          results.forEach(function (r) {
            if (Array.isArray(r)) stems[r[0]] = r[1];
          });
          state.stemCache[rate] = stems;
        }
        state.originalBuffer = state.audioCache[rate];
        state.stems = state.stemCache[rate];
        applyBuffers();
      })
      .catch(function (e) {
        if (e && e.message && /original audio/.test(e.message)) {
          banner("Playback audio failed to load: " + e.message, "warn");
        } else {
          banner("Stem rendering failed: " + (e.message || e), "warn");
        }
      })
      .then(function () {
        state.instruments.forEach(function (inst) {
          $("spin" + CSS.escape(inst)).textContent = "";
        });
      });
  }

  function applyBuffers() {
    var buffers = {};
    if (state.originalBuffer) buffers.original = state.originalBuffer;
    for (var k in state.stems) buffers[k] = state.stems[k];
    player.setBuffers(buffers, state.duration, state.rate);
    player.setChannels(channelState());
    player.updateGains();
  }

  /* dragging the slider fires one event per detent; render only once the
     user settles, or every intermediate speed would be rendered server-side */
  var rateTimer = null;

  function changeRate(rate) {
    state.rate = rate;
    if (rateTimer) global.clearTimeout(rateTimer);
    var haveStems = !state.config || !state.config.fluidsynth ||
      state.stemCache[rate];
    if (state.audioCache[rate] && haveStems) {
      state.originalBuffer = state.audioCache[rate];
      state.stems = state.stemCache[rate] || {};
      applyBuffers();
      return;
    }
    rateTimer = global.setTimeout(function () { loadPlaybackAt(rate); }, 350);
  }

  /* ---------- exports ---------- */

  function download(url) {
    var a = document.createElement("a");
    a.href = url;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  $("expMidi").onclick = function () { download("/api/jobs/" + state.jobId + "/export/midi"); };
  $("expJsonl").onclick = function () { download("/api/jobs/" + state.jobId + "/export/jsonl"); };
  $("expSheets").onclick = function () {
    var btn = $("expSheets");
    btn.disabled = true;
    btn.textContent = "Engraving...";
    global.fetch("/api/jobs/" + state.jobId + "/export/sheets", { method: "POST" })
      .then(function (r) {
        if (!r.ok) return r.json().then(function (e) { throw new Error(e.detail || r.statusText); });
        return r.blob();
      })
      .then(function (blob) {
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "sheets.zip";
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(a.href);
      })
      .catch(function (e) { banner("Sheets export failed: " + e.message, "error"); })
      .then(function () { btn.disabled = false; btn.textContent = "Engrave + download sheets (zip)"; });
  };

  /* ---------- library ---------- */

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function loadLibrary() {
    global.fetch("/api/library").then(function (r) { return r.json(); }).then(function (d) {
      var list = $("libraryList");
      list.innerHTML = "";
      $("libraryPath").textContent = "stored under " + (state.config ? state.config.data_dir : "");
      if (!d.jobs.length) {
        list.innerHTML = "<div class='hint'>no transcriptions yet</div>";
        return;
      }
      d.jobs.forEach(function (job) {
        var row = document.createElement("div");
        row.className = "row";
        var dotCls = job.status === "complete" ? "st-ok"
          : job.status === "error" ? "st-err"
          : job.status === "running" ? "st-run" : "st-cxl";
        var dur = job.duration_s ? Math.round(job.duration_s) + "s" : "?";
        var insts = (job.instruments_found || []).map(pretty).join(", ") || "-";
        row.innerHTML =
          "<span class='status-dot " + dotCls + "'></span>" +
          "<span class='name'>" + escapeHtml(job.source_name) + "</span>" +
          "<span class='hint'>" + dur + " · " + job.model + " · " + escapeHtml(insts) + "</span>" +
          "<span class='hint'>" + escapeHtml(job.created_at.replace("T", " ")) + "</span>";
        row.onclick = function (e) {
          if (e.target.tagName === "BUTTON") return;
          reopenJob(job.id);
        };
        var del = document.createElement("button");
        del.className = "small";
        del.textContent = "Delete";
        del.onclick = function (e) {
          e.stopPropagation();
          if (!global.confirm("Delete '" + job.source_name + "' and all its artifacts?")) return;
          global.fetch("/api/jobs/" + job.id, { method: "DELETE" }).then(loadLibrary);
        };
        row.appendChild(del);
        list.appendChild(row);
      });
    });
  }

  function reopenJob(id) {
    resetWorkspace();
    clearBanners();
    global.fetch("/api/jobs/" + id).then(function (r) { return r.json(); }).then(function (job) {
      state.jobId = id;
      state.duration = job.duration_s || 0;
      state.instruments = job.instruments_found || [];
      if (job.bpm != null) {
        state.grid = { bpm: job.bpm, beatsPerBar: job.beats_per_bar, firstDownbeat: 0 };
      }
      /* replay the persisted event stream through the same SSE path;
         job_complete in it builds the workspace */
      openJobStream(id, true);
      setTimeout(function () {
        if (state.notes.size === 0 && job.note_count === 0) {
          enterWorkspace();
        }
      }, 400);
    });
  }

  /* ---------- tabs ---------- */

  $("tabPractice").onclick = function () {
    switchView(state.jobId ? "workspace" : "upload");
    /* the rAF clock stops when the workspace hides; restart it so the
       playhead and time display track audio that kept playing */
    if (state.view === "workspace" && !rafId) tick();
  };
  $("tabLibrary").onclick = function () { switchView("library"); loadLibrary(); };

  /* ---------- boot ---------- */

  roll = new global.Roll($("rollCanvas"), $("rollScroll"));

  global.addEventListener("keydown", function (e) {
    if (state.view !== "workspace") return;
    var tag = e.target.tagName;
    if (tag === "INPUT" || tag === "SELECT" || tag === "BUTTON") return;
    if (e.code === "Space") { e.preventDefault(); togglePlay(); }
    if (e.code === "ArrowLeft" || e.code === "ArrowRight") {
      e.preventDefault();
      var step = e.shiftKey ? 10 : 2;
      var target = Math.max(0, Math.min(
        state.duration, player.position() + (e.code === "ArrowRight" ? step : -step)));
      player.seek(target);
      roll.setPosition(target);
      roll.follow = true;
    }
  });

  global.earforgeState = state;
})(typeof window !== "undefined" ? window : globalThis);
