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
  var stateBadgeEl = document.getElementById("machine-state-badge");
  var elCycles = document.getElementById("val-cycles");
  var elCycleTime = document.getElementById("val-cycletime");
  var elAvg = document.getElementById("val-avg");
  var elUpdated = document.getElementById("val-updated");
  var elOrder = document.getElementById("val-order");
  var elTool = document.getElementById("val-tool");
  var elLastLabel = document.getElementById("val-last-label");
  var elNextLabel = document.getElementById("val-next-label");
  var paramsTbody = document.querySelector("#paramsTable tbody");
  var paramFilterInput = document.getElementById("paramFilter");
  var paramFilterText = "";
  var chartTitleEl = document.getElementById("chart-title");
  var chartLimitInput = document.getElementById("chart-limit-input");
  var chartResetBtn = document.getElementById("chart-reset-zoom");
  var chartContainer = document.getElementById("cycleChartContainer");
  var rangePresetsEl = document.getElementById("rangePresets");
  var rangeCustomEl = document.getElementById("rangeCustom");
  var rangeCountWrapEl = document.getElementById("rangeCountWrap");
  var rangeFromInput = document.getElementById("rangeFrom");
  var rangeToInput = document.getElementById("rangeTo");
  var rangeApplyBtn = document.getElementById("rangeApply");
  var downtimeTrackEl = document.getElementById("downtimeTrack");
  var downtimeSummaryEl = document.getElementById("downtimeSummary");
  var downtimeLegendEl = document.getElementById("downtimeLegend");
  var downtimeTooltipEl = document.getElementById("downtimeTooltip");

  var paramDefs = {};
  var lastCycles = [];
  var chartLimit = parseInt(chartLimitInput.value, 10) || 100;
  var ws = null;
  var chart = null;

  // Rezim grafu: "count" = poslednich N cyklu (puvodni chovani), "range" =
  // casove okno (presety 15m..7d nebo vlastni od-do), posilane na API jako
  // since/until misto limit.
  var chartMode = "range";
  var rangeSince = null;
  var rangeUntil = null;
  var activeRangeKey = "24h"; // null kdyz je aktivni vlastni rozsah (fixni od-do)

  var RANGE_MS = {
    "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "6h": 6 * 60 * 60 * 1000,
    "8h": 8 * 60 * 60 * 1000,
    "24h": 24 * 60 * 60 * 1000,
    "3d": 3 * 24 * 60 * 60 * 1000,
    "7d": 7 * 24 * 60 * 60 * 1000,
  };

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

  var STATE_LABELS = {
    bezi: "Běží",
    stoji: "Stojí",
    bez_zakazky: "Bez zakázky",
    neznamo: "Neznámo",
  };

  function fmtDuration(sec) {
    if (!sec || sec <= 0) return "–";
    var h = Math.floor(sec / 3600);
    var m = Math.floor((sec % 3600) / 60);
    var s = Math.round(sec % 60);
    if (h > 0) return h + " h " + m + " min";
    if (m > 0) return m + " min " + s + " s";
    return s + " s";
  }

  // Kategoricka paleta pro duvody prostoju (8 fixnich slotu, viz style.css
  // --series-1..8). Barva se prideluje podle poradi PRVNIHO vyskytu
  // duvodu v aktualne zobrazenem okne (ne hashem kodu - napr. "Porucha
  // nastroje" kod 5 a "Preventivni udrzba" kod 29 by na modulo-8 hash
  // spadly do stejneho slotu). Diky tomu se nikdy nesrazi dva duvody,
  // co se zobrazuji soucasne; napric ruznymi okny se barva stejneho
  // duvodu muze lisit, pokud se zmeni mnozina soucasne zobrazenych duvodu.
  var UNKNOWN_REASON_COLOR = "var(--muted)";

  function makeReasonColorAssigner() {
    var order = [];
    return function (label) {
      if (label === null || label === undefined) return UNKNOWN_REASON_COLOR;
      var idx = order.indexOf(label);
      if (idx === -1) {
        idx = order.length;
        order.push(label);
      }
      return "var(--series-" + ((idx % 8) + 1) + ")";
    };
  }

  function updateStateBadge(m) {
    if (!stateBadgeEl) return;
    stateBadgeEl.hidden = false;
    stateBadgeEl.className = "state-badge state-badge--" + (m.state || "neznamo");
    stateBadgeEl.textContent = STATE_LABELS[m.state] || m.state || "–";

    var downtimeChip = document.getElementById("chip-downtime");
    if (m.state === "stoji") {
      var text = escapeHtml(m.stop_reason || "neurčeno") + " (" + fmtDuration(m.stop_duration_s) + ")";
      if (!downtimeChip) {
        downtimeChip = document.createElement("span");
        downtimeChip.id = "chip-downtime";
        downtimeChip.className = "info-chip info-chip--downtime";
        if (machineInfoEl) {
          machineInfoEl.hidden = false;
          machineInfoEl.appendChild(downtimeChip);
        }
      }
      downtimeChip.innerHTML = '<span class="info-chip__label">Prostoj</span><span class="info-chip__value">' + text + '</span>';
    } else if (downtimeChip) {
      downtimeChip.remove();
    }
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
      height: 420,
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
      tdKey.className = "truncate";
      tdKey.textContent = key;
      tdKey.title = key;

      var tdLabel = document.createElement("td");
      tdLabel.className = "truncate";
      tdLabel.textContent = def.label || "–";
      tdLabel.title = def.label || "";

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
    applyParamFilter();
  }

  function applyParamFilter() {
    var needle = paramFilterText.trim().toLowerCase();
    Array.prototype.forEach.call(paramsTbody.rows, function (tr) {
      if (!needle) { tr.hidden = false; return; }
      var key = tr.dataset.param || "";
      var label = tr.children[1] ? tr.children[1].textContent : "";
      var hay = (key + " " + label).toLowerCase();
      tr.hidden = hay.indexOf(needle) === -1;
    });
  }

  if (paramFilterInput) {
    paramFilterInput.addEventListener("input", function () {
      paramFilterText = paramFilterInput.value;
      applyParamFilter();
    });
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
    if (chartMode === "count" && lastCycles.length > chartLimit) {
      lastCycles.splice(0, lastCycles.length - chartLimit);
    } else if (chartMode === "range" && activeRangeKey) {
      // Presetove okno ("poslednich X") plyne s casem - posun levou hranici
      // dopredu a zahod body, co uz z ni vypadly, aby graf pri dlouho
      // otevrene strance porad ukazoval "poslednich X", ne zamrzly usek.
      rangeSince = new Date(Date.now() - RANGE_MS[activeRangeKey]).toISOString();
      var minTime = Date.now() - RANGE_MS[activeRangeKey];
      while (lastCycles.length && new Date(lastCycles[0].time).getTime() < minTime) {
        lastCycles.shift();
      }
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

  function pollLabels() {
    if (!elLastLabel || !elNextLabel) return;
    fetchJson("/api/machines/status")
      .then(function (machines) {
        var m = machines.find(function (x) { return x.machine_code === MACHINE; });
        if (!m) return;
        elLastLabel.textContent = m.last_label || "–";
        elNextLabel.textContent = m.next_label || "–";
        if (elTool) {
          elTool.textContent = m.tool_ref ? (m.tool_label ? m.tool_ref + " — " + m.tool_label : m.tool_ref) : "–";
        }
        updateStateBadge(m);
      })
      .catch(function () {});
  }

  function pollChart() {
    var url;
    if (chartMode === "range" && rangeSince) {
      url = "/api/cycles?machine=" + encodeURIComponent(MACHINE) + "&since=" + encodeURIComponent(rangeSince);
      if (rangeUntil) url += "&until=" + encodeURIComponent(rangeUntil);
    } else {
      url = "/api/cycles?machine=" + encodeURIComponent(MACHINE) + "&limit=" + chartLimit;
    }
    fetchJson(url)
      .then(function (rows) {
        lastCycles = rows;
        initChart();
        loadDowntimes();
      })
      .catch(function () {});
  }

  function resolveWindow() {
    if (chartMode === "range" && rangeSince) {
      return { since: rangeSince, until: rangeUntil || new Date().toISOString() };
    }
    if (lastCycles.length) {
      return { since: lastCycles[0].time, until: lastCycles[lastCycles.length - 1].time };
    }
    return null;
  }

  function showDowntimeTooltip(evt, html) {
    var tip = downtimeTooltipEl;
    if (!tip) return;
    tip.innerHTML = html;
    tip.hidden = false;
    var x = evt.clientX + 14;
    var y = evt.clientY + 14;
    var maxX = window.innerWidth - tip.offsetWidth - 8;
    var maxY = window.innerHeight - tip.offsetHeight - 8;
    tip.style.left = Math.max(4, Math.min(x, maxX)) + "px";
    tip.style.top = Math.max(4, Math.min(y, maxY)) + "px";
  }

  function hideDowntimeTooltip() {
    if (downtimeTooltipEl) downtimeTooltipEl.hidden = true;
  }

  function renderDowntimes(result) {
    if (!downtimeTrackEl) return;
    downtimeTrackEl.innerHTML = "";
    if (downtimeLegendEl) downtimeLegendEl.innerHTML = "";
    var minT = new Date(result.since).getTime();
    var maxT = new Date(result.until).getTime();
    var span = maxT - minT || 1;

    if (!result.segments.length) {
      downtimeSummaryEl.textContent = "Bez prostojů v tomto okně";
      return;
    }

    var totalDown = 0;
    var byReason = {}; // reason label -> { color, duration }
    var assignColor = makeReasonColorAssigner();

    result.segments.forEach(function (seg) {
      totalDown += seg.duration_s;
      var label = seg.reason || null;
      var color = assignColor(label);
      var displayLabel = label || "Neznámý důvod";

      var startT = new Date(seg.start).getTime();
      var endT = new Date(seg.end).getTime();
      var leftPct = Math.max(0, (startT - minT) / span * 100);
      var widthPct = Math.max(0.3, (endT - startT) / span * 100);
      var bar = document.createElement("div");
      bar.className = "downtime-bar";
      bar.style.left = leftPct + "%";
      bar.style.width = widthPct + "%";
      bar.style.background = color;

      var tooltipHtml =
        '<strong>' + escapeHtml(displayLabel) + '</strong><br>' +
        escapeHtml(new Date(seg.start).toLocaleString("cs-CZ")) + ' – ' + escapeHtml(new Date(seg.end).toLocaleString("cs-CZ")) + '<br>' +
        'Délka: ' + escapeHtml(fmtDuration(seg.duration_s));
      bar.addEventListener("mouseenter", function (evt) { showDowntimeTooltip(evt, tooltipHtml); });
      bar.addEventListener("mousemove", function (evt) { showDowntimeTooltip(evt, tooltipHtml); });
      bar.addEventListener("mouseleave", hideDowntimeTooltip);

      downtimeTrackEl.appendChild(bar);

      if (!byReason[displayLabel]) byReason[displayLabel] = { color: color, duration: 0 };
      byReason[displayLabel].duration += seg.duration_s;
    });

    if (downtimeLegendEl) {
      Object.keys(byReason).sort(function (a, b) { return byReason[b].duration - byReason[a].duration; })
        .forEach(function (label) {
          var info = byReason[label];
          var item = document.createElement("span");
          item.className = "downtime-legend__item";
          item.innerHTML =
            '<span class="downtime-legend__swatch" style="background:' + info.color + '"></span>' +
            escapeHtml(label) +
            ' <span class="downtime-legend__duration">(' + fmtDuration(info.duration) + ')</span>';
          downtimeLegendEl.appendChild(item);
        });
    }

    downtimeSummaryEl.textContent = result.segments.length + " prostojů, celkem " + fmtDuration(totalDown);
  }

  function loadDowntimes() {
    var win = resolveWindow();
    if (!win || !downtimeTrackEl) return;
    fetchJson(
      "/api/downtimes?machine=" + encodeURIComponent(MACHINE) +
      "&since=" + encodeURIComponent(win.since) +
      "&until=" + encodeURIComponent(win.until)
    )
      .then(function (result) { renderDowntimes(result); })
      .catch(function () {
        if (downtimeSummaryEl) downtimeSummaryEl.textContent = "Chyba načtení";
      });
  }

  function setActivePresetButton(key) {
    Array.prototype.forEach.call(rangePresetsEl.querySelectorAll("button"), function (btn) {
      btn.classList.toggle("active", btn.dataset.range === key);
    });
  }

  function selectPreset(key) {
    if (key === "count") {
      chartMode = "count";
      activeRangeKey = null;
      rangeCustomEl.hidden = true;
      rangeCountWrapEl.hidden = false;
      setActivePresetButton(key);
      pollChart();
      return;
    }
    if (key === "custom") {
      activeRangeKey = null;
      rangeCountWrapEl.hidden = true;
      rangeCustomEl.hidden = false;
      setActivePresetButton(key);
      return;
    }
    chartMode = "range";
    activeRangeKey = key;
    rangeCustomEl.hidden = true;
    rangeCountWrapEl.hidden = true;
    rangeSince = new Date(Date.now() - RANGE_MS[key]).toISOString();
    rangeUntil = null;
    setActivePresetButton(key);
    pollChart();
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

  rangePresetsEl.addEventListener("click", function (evt) {
    var btn = evt.target.closest("button[data-range]");
    if (!btn) return;
    selectPreset(btn.dataset.range);
  });

  rangeApplyBtn.addEventListener("click", function () {
    if (!rangeFromInput.value) return;
    chartMode = "range";
    rangeSince = new Date(rangeFromInput.value).toISOString();
    rangeUntil = rangeToInput.value ? new Date(rangeToInput.value).toISOString() : null;
    pollChart();
  });

  window.addEventListener("resize", function () {
    if (chart) chart.setSize({ width: chartContainer.clientWidth || 900, height: 420 });
  });

  // Vychozi vyber: 24h (odpovida tlacitku s "active" tridou primo v HTML)
  rangeSince = new Date(Date.now() - RANGE_MS["24h"]).toISOString();

  updateChartTitle();
  loadMachineInfo();
  loadParamDefs().then(loadInitial);
  pollStats();
  pollLabels();
  connectWs();
  setInterval(pollStats, 5000);
  setInterval(pollLabels, 5000);
})();
