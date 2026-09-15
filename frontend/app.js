(function () {
  "use strict";

  var cfg = window.EUROMAP63_CONFIG || {};
  var API_BASE = cfg.apiBaseUrl || "http://localhost:8091";
  var MACHINE = cfg.machineCode || "KM-MC5-01";
  var CHART_LIMIT = 100;

  var statusEl = document.getElementById("status");
  var elCycles = document.getElementById("val-cycles");
  var elCycleTime = document.getElementById("val-cycletime");
  var elAvg = document.getElementById("val-avg");
  var elUpdated = document.getElementById("val-updated");
  var paramsTbody = document.querySelector("#paramsTable tbody");
  var canvas = document.getElementById("cycleChart");
  var ctx = canvas.getContext("2d");

  function setStatus(kind, text) {
    statusEl.className = "status status--" + kind;
    statusEl.textContent = text;
  }

  function fmt(n, digits) {
    if (n === null || n === undefined || Number.isNaN(n)) return "–";
    return Number(n).toFixed(digits === undefined ? 2 : digits);
  }

  function drawChart(rows) {
    var w = canvas.width, h = canvas.height, pad = 30;
    ctx.clearRect(0, 0, w, h);

    var values = rows
      .map(function (r) { return r.cycle_time_s; })
      .filter(function (v) { return v !== null && v !== undefined; });
    if (values.length < 2) return;

    var min = Math.min.apply(null, values);
    var max = Math.max.apply(null, values);
    if (min === max) { min -= 1; max += 1; }

    var style = getComputedStyle(document.body);
    var gridColor = style.getPropertyValue("--border").trim() || "#ddd";
    var lineColor = style.getPropertyValue("--accent").trim() || "#2f6fed";
    var textColor = style.getPropertyValue("--muted").trim() || "#666";

    ctx.strokeStyle = gridColor;
    ctx.fillStyle = textColor;
    ctx.font = "11px sans-serif";
    ctx.lineWidth = 1;
    var gridLines = 4;
    for (var g = 0; g <= gridLines; g++) {
      var y = pad + (h - 2 * pad) * (g / gridLines);
      ctx.beginPath();
      ctx.moveTo(pad, y);
      ctx.lineTo(w - pad, y);
      ctx.stroke();
      var val = max - (max - min) * (g / gridLines);
      ctx.fillText(val.toFixed(1) + "s", 2, y + 3);
    }

    ctx.strokeStyle = lineColor;
    ctx.lineWidth = 2;
    ctx.beginPath();
    values.forEach(function (v, i) {
      var x = pad + (w - 2 * pad) * (i / (values.length - 1));
      var y = pad + (h - 2 * pad) * (1 - (v - min) / (max - min));
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  function renderParams(params) {
    paramsTbody.innerHTML = "";
    Object.keys(params).forEach(function (key) {
      var tr = document.createElement("tr");
      var tdKey = document.createElement("td");
      tdKey.textContent = key;
      var tdVal = document.createElement("td");
      tdVal.className = "num";
      tdVal.textContent = params[key];
      tr.appendChild(tdKey);
      tr.appendChild(tdVal);
      paramsTbody.appendChild(tr);
    });
  }

  function fetchJson(path) {
    return fetch(API_BASE + path).then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    });
  }

  function pollLatest() {
    fetchJson("/api/cycles/latest?machine=" + encodeURIComponent(MACHINE))
      .then(function (row) {
        setStatus("ok", "Online");
        elCycles.textContent = row.cycle_count;
        elCycleTime.textContent = fmt(row.cycle_time_s) + " s";
        elUpdated.textContent = new Date(row.time).toLocaleTimeString("cs-CZ");
        renderParams(row.params || {});
      })
      .catch(function (err) {
        setStatus("err", "Chyba: " + err.message);
      });

    fetchJson("/api/stats?machine=" + encodeURIComponent(MACHINE) + "&window=100")
      .then(function (s) {
        elAvg.textContent = fmt(s.avg_cycle_time) + " s";
      })
      .catch(function () {});
  }

  function pollChart() {
    fetchJson("/api/cycles?machine=" + encodeURIComponent(MACHINE) + "&limit=" + CHART_LIMIT)
      .then(drawChart)
      .catch(function () {});
  }

  pollLatest();
  pollChart();
  setInterval(pollLatest, 3000);
  setInterval(pollChart, 10000);
})();
