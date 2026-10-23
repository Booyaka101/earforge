/* Pure grid helpers. All ruler positions remain in original recording seconds. */
(function (global) {
  "use strict";

  function acceptRevision(current, incoming) {
    return (incoming == null ? -1 : incoming) >= current;
  }

  function lines(grid, duration) {
    if (!grid) return [];
    var result = [], meter = grid.beats_per_bar, first = grid.first_downbeat;
    if (!grid.steady && grid.beats && grid.beats.length >= 2) {
      var nearest = 0;
      grid.beats.forEach(function (t, i) {
        if (Math.abs(t - first) < Math.abs(grid.beats[nearest] - first)) nearest = i;
      });
      grid.beats.forEach(function (t, i) {
        var index = i - nearest;
        if (t >= 0 && t <= duration) result.push({ time: t,
          bar: meter && index >= 0 && index % meter === 0 ? index / meter + 1 : null });
      });
    } else if (grid.bpm > 0) {
      var beat = 60 / grid.bpm;
      var start = Math.ceil(-first / beat);
      for (var i = start; first + i * beat <= duration; i++) {
        result.push({ time: first + i * beat,
          bar: meter && i >= 0 && i % meter === 0 ? i / meter + 1 : null });
      }
    }
    return result;
  }

  global.EarforgeGrid = { lines: lines, acceptRevision: acceptRevision };
  if (typeof module !== "undefined" && module.exports) module.exports = global.EarforgeGrid;
})(typeof window !== "undefined" ? window : globalThis);
