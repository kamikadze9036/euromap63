(function () {
  "use strict";

  var cfg = window.EUROMAP63_CONFIG || {};
  var API_BASE = cfg.apiBaseUrl || "http://localhost:8091";
  var WS_BASE = API_BASE.replace(/^http/, "ws");

  var urlParams = new URLSearchParams(window.location.search);
  var MACHINE = urlParams.get("code") || cfg.machineCode || "KM-MC5-01";

  var statusEl = document.getElementById("status");
  var pageTitleEl = document.getElementById("page-title");
  var elCycles = document.getElementById("val-cycles");
  var elCycleTime = document.getElementById("val-cycletime");
  var elAvg = document.getElementById("val-avg");
  var elUpdated = document.getElementById("val-updated");
  var elOrder = document.getElementById("val-order");
  var paramsTbody = document.querySelector("#paramsTable tbody");
  var chartTitleEl = document.getElementById("chart-title");
  var chartLimitInput = document.getElementById("chart-limit-input");
  var canvas = document.getElementById("cycleChart");
  var ctx = canvas.getContext("2d");

  var paramDefs = {};
  var lastCycles = [];
  var chartLimit = parseInt(chartLimitInput.value, 10) || 100;
  var ws = null;

  if (pageTitleEl) pageTitleEl.textContent = "Euromap63 — " + MACHINE;

  // Vychozi zobrazeny parametr v grafu - doba cyklu (stejna data jako
  // cycle_time_s, ale pristupuje se k ni pres params jako ke kterekoliv
  // jine hodnote, aby byl vyber jednotny).
  var selected = { key: "ActTimCyc", label: "Doba cyklu", unit: "s" };

  function setStatus(kind, text) {
    statusEl.className = "status status--" + kind;
    statusEl.textContent = text;
  }

  function fmt(n, digits) {
    if (n === null || n === undefined || Number.isNaN(n)) return "–";
    return Number(n).toFixed(digits === undefined ? 2 : digits);
  }

  function paramLabel(key) {
    var def = paramDefs[key];
    return def && def.label ? def.label : key;
  }

  function paramUnit(key) {
    var def = paramDefs[key];
    return def && def.unit && def.unit !== "-" ? def.unit : "";
  }

  function getValue(row, key) {
    if (!row.params || !(key in row.params)) return null;
    var v = parseFloat(row.params[key]);
    return Number.isNaN(v) ? null : v;
  }

  function updateChartTitle() {
    var unitSuffix = selected.unit ? " (" + selected.unit + ")" : "";
    chartTitleEl.textContent = selected.label + unitSuffix;
  }

  function selectParam(key) {
    selected = { key: key, label: paramLabel(key), unit: paramUnit(key) };
    updateChartTitle();
    highlightSelectedRow();
    drawChart(lastCycles);
  }

  function highlightSelectedRow() {
    Array.prototype.forEach.call(paramsTbody.rows, function (tr) {
      tr.classList.toggle("row--selected", tr.dataset.param === selected.key);
    });
  }

  function drawChart(rows) {
    var w = canvas.width, h = canvas.height, pad = 30;
    ctx.clearRect(0, 0, w, h);

    var values = rows
      .map(function (r) { return getValue(r, selected.key); })
      .filter(function (v) { return v !== null; });
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
      ctx.fillText(val.toFixed(2), 2, y + 3);
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

  function loadParamDefs() {
    return fetchJson("/api/parameters?machine=" + encodeURIComponent(MACHINE))
      .then(function (rows) {
        paramDefs = {};
        rows.forEach(function (r) {
          paramDefs[r.param_name] = { label: r.param_label, unit: r.param_unit };
        });
        updateChartTitle();
      })
      .catch(function () {});
  }

  function renderParams(params) {
    paramsTbody.innerHTML = "";
    Object.keys(params).forEach(function (key) {
      var def = paramDefs[key] || {};
      var tr = document.createElement("tr");
      tr.dataset.param = key;
      tr.title = "Zobrazit historii v grafu";
      tr.addEventListener("click", function () { selectParam(key); });

      var tdKey = document.createElement("td");
      tdKey.textContent = key;

      var tdLabel = document.createElement("td");
      tdLabel.textContent = def.label || "–";

      var tdVal = document.createElement("td");
      tdVal.className = "num";
      tdVal.textContent = params[key];

      var tdUnit = document.createElement("td");
      tdUnit.textContent = def.unit && def.unit !== "-" ? def.unit : "–";

      tr.appendChild(tdKey);
      tr.appendChild(tdLabel);
      tr.appendChild(tdVal);
      tr.appendChild(tdUnit);
      paramsTbody.appendChild(tr);
    });
    highlightSelectedRow();
  }

  function fetchJson(path) {
    return fetch(API_BASE + path).then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    });
  }

  function applyCycle(row) {
    elCycles.textContent = row.cycle_count;
    elCycleTime.textContent = fmt(row.cycle_time_s) + " s";
    elUpdated.textContent = new Date(row.time).toLocaleTimeString("cs-CZ");
    elOrder.textContent = row.order_ref || "–";
    renderParams(row.params || {});

    lastCycles.push(row);
    if (lastCycles.length > chartLimit) {
      lastCycles.splice(0, lastCycles.length - chartLimit);
    }
    drawChart(lastCycles);
  }

  function loadInitial() {
    fetchJson("/api/cycles/latest?machine=" + encodeURIComponent(MACHINE))
      .then(function (row) {
        renderParams(row.params || {});
        elCycles.textContent = row.cycle_count;
        elCycleTime.textContent = fmt(row.cycle_time_s) + " s";
        elUpdated.textContent = new Date(row.time).toLocaleTimeString("cs-CZ");
        elOrder.textContent = row.order_ref || "–";
      })
      .catch(function () {});

    pollChart();
  }

  function pollStats() {
    fetchJson("/api/stats?machine=" + encodeURIComponent(MACHINE) + "&window=100")
      .then(function (s) {
        elAvg.textContent = fmt(s.avg_cycle_time) + " s";
      })
      .catch(function () {});
  }

  function pollChart() {
    fetchJson("/api/cycles?machine=" + encodeURIComponent(MACHINE) + "&limit=" + chartLimit)
      .then(function (rows) {
        lastCycles = rows;
        drawChart(rows);
      })
      .catch(function () {});
  }

  function connectWs() {
    ws = new WebSocket(WS_BASE + "/ws/cycles?machine=" + encodeURIComponent(MACHINE));
    ws.onopen = function () { setStatus("ok", "Online (živě)"); };
    ws.onmessage = function (evt) {
      try {
        var msg = JSON.parse(evt.data);
        if (msg.type === "cycle") applyCycle(msg.data);
      } catch (e) { /* ignore */ }
    };
    ws.onclose = function () {
      setStatus("waiting", "Spojení přerušeno, obnovuji…");
      setTimeout(connectWs, 3000);
    };
    ws.onerror = function () { ws.close(); };
  }

  chartLimitInput.addEventListener("change", function () {
    var v = parseInt(chartLimitInput.value, 10);
    if (!v || v < 10) v = 10;
    if (v > 2000) v = 2000;
    chartLimitInput.value = v;
    chartLimit = v;
    pollChart();
  });

  updateChartTitle();
  loadParamDefs().then(loadInitial);
  pollStats();
  connectWs();
  setInterval(pollStats, 5000);
})();
