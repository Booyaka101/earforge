/* Piano roll: one colored lane per instrument, ruler with bars, loop region. */
(function (global) {
  "use strict";

  var PALETTE = [
    "#ffb454", "#6ea8fe", "#7ddb8a", "#ff8fa3", "#c792ea", "#89ddff",
    "#f78c6c", "#a6e22e", "#ffcb6b", "#82aaff", "#ecbe7b", "#b2ccd6",
  ];
  var PITCH_MIN = 21, PITCH_MAX = 108;
  var RULER_H = 26;

  function colorFor(index) { return PALETTE[index % PALETTE.length]; }

  function fmtTime(t) {
    var m = Math.floor(t / 60), s = t - m * 60;
    return m + ":" + (s < 10 ? "0" : "") + s.toFixed(1);
  }

  function Roll(canvas, scrollEl) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.scrollEl = scrollEl;
    this.instruments = [];
    this.notes = [];          /* [{instrument, pitch, start_time, end_time, index}] */
    this.duration = 0;
    this.grid = null;         /* {bpm, beatsPerBar, firstDownbeat} or null */
    this.loop = null;         /* {a, b} raw seconds */
    this.dragStart = null;
    this.onSeek = null;
    this.onLoop = null;
    this.pxPerSec = 60;
    this.follow = true;
    this.position = -1;       /* raw seconds; -1 hides the playhead */
    /* static scene (lanes, ruler, notes) is cached here and only re-rendered
       when the data or layout changes; the animation frame then just blits it
       and adds the moving parts. A 3000-note song does not re-draw 3000
       rectangles sixty times a second. */
    this._layer = document.createElement("canvas");
    this._layerDirty = true;

    var self = this;
    this.canvas.addEventListener("mousedown", function (e) {
      var x = e.offsetX;
      if (e.offsetY <= RULER_H) {
        self.dragStart = { x: x, moved: false };
        self._pendingLoop = null;
      } else if (self.onSeek) {
        self.onSeek(x / self.pxPerSec);
        self.follow = true;
      }
    });
    this.canvas.addEventListener("mousemove", function (e) {
      if (!self.dragStart) return;
      var x = Math.max(0, Math.min(self.canvas.width, e.offsetX));
      if (Math.abs(x - self.dragStart.x) > 4) self.dragStart.moved = true;
      if (self.dragStart.moved) {
        var a = self.dragStart.x / self.pxPerSec, b = x / self.pxPerSec;
        self._pendingLoop = { a: Math.min(a, b), b: Math.max(a, b) };
        self.draw();
      }
    });
    global.addEventListener("mouseup", function () {
      if (!self.dragStart) return;
      if (self.dragStart.moved && self._pendingLoop) {
        self.loop = self._pendingLoop;
        if (self.onLoop) self.onLoop(self.loop);
      } else if (self.dragStart.y === undefined) {
        /* plain click on the ruler: seek */
        if (self.onSeek) { self.onSeek(self.dragStart.x / self.pxPerSec); self.follow = true; }
      }
      self.dragStart = null;
      self._pendingLoop = null;
      self.draw();
    });
    this.canvas.addEventListener("dblclick", function () {
      self.loop = null;
      if (self.onLoop) self.onLoop(null);
      self.draw();
    });
    /* the canvas sizes to its container; a resized window has to relayout */
    global.addEventListener("resize", function () {
      if (self.instruments.length) {
        self.layout();
        self.draw();
      }
    });
  }

  Roll.prototype.setData = function (instruments, notes, duration, grid) {
    this.instruments = instruments;
    this.notes = notes;
    this.duration = duration;
    this.grid = grid || null;
    this.layout();
    this.draw();
  };

  Roll.prototype.layout = function () {
    /* cap total width so long recordings stay inside the browser's canvas cap */
    this.pxPerSec = Math.min(60, 28000 / Math.max(1, this.duration));
    var dpr = global.devicePixelRatio || 1;
    var w = Math.max(
      this.scrollEl.clientWidth,
      Math.ceil((this.duration + 4) * this.pxPerSec)
    );
    var laneH = Math.max(34, Math.min(64, 520 / Math.max(1, this.instruments.length)));
    var h = RULER_H + this.instruments.length * laneH + 4;
    this.canvas.style.width = w + "px";
    this.canvas.style.height = h + "px";
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.laneH = laneH;
    this._layer.width = this.canvas.width;
    this._layer.height = this.canvas.height;
    this._layerDirty = true;
  };

  Roll.prototype.activeNote = function (note, position) {
    return position >= note.start_time && position <= note.end_time;
  };

  Roll.prototype._renderLayer = function () {
    var dpr = global.devicePixelRatio || 1;
    var ctx = this._layer.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    var w = this._layer.width / dpr;
    var h = this._layer.height / dpr;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#0c1014";
    ctx.fillRect(0, 0, w, h);

    /* ruler: seconds + bars when a grid is known */
    ctx.fillStyle = "#141b22";
    ctx.fillRect(0, 0, w, RULER_H);
    ctx.strokeStyle = "#2a3441";
    ctx.beginPath(); ctx.moveTo(0, RULER_H + 0.5); ctx.lineTo(w, RULER_H + 0.5); ctx.stroke();
    ctx.font = "10px system-ui";
    ctx.fillStyle = "#8fa0b3";
    var secStep = this.pxPerSec > 25 ? 5 : 15;
    for (var t = 0; t <= this.duration + 4; t += secStep) {
      var x = t * this.pxPerSec;
      ctx.fillText(fmtTime(t), x + 3, 12);
      ctx.strokeStyle = "#1c2530";
      ctx.beginPath(); ctx.moveTo(x, RULER_H); ctx.lineTo(x, h); ctx.stroke();
    }
    if (this.grid && this.grid.beatsPerBar) {
      var beat = 60 / this.grid.bpm, bar = beat * this.grid.beatsPerBar;
      ctx.fillStyle = "#54677c";
      for (var b = Math.max(0, this.grid.firstDownbeat); b < this.duration + 4; b += bar) {
        var bx = b * this.pxPerSec;
        ctx.fillText(String(Math.round((b - this.grid.firstDownbeat) / bar) + 1), bx + 2, 23);
        ctx.strokeStyle = "#33414f";
        ctx.beginPath(); ctx.moveTo(bx, RULER_H); ctx.lineTo(bx, h); ctx.stroke();
      }
    }

    /* lanes */
    this.instruments.forEach(function (name, i) {
      var y0 = RULER_H + i * this.laneH;
      if (i % 2 === 1) {
        ctx.fillStyle = "#10161c";
        ctx.fillRect(0, y0, w, this.laneH);
      }
      ctx.fillStyle = colorFor(i);
      ctx.fillText(String(name).replace(/_/g, " "), 6, y0 + 12);
      ctx.strokeStyle = "#1c2530";
      ctx.beginPath(); ctx.moveTo(0, y0 + 0.5); ctx.lineTo(w, y0 + 0.5); ctx.stroke();
    }, this);

    /* notes */
    this.notes.forEach(function (n) {
      var li = this.instruments.indexOf(n.instrument);
      if (li < 0) return;
      var y0 = RULER_H + li * this.laneH;
      var rel = (n.pitch - PITCH_MIN) / (PITCH_MAX - PITCH_MIN);
      var nh = Math.max(3, this.laneH / 26);
      var y = y0 + this.laneH - 4 - rel * (this.laneH - 8) - nh / 2;
      var x = n.start_time * this.pxPerSec;
      var wN = Math.max(2.5, (n.end_time - n.start_time) * this.pxPerSec);
      ctx.fillStyle = colorFor(li);
      ctx.globalAlpha = 0.66;
      ctx.fillRect(x, y, wN, nh);
      ctx.globalAlpha = 1;
    }, this);
    this._layerDirty = false;
  };

  Roll.prototype.draw = function () {
    if (this._layerDirty) this._renderLayer();
    var ctx = this.ctx;
    var dpr = global.devicePixelRatio || 1;
    var w = this.canvas.width / dpr;
    var h = this.canvas.height / dpr;
    ctx.clearRect(0, 0, w, h);
    ctx.drawImage(this._layer, 0, 0, w, h);

    /* loop region (live: the drag preview draws here too) */
    var loop = this._pendingLoop || this.loop;
    if (loop) {
      ctx.fillStyle = "rgba(255,180,84,0.10)";
      ctx.fillRect(loop.a * this.pxPerSec, 0,
        (loop.b - loop.a) * this.pxPerSec, h);
      ctx.strokeStyle = "rgba(255,180,84,0.55)";
      ctx.lineWidth = 1;
      [loop.a, loop.b].forEach(function (t) {
        var x = t * this.pxPerSec;
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
      }, this);
    }

    /* active notes at full brightness over their dim layer copies, then the
       playhead */
    var pos = this.position;
    if (pos >= 0) {
      this.notes.forEach(function (n) {
        if (!this.activeNote(n, pos)) return;
        var li = this.instruments.indexOf(n.instrument);
        if (li < 0) return;
        var y0 = RULER_H + li * this.laneH;
        var rel = (n.pitch - PITCH_MIN) / (PITCH_MAX - PITCH_MIN);
        var nh = Math.max(3, this.laneH / 26);
        var y = y0 + this.laneH - 4 - rel * (this.laneH - 8) - nh / 2;
        ctx.fillStyle = colorFor(li);
        ctx.fillRect(n.start_time * this.pxPerSec, y,
          Math.max(2.5, (n.end_time - n.start_time) * this.pxPerSec), nh);
      }, this);
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 1.4;
      var px = pos * this.pxPerSec;
      ctx.beginPath(); ctx.moveTo(px, 0); ctx.lineTo(px, h); ctx.stroke();
    }
  };

  Roll.prototype.setPosition = function (rawPos) {
    this.position = rawPos;
    if (this.follow && rawPos >= 0) {
      var x = rawPos * this.pxPerSec;
      var view = this.scrollEl;
      if (x < view.scrollLeft + 40 || x > view.scrollLeft + view.clientWidth - 80) {
        view.scrollLeft = Math.max(0, x - view.clientWidth * 0.3);
      }
    }
    this.draw();
  };

  Roll.prototype.setLoop = function (loop) { this.loop = loop; this.draw(); };

  global.Roll = Roll;
  global.rollPalette = PALETTE;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { Roll: Roll, colorFor: colorFor, fmtTime: fmtTime };
  }
})(typeof window !== "undefined" ? window : globalThis);
