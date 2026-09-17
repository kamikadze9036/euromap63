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
  var chartLimitInput = document.getElementById("chart-limit-input");
  var chartResetBtn = document.getElementById("chart-reset-zoom");
  var pickerEl = document.getElementById("picker");
  var chartsEl = document.getElementById("charts");
  var pairToggleEl = document.getElementById("pairToggle");
  var rangePresetsEl = document.getElementById("rangePresets");
  var rangeCustomEl = document.getElementById("rangeCustom");
  var rangeCountWrapEl = document.getElementById("rangeCountWrap");
  var rangeFromInput = document.getElementById("rangeFrom");
  var rangeToInput = document.getElementById("rangeTo");
  var rangeApplyBtn = document.getElementById("rangeApply");
  var downtimeTrackEl = document.getElementById("downtimeTrack");
  var downtimePlotAreaEl = document.getElementById("downtimePlotArea");
  var downtimeSummaryEl = document.getElementById("downtimeSummary");
  var downtimeLegendEl = document.getElementById("downtimeLegend");
  var downtimeTooltipEl = document.getElementById("downtimeTooltip");

  var paramDefs = {};
  var lastCycles = [];
  var chartLimit = parseInt(chartLimitInput.value, 10) || 100;
  var ws = null;

  // Vice grafu vedle sebe (small multiples), jeden na kazdy zaskrtnuty
  // parametr - misto puvodniho jednoho grafu s jednim vybranym parametrem.
  // key -> { uplot, container }
  var charts = {};
  var selectedKeys = []; // poradi zaskrtnuti = poradi prideleni barvy
  var colorOf = {};
  var SERIES_VARS = ["--series-1", "--series-2", "--series-3", "--series-4", "--series-5", "--series-6", "--series-7", "--series-8"];

  // Parovani "zmereno" -> "zadano" pro zony, kde existuje obojí zvlast -
  // zadana hodnota se automaticky dokresli do stejneho grafu slabsi
  // carkovanou carou, aniz by si ji uzivatel musel zvlast zaskrtavat.
  // Pokryva jen zivé/uzitecne zony (viz 05_seed_parameters_tmpact_fix a
  // 04_seed_parameters_barrel_extruder_hoppers) - mrtve zony nastroje
  // parovani nemaji, protoze cela rodina je momentalne mimo provoz.
  var PAIRS = {
    "@020Inj1T1.TmpAct": "SetTmpBrlZn[1,1]",
    "@020Inj1T2.TmpAct": "SetTmpBrlZn[1,2]",
    "@020Inj1T3.TmpAct": "SetTmpBrlZn[1,3]",
    "@020Inj1T4.TmpAct": "SetTmpBrlZn[1,4]",
    "@020Inj1T5.TmpAct": "SetTmpBrlZn[1,5]",
    "@020Inj1T11.TmpAct": "SetTmpBrlZn[1,11]",
    "@020Inj1T12.TmpAct": "SetTmpBrlZn[1,12]",
    "@080Ext1TmpGear1\\CycDataTmpZone\\CycVal.PdeValue": "@080Ext1TmpGear1.TmpSet",
    "@080Ext1TmpBush1\\CycDataTmpZone\\CycVal.PdeValue": "@080Ext1TmpBush1.TmpSet",
    "@080Ext1TmpBar1.TmpAct": "@080Ext1TmpBar1.TmpSet",
    "@080Ext1TmpBar2.TmpAct": "@080Ext1TmpBar2.TmpSet",
    "@080Ext1TmpBar3.TmpAct": "@080Ext1TmpBar3.TmpSet",
    "@080Ext1TmpBar4.TmpAct": "@080Ext1TmpBar4.TmpSet",
    "@080Ext1TmpBar5.TmpAct": "@080Ext1TmpBar5.TmpSet",
    "@080Ext1TmpBar6.TmpAct": "@080Ext1TmpBar6.TmpSet",
    "@080Ext1TmpBar7.TmpAct": "@080Ext1TmpBar7.TmpSet",
  };
  var showPairs = pairToggleEl ? pairToggleEl.checked : true;

  // Rezim grafu: "count" = poslednich N cyklu (puvodni chovani), "range" =
  // casove okno (presety 15m..7d nebo vlastni od-do), posilane na API jako
  // since/until misto limit.
  var chartMode = "range";
  var rangeSince = null;
  var rangeUntil = null;
  var activeRangeKey = "24h"; // null kdyz je aktivni vlastni rozsah (fixni od-do)
  var lastDowntimeResult = null; // posledni nactena API odpoved, pro prekresleni pri zoomu grafu

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

  // Vychozi zaskrtnute parametry, aby stranka po nacteni rovnou neco
  // ukazovala (misto prazdneho pickeru bez jedineho grafu).
  var DEFAULT_SELECTED = ["ActTimCyc", "@020Inj1T1.TmpAct", "@080ETGraviPseudoDos.MassPerc"];

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

  // ── uPlot graf s zoomem (kolecko mysi) a panem (tazeni) ──────────────
  // Standardni recept z uPlot demos (zoom-wheel.html), upraveno pro
  // jedinou X osu s casem. Dvojklik resetuje zoom na plny rozsah dat.
  function wheelZoomPlugin(factor, chartKey, onRangeChange) {
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
        setScale: function (u, key) {
          if (key === "x" && onRangeChange) onRangeChange(u.scales.x.min, u.scales.x.max, chartKey);
        },
      },
    };
  }

  function themeColor(name, fallback) {
    var v = getComputedStyle(document.body).getPropertyValue(name).trim();
    return v || fallback;
  }

  // Kdyz nekdo zazoomuje/posune jeden graf, promitne se stejny rozsah do
  // vsech ostatnich (uPlot instance na sobe jinak navzajem nevi) a do
  // pruhu prostoju. Bez podminky by se pri kazdem setScale zacaly grafy
  // volat navzajem donekonecna - setScale je u uPlot no-op (nevyvola
  // znovu hook), kdyz se nastavi uz aktivni hodnota, takze se to samo
  // zastavi po jednom kole.
  function handleRangeChange(minSec, maxSec, sourceKey) {
    Object.keys(charts).forEach(function (k) {
      if (k === sourceKey) return;
      var u = charts[k].uplot;
      if (u.scales.x.min !== minSec || u.scales.x.max !== maxSec) {
        u.setScale("x", { min: minSec, max: maxSec });
      }
    });
    renderDowntimesForRange(minSec * 1000, maxSec * 1000);
  }

  function buildSeriesData(rows, key) {
    var xs = [], ys = [];
    rows.forEach(function (r) {
      var v = getValue(r, key);
      xs.push(new Date(r.time).getTime() / 1000);
      ys.push(v === null ? null : v);
    });
    return [xs, ys];
  }

  function assignColor(key) {
    if (colorOf[key]) return colorOf[key];
    var used = Object.keys(colorOf).length;
    colorOf[key] = "var(" + SERIES_VARS[used % SERIES_VARS.length] + ")";
    return colorOf[key];
  }

  function latestValueText(key) {
    if (!lastCycles.length) return "–";
    var v = getValue(lastCycles[lastCycles.length - 1], key);
    if (v === null) return "–";
    var unit = paramUnit(key);
    var txt = Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2);
    return txt + (unit ? " " + unit : "");
  }

  function buildChartOpts(key, width) {
    var accent = themeColor("--accent", "#2f6fed");
    var muted = themeColor("--muted", "#666");
    var border = themeColor("--border", "#ddd");
    var color = colorOf[key].replace("var(", "").replace(")", "");
    color = themeColor(color, accent);

    var series = [{}, { label: paramLabel(key), stroke: color, width: 2, points: { show: lastCycles.length <= 150, size: 4, stroke: color, fill: color } }];
    var pairKey = PAIRS[key];
    var hasPair = !!(pairKey && paramDefs[pairKey]);
    if (hasPair) {
      series.push({
        label: paramLabel(pairKey) + " (zadáno)",
        stroke: muted,
        width: 1.25,
        dash: [4, 3],
        points: { show: false },
        show: showPairs,
      });
    }

    return {
      width: width || 900,
      height: 160,
      plugins: [wheelZoomPlugin(0.75, key, handleRangeChange)],
      cursor: { drag: { x: false, y: false } },
      scales: { x: { time: true } },
      series: series,
      axes: [
        { stroke: muted, grid: { stroke: border, width: 1 }, ticks: { stroke: border } },
        { stroke: muted, grid: { stroke: border, width: 1 }, ticks: { stroke: border }, size: 52 },
      ],
      legend: { show: true },
    };
  }

  function buildChartDataFor(key) {
    var base = buildSeriesData(lastCycles, key);
    var pairKey = PAIRS[key];
    if (pairKey && paramDefs[pairKey]) {
      var pairSeries = buildSeriesData(lastCycles, pairKey);
      return [base[0], base[1], pairSeries[1]];
    }
    return base;
  }

  function createChartCard(key) {
    var card = document.createElement("div");
    card.className = "chart-card";
    card.dataset.key = key;
    var head = document.createElement("div");
    head.className = "chart-card__head";
    head.innerHTML =
      '<span class="chart-card__title">' + escapeHtml(paramLabel(key)) + '</span>' +
      '<span class="chart-card__now">aktuálně <b>' + latestValueText(key) + '</b></span>';
    var miniEl = document.createElement("div");
    miniEl.className = "chart-card__mini";
    card.appendChild(head);
    card.appendChild(miniEl);
    chartsEl.appendChild(card);

    var text = themeColor("--text", "#1b1f24");
    var u = new uPlot(buildChartOpts(key, miniEl.clientWidth || 900), buildChartDataFor(key), miniEl);
    miniEl.style.color = text;

    // Novy graf se pripoji na aktualni zoom, ktery uz maji ostatni,
    // aby se pri pridani dalsiho parametru neresetoval pohled vsem.
    var existingKey = Object.keys(charts)[0];
    if (existingKey) {
      var ref = charts[existingKey].uplot;
      u.setScale("x", { min: ref.scales.x.min, max: ref.scales.x.max });
    }

    charts[key] = { uplot: u, container: card, mini: miniEl, headNow: head.querySelector(".chart-card__now b") };
  }

  function destroyChartCard(key) {
    var c = charts[key];
    if (!c) return;
    c.uplot.destroy();
    c.container.remove();
    delete charts[key];
  }

  function renderChartsEmptyState() {
    if (Object.keys(charts).length) return;
    chartsEl.innerHTML = '<div class="chart-empty">Zaškrtni parametry vlevo — grafy se objeví tady.</div>';
  }

  function clearChartsEmptyState() {
    var empty = chartsEl.querySelector(".chart-empty");
    if (empty) empty.remove();
  }

  // Kompletni prestavba vsech grafu (zmena okna, reset zoomu) - jednodussi
  // a spolehlivejsi nez upravovat data uz existujicich instanci, protoze
  // se zaroven vyresetuje i jejich zoom na novy plny rozsah.
  function rebuildAllCharts() {
    Object.keys(charts).forEach(destroyChartCard);
    chartsEl.innerHTML = "";
    if (!selectedKeys.length) { renderChartsEmptyState(); return; }
    selectedKeys.forEach(createChartCard);
  }

  function updateChartsData() {
    if (!selectedKeys.length) return;
    selectedKeys.forEach(function (key) {
      var c = charts[key];
      if (!c) return;
      c.uplot.setData(buildChartDataFor(key));
      if (c.headNow) c.headNow.textContent = latestValueText(key);
    });
  }

  // ── zaskrtavaci panel parametru (seskupeny podle kategorie) ──────────
  function buildPicker() {
    var byCat = {};
    Object.keys(paramDefs).forEach(function (key) {
      var def = paramDefs[key];
      if (!def.category) return; // bez kategorie = jen do surove tabulky, ne do pickeru
      (byCat[def.category] = byCat[def.category] || []).push(key);
    });

    pickerEl.innerHTML = "";
    Object.keys(byCat).forEach(function (cat) {
      var block = document.createElement("div");
      block.className = "picker-cat";
      block.innerHTML = '<div class="picker-cat__title">' + escapeHtml(cat) + '</div>';
      byCat[cat].forEach(function (key) {
        var row = document.createElement("label");
        row.className = "picker-row";
        row.innerHTML =
          '<input type="checkbox" data-key="' + escapeHtml(key) + '">' +
          '<span class="picker-row__swatch"></span>' +
          '<span class="picker-row__label">' + escapeHtml(paramDefs[key].label) + '</span>' +
          '<span class="picker-row__value" data-now="' + escapeHtml(key) + '">' + latestValueText(key) + '</span>';
        block.appendChild(row);
      });
      pickerEl.appendChild(block);
    });

    pickerEl.querySelectorAll('input[type="checkbox"]').forEach(function (input) {
      input.addEventListener("change", function () {
        var key = input.dataset.key;
        var row = input.closest(".picker-row");
        if (input.checked) {
          assignColor(key);
          selectedKeys.push(key);
          row.classList.add("checked");
          row.style.setProperty("--row-color", colorOf[key]);
          clearChartsEmptyState();
          createChartCard(key);
        } else {
          selectedKeys = selectedKeys.filter(function (k) { return k !== key; });
          row.classList.remove("checked");
          destroyChartCard(key);
          renderChartsEmptyState();
        }
      });
    });

    // Vychozi vyber pri prvnim naplneni pickeru.
    DEFAULT_SELECTED.forEach(function (key) {
      var input = pickerEl.querySelector('input[data-key="' + key.replace(/"/g, '\\"') + '"]');
      if (input && !input.checked) input.click();
    });
  }

  function refreshPickerValues() {
    pickerEl.querySelectorAll("[data-now]").forEach(function (el) {
      el.textContent = latestValueText(el.dataset.now);
    });
  }

  if (pairToggleEl) {
    pairToggleEl.addEventListener("change", function () {
      showPairs = pairToggleEl.checked;
      Object.keys(charts).forEach(function (key) {
        if (!PAIRS[key]) return;
        charts[key].uplot.setSeries(2, { show: showPairs });
      });
    });
  }

  function loadParamDefs() {
    return fetchJson("/api/parameters?machine=" + encodeURIComponent(MACHINE))
      .then(function (rows) {
        paramDefs = {};
        rows.forEach(function (r) {
          paramDefs[r.param_name] = { label: r.param_label, unit: r.param_unit, category: r.param_category };
        });
        buildPicker();
      })
      .catch(function () {});
  }

  function renderParams(params) {
    paramsTbody.innerHTML = "";
    Object.keys(params).forEach(function (key) {
      var def = paramDefs[key] || {};
      var tr = document.createElement("tr");
      tr.dataset.param = key;

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
    applyParamFilter();
    refreshPickerValues();
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
    updateChartsData();
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
        rebuildAllCharts();
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

  // Zarovna kresici plochu prostoju (downtime-plot-area) presne na plotovaci
  // obdelnik uPlot grafu nad ni (chart.over pokryva jen samotnou plochu dat,
  // bez osy Y a legendy) - jinak by pri jine sirce popisku osy Y (napr. po
  // zoomu, kdy se zmeni rozsah hodnot) sloupce prostoju nesedely na sirku
  // s grafem.
  function syncDowntimePlotArea() {
    var anyKey = Object.keys(charts)[0];
    if (!anyKey || !downtimePlotAreaEl || !downtimeTrackEl) return;
    var refChart = charts[anyKey].uplot;
    if (!refChart || !refChart.over) return;
    var overRect = refChart.over.getBoundingClientRect();
    var trackRect = downtimeTrackEl.getBoundingClientRect();
    downtimePlotAreaEl.style.left = (overRect.left - trackRect.left) + "px";
    downtimePlotAreaEl.style.width = overRect.width + "px";
  }

  function renderDowntimesForRange(minT, maxT) {
    if (!downtimeTrackEl || !downtimePlotAreaEl) return;
    if (!lastDowntimeResult) return;
    syncDowntimePlotArea();
    downtimePlotAreaEl.innerHTML = "";
    if (downtimeLegendEl) downtimeLegendEl.innerHTML = "";
    var span = maxT - minT || 1;

    var segments = lastDowntimeResult.segments.filter(function (seg) {
      var startT = new Date(seg.start).getTime();
      var endT = new Date(seg.end).getTime();
      return endT >= minT && startT <= maxT;
    });

    if (!segments.length) {
      downtimeSummaryEl.textContent = "Bez prostojů v tomto okně";
      return;
    }

    var totalDown = 0;
    var byReason = {}; // reason label -> { color, duration }
    var assignColor = makeReasonColorAssigner();

    segments.forEach(function (seg) {
      var startT = new Date(seg.start).getTime();
      var endT = new Date(seg.end).getTime();
      var clippedStart = Math.max(startT, minT);
      var clippedEnd = Math.min(endT, maxT);
      var visibleDurationS = (clippedEnd - clippedStart) / 1000;
      totalDown += visibleDurationS;
      var label = seg.reason || null;
      var color = assignColor(label);
      var displayLabel = label || "Neznámý důvod";

      var leftPct = Math.max(0, (clippedStart - minT) / span * 100);
      var widthPct = Math.max(0.3, (clippedEnd - clippedStart) / span * 100);
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

      downtimePlotAreaEl.appendChild(bar);

      if (!byReason[displayLabel]) byReason[displayLabel] = { color: color, duration: 0 };
      byReason[displayLabel].duration += visibleDurationS;
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

    downtimeSummaryEl.textContent = segments.length + " prostojů, celkem " + fmtDuration(totalDown);
  }

  function loadDowntimes() {
    var win = resolveWindow();
    if (!win || !downtimeTrackEl) return;
    fetchJson(
      "/api/downtimes?machine=" + encodeURIComponent(MACHINE) +
      "&since=" + encodeURIComponent(win.since) +
      "&until=" + encodeURIComponent(win.until)
    )
      .then(function (result) {
        lastDowntimeResult = result;
        renderDowntimesForRange(new Date(result.since).getTime(), new Date(result.until).getTime());
      })
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
    rebuildAllCharts();
    if (lastDowntimeResult) {
      renderDowntimesForRange(new Date(lastDowntimeResult.since).getTime(), new Date(lastDowntimeResult.until).getTime());
    }
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
    var anyKey = Object.keys(charts)[0];
    if (!anyKey) return;
    Object.keys(charts).forEach(function (key) {
      var c = charts[key];
      c.uplot.setSize({ width: c.mini.clientWidth || 900, height: 160 });
    });
    if (lastDowntimeResult) {
      var refScale = charts[anyKey].uplot.scales.x;
      renderDowntimesForRange(refScale.min * 1000, refScale.max * 1000);
    }
  });

  // Vychozi vyber: 24h (odpovida tlacitku s "active" tridou primo v HTML)
  rangeSince = new Date(Date.now() - RANGE_MS["24h"]).toISOString();
  loadMachineInfo();
  loadParamDefs().then(loadInitial);
  pollStats();
  pollLabels();
  connectWs();
  setInterval(pollStats, 5000);
  setInterval(pollLabels, 5000);
})();
