/* Play-along mixer: original audio + per-instrument stems, solo/mute/gain,
   A/B loop, and 50-100% speed. All public times are RAW seconds (the
   transcription's own timeline); the stretched math stays internal.

   Every playback source is pre-rendered server-side at the chosen speed:
   stems from scaled note times (pitches untouched), the original through
   ffmpeg's atempo (pitch preserved). All buffers therefore share the
   stretched timeline, which is what keeps the sources sample-aligned. */
(function (global) {
  "use strict";

  /* Pure function, unit-tested: the gain a channel gets given every
     channel's {gain, mute, solo}. Any solo solos everything else out. */
  function effectiveGain(channels, id) {
    var me = channels[id];
    if (!me) return 0;
    var anySolo = false;
    for (var k in channels) if (channels[k].solo) { anySolo = true; break; }
    if (anySolo) return me.solo ? me.gain : 0;
    return me.mute ? 0 : me.gain;
  }

  function Player() {
    this.ctx = null;
    this.master = null;
    this.buffers = {};        /* id -> AudioBuffer, all rendered at this.rate */
    this.gains = {};          /* id -> GainNode */
    this.channels = {};       /* id -> {gain, mute, solo} */
    this.rate = 1;
    this.duration = 0;        /* raw seconds */
    this.loop = null;         /* raw seconds {a,b} */
    this.playing = false;
    this.anchorCtxTime = 0;   /* ctx time when the current take started */
    this.anchorPos = 0;       /* raw position of the take start */
    this.live = [];           /* sources of the current+next take */
    this.nextTake = null;     /* {atCtx, fromRaw} next unscheduled loop take */
    this.timer = null;
    this.onEnd = null;
  }

  Player.prototype._ensureCtx = function () {
    if (!this.ctx) {
      var AC = global.AudioContext || global.webkitAudioContext;
      this.ctx = new AC();
      this.master = this.ctx.createGain();
      this.master.connect(this.ctx.destination);
    }
    if (this.ctx.state === "suspended") this.ctx.resume();
  };

  Player.prototype.setChannels = function (channels) {
    /* channels: {id: {gain, mute, solo}}; call before/after setBuffers */
    this.channels = channels;
    this._ensureCtx();
    for (var id in channels) {
      if (!this.gains[id]) {
        this.gains[id] = this.ctx.createGain();
        this.gains[id].connect(this.master);
      }
      this.gains[id].gain.value = effectiveGain(channels, id);
    }
    /* drop channels that no longer exist, so a previous job's lanes stop
       occupying the graph */
    for (var old in this.gains) {
      if (!(old in channels)) {
        try { this.gains[old].disconnect(); } catch (e) { /* fine */ }
        delete this.gains[old];
        delete this.channels[old];
      }
    }
  };

  Player.prototype.updateGains = function () {
    for (var id in this.channels) {
      if (this.gains[id]) this.gains[id].gain.value = effectiveGain(this.channels, id);
    }
  };

  Player.prototype.setBuffers = function (buffers, duration, rate) {
    /* buffers: {id: AudioBuffer}, every one rendered at `rate` */
    var wasPlaying = this.playing;
    var pos = this.position();
    this.pause();
    this.buffers = buffers;
    this.duration = duration;
    this.rate = rate;
    if (wasPlaying) { this.seek(pos); this.play(); }
  };

  Player.prototype._startTake = function (atCtx, fromRaw, spanRaw) {
    /* spanRaw: raw seconds this take covers before the loop point/end */
    for (var id in this.buffers) {
      var buf = this.buffers[id];
      var src = this.ctx.createBufferSource();
      src.buffer = buf;
      src.connect(this.gains[id]);
      var off = Math.min(fromRaw / this.rate, Math.max(0, buf.duration - 0.01));
      src.start(atCtx, off);
      var stopAt = atCtx + spanRaw / this.rate;
      src.stop(stopAt);
      this.live.push({ src: src, stopAt: stopAt });
    }
  };

  Player.prototype._cleanupLive = function () {
    this.live.forEach(function (entry) {
      try { entry.src.stop(); } catch (e) { /* already stopped */ }
      try { entry.src.disconnect(); } catch (e) { /* fine */ }
    });
    this.live = [];
    this.nextTake = null;
  };

  Player.prototype.play = function () {
    if (this.playing || !this.buffers.original && !Object.keys(this.buffers).length) return;
    this._ensureCtx();
    var pos = this.position();
    if (pos >= this.duration - 0.05) pos = this.loop ? this.loop.a : 0;
    if (this.loop && pos >= this.loop.b) pos = this.loop.a;
    this.playing = true;
    this._scheduleTake(this.ctx.currentTime + 0.02, pos);
    this._startScheduler();
  };

  Player.prototype._scheduleTake = function (atCtx, fromRaw, queued) {
    // Look-ahead must not move the audible playhead to a future take.
    if (!queued) {
      this.anchorCtxTime = atCtx;
      this.anchorPos = fromRaw;
    }
    var endRaw = this.loop ? Math.min(this.loop.b, this.duration) : this.duration;
    var spanRaw = Math.max(0.02, endRaw - fromRaw);
    this._startTake(atCtx, fromRaw, spanRaw);
    var nextAt = atCtx + spanRaw / this.rate;
    if (this.loop && this.loop.b <= this.duration) {
      this.nextTake = { atCtx: nextAt, fromRaw: this.loop.a };
    } else {
      var self = this;
      var stopWatch = function () {
        if (!self.playing) return;
        if (self.ctx.currentTime >= nextAt - 0.03) {
          self.pause();
          self.anchorPos = 0;
          self.anchorCtxTime = 0;
          if (self.onEnd) self.onEnd();
          return;
        }
        self._stopTimer = global.setTimeout(stopWatch, 40);
      };
      stopWatch();
    }
  };

  Player.prototype._startScheduler = function () {
    var self = this;
    if (this.timer) global.clearInterval(this.timer);
    this.timer = global.setInterval(function () {
      if (!self.playing || !self.nextTake) return;
      var now = self.ctx.currentTime;
      self.live = self.live.filter(function (entry) {
        if (entry.stopAt > now) return true;
        try { entry.src.disconnect(); } catch (e) { /* already disconnected */ }
        return false;
      });
      while (self.nextTake && self.nextTake.atCtx <= now + 0.45) {
        var nt = self.nextTake;
        self.nextTake = null;
        if (nt.atCtx < now) {
          // A throttled timer may miss entire loops. Resume at the audible phase.
          var span = (self.loop.b - self.loop.a) / self.rate;
          nt.atCtx += Math.floor((now - nt.atCtx) / span) * span;
          nt.fromRaw += (now - nt.atCtx) * self.rate;
          nt.atCtx = now;
        }
        self._scheduleTake(nt.atCtx, nt.fromRaw, true);
      }
    }, 150);
  };

  Player.prototype.pause = function () {
    if (!this.playing) return;
    this.anchorPos = this.position();
    this.playing = false;
    if (this.timer) { global.clearInterval(this.timer); this.timer = null; }
    if (this._stopTimer) { global.clearTimeout(this._stopTimer); this._stopTimer = null; }
    this._cleanupLive();
  };

  Player.prototype.position = function () {
    /* raw seconds; frozen at the anchor while paused */
    if (!this.playing) return this.anchorPos;
    var elapsed = Math.max(0, this.ctx.currentTime - this.anchorCtxTime) * this.rate;
    var pos = this.anchorPos + elapsed;
    if (this.loop && this.loop.b <= this.duration && pos >= this.loop.b) {
      pos = this.loop.a + (pos - this.loop.b) % (this.loop.b - this.loop.a);
    }
    return Math.min(pos, this.duration);
  };

  Player.prototype.seek = function (raw) {
    var wasPlaying = this.playing;
    this.pause();
    this.anchorPos = Math.max(0, Math.min(raw, this.duration));
    if (wasPlaying) this.play();
  };

  Player.prototype.setLoop = function (loop) {
    /* raw seconds {a,b} or null; live-safe */
    var wasPlaying = this.playing;
    var pos = this.position();
    this.pause();
    this.loop = loop;
    if (wasPlaying) {
      this.seek(pos);
      this.play();
    }
  };

  global.Player = Player;
  global.effectiveGain = effectiveGain;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { Player: Player, effectiveGain: effectiveGain };
  }
})(typeof window !== "undefined" ? window : globalThis);
