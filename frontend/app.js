(function () {
  "use strict";

  var cfg = window.EUROMAP63_CONFIG || {};
  var API_BASE = cfg.apiBaseUrl || "http://localhost:8091";
  var WS_BASE = API_BASE.replace(/^http/, "ws");

  var urlParams = new URLSearchParams(window.location.search);
  var MACHINE = urlParams.get("code") || cfg.machineCode || "KM-MC5-01";

  var statusEl = document.getElementById("status");
  var pageTitleEl = document.getElementById("page-title");
  var machineInfoEl = document.getElementById("machineInfo");
  var elCycles = document.getElementById("val-cycles");
  var elCycleTime = document.getElementById("val-cycletime");
  var elAvg = document.getElementById("val-avg");
  var elUpdated = document.getElementById("val-updated");
  var elOrder = document.getElementById("val-order");
  var paramsTbody = document.querySelector("#paramsTable tbody");
  var chartTitleEl = document.getElementById("chart-title");
  var chartLimitInput = document.getElementById("chart-limit-input");
  var chartResetBtn = document.getElementById("chart-reset-zoom");
  var chartContainer = document.getElementById("cycleChartContainer");

  var paramDefs = {};
  var lastCycles = [];
  var chartLimit = parseInt(chartLimitInput.value, 10) || 100;
  var ws = null;
  var chart = null;

  if (pageTitleEl) pageTitleEl.textContent = "Euromap63 — " + MACHINE;

  // Vychozi zobrazeny parametr v grafu - doba cyklu (stejna data jako
  // cycle_time_s, ale pristupuje se k ni pres params jako ke kterekoliv
  // jine hodnote, aby byl vyber jednotny).
  var selected = { key: "ActTimCyc", label: "Doba cyklu", unit: "s" };

  function setStatus(kind, text) {
    statusEl.className = "status status--" + kind;
    statusEl.textContent = text;
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function loadMachineInfo() {
    fetchJson("/api/machines/info?machine=" + encodeURIComponent(MACHINE))
      .then(function (info) {
        var titleParts = [info.cyclades_mac_refmac || MACHINE];
        if (info.cyclades_label) titleParts.push(info.cyclades_label);
        if (pageTitleEl) pageTitleEl.textContent = "Euromap63 — " + titleParts.join(" — ");

        if (!machineInfoEl) return;
        var chips = [];
        if (info.cyclades_label) chips.push(["Typ", info.cyclades_label]);
        if (info.type_label) chips.push(["Kategorie", info.type_label]);
        if (info.atelier) chips.push(["Dílna", info.atelier]);
        if (info.section) chips.push(["Sekce", info.section]);
        if (!chips.length) return;
        machineInfoEl.hidden = false;
        machineInfoEl.innerHTML = chips.map(function (c) {
          return '<span class="info-chip"><span class="info-chip__label">' + escapeHtml(c[0]) +
            '</span><span class="info-chip__value">' + escapeHtml(c[1]) + '</span></span>';
        }).join("");
      })
      .catch(function () {});
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
    initChart();
  }

  function highlightSelectedRow() {
    Array.prototype.forEach.call(paramsTbody.rows, function (tr) {
      tr.classList.toggle("row--selected", tr.dataset.param === selected.key);
    });
  }

  // ── uPlot graf s zoomem (kolecko mysi) a panem (tazeni) ──────────────
  // Standardni recept z uPlot demos (zoom-wheel.html), upraveno pro
  // jedinou X osu s casem. Dvojklik resetuje zoom na plny rozsah dat.
  function wheelZoomPlugin(factor) {
    factor = factor || 0.75;
    var xMin, xMax, xRange;

    function clamp(nRange, nMin, nMax, fRange, fMin, fMax) {
      if (nRange > fRange) { nMin = fMin; nMax = fMax; }
      else if (nMin < fMin) { nMin = fMin; nMax = fMin + nRange; }
      else if (nMax > fMax) { nMax = fMax; nMin = fMax - nRange; }
      return [nMin, nMax];
    }

    return {
      hooks: {
        ready: function (u) {
          xMin = u.scales.x.min;
          xMax = u.scales.x.max;
          xRange = xMax - xMin;

          var over = u.over;

          over.addEventListener("dblclick", function () {
            u.setScale("x", { min: xMin, max: xMax });
          });

          over.addEventListener("mousedown", function (e) {
            if (e.button !== 0) return;
            e.preventDefault();
            var rect = over.getBoundingClientRect();
            var left0 = e.clientX;
            var scXMin0 = u.scales.x.min;
            var scXMax0 = u.scales.x.max;

            function onmove(e2) {
              e2.preventDefault();
              var dx = e2.clientX - left0;
              var dFactor = dx / rect.width;
              var oxRange = scXMax0 - scXMin0;
              var dx2 = oxRange * dFactor;
              u.setScale("x", { min: scXMin0 - dx2, max: scXMax0 - dx2 });
            }
            function onup() {
              document.removeEventListener("mousemove", onmove);
              document.removeEventListener("mouseup", onup);
            }
            document.addEventListener("mousemove", onmove);
            document.addEventListener("mouseup", onup);
          });

          over.addEventListener("wheel", function (e) {
            e.preventDefault();
            var rect = over.getBoundingClientRect();
            var left = u.cursor.left;
            if (left == null) return;
            var leftPct = left / rect.width;
            var xVal = u.posToVal(left, "x");
            var oxRange = u.scales.x.max - u.scales.x.min;
            var nxRange = e.deltaY < 0 ? oxRange * factor : oxRange / factor;
            var nxMin = xVal - leftPct * nxRange;
            var nxMax = nxMin + nxRange;
            var clamped = clamp(nxRange, nxMin, nxMax, xRange, xMin, xMax);
            u.setScale("x", { min: clamped[0], max: clamped[1] });
          });
        },
      },
    };
  }

  function themeColor(name, fallback) {
    var v = getComputedStyle(document.body).getPropertyValue(name).trim();
    return v || fallback;
  }

  function buildChartData(rows) {
    var xs = [], ys = [];
    rows.forEach(function (r) {
      var v = getValue(r, selected.key);
      if (v === null) return;
      xs.push(new Date(r.time).getTime() / 1000);
      ys.push(v);
    });
    return [xs, ys];
  }

  function initChart() {
    var data = buildChartData(lastCycles);
    var accent = themeColor("--accent", "#2f6fed");
    var muted = themeColor("--muted", "#666");
    var border = themeColor("--border", "#ddd");
    var text = themeColor("--text", "#1b1f24");

    var opts = {
      width: chartContainer.clientWidth || 900,
      height: 260,
      plugins: [wheelZoomPlugin(0.75)],
      cursor: { drag: { x: false, y: false } },
      scales: { x: { time: true } },
      series: [
        {},
        {
          label: selected.label,
          stroke: accent,
          width: 2,
          points: { show: data[0].length <= 150, size: 4, stroke: accent, fill: accent },
        },
      ],
      axes: [
        { stroke: muted, grid: { stroke: border, width: 1 }, ticks: { stroke: border } },
        {
          stroke: muted,
          grid: { stroke: border, width: 1 },
          ticks: { stroke: border },
          label: selected.unit || "",
          labelFont: "11px sans-serif",
        },
      ],
      legend: { show: true },
    };

    if (chart) chart.destroy();
    chartContainer.innerHTML = "";
    chart = new uPlot(opts, data, chartContainer);

    // uPlot generuje vlastni text barvou z CSS - u tmaveho rezimu je
    // potreba nastavit barvu popisku/legendy explicitne.
    chartContainer.style.color = text;
  }

  function updateChartData() {
    if (!chart) { initChart(); return; }
    chart.setData(buildChartData(lastCycles));
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
    updateChartData();
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
        initChart();
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

  chartResetBtn.addEventListener("click", function () {
    initChart();
  });

  window.addEventListener("resize", function () {
    if (chart) chart.setSize({ width: chartContainer.clientWidth || 900, height: 260 });
  });

  updateChartTitle();
  loadMachineInfo();
  loadParamDefs().then(loadInitial);
  pollStats();
  connectWs();
  setInterval(pollStats, 5000);
})();
