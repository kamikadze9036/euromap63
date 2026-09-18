(function () {
  "use strict";

  var cfg = window.EUROMAP63_CONFIG || {};
  var API_BASE = cfg.apiBaseUrl || "http://localhost:8091";
  var WS_BASE = API_BASE.replace(/^http/, "ws");

  var statusEl = document.getElementById("status");
  var gridEl = document.getElementById("machineGrid");
  var sockets = {};
  var latestByMachine = {};

  var STATE_LABELS = {
    bezi: "Běží",
    stoji: "Stojí",
    bez_zakazky: "Bez zakázky",
    neznamo: "Neznámo",
  };

  function setStatus(kind, text) {
    statusEl.className = "status status--" + kind;
    statusEl.textContent = text;
  }

  function formatTool(m) {
    if (!m.tool_ref) return "–";
    return m.tool_label ? m.tool_ref + " — " + m.tool_label : m.tool_ref;
  }

  function fmt(n, digits) {
    if (n === null || n === undefined || Number.isNaN(n)) return "–";
    return Number(n).toFixed(digits === undefined ? 2 : digits);
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function fmtDuration(sec) {
    if (!sec || sec <= 0) return "–";
    var m = Math.floor(sec / 60);
    var s = Math.round(sec % 60);
    return m > 0 ? (m + " min " + s + " s") : (s + " s");
  }

  function fetchJson(path) {
    return fetch(API_BASE + path).then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    });
  }

  function cardId(machineCode) {
    return "machine-card-" + machineCode.replace(/[^a-zA-Z0-9_-]/g, "_");
  }

  // Karty se řadí podle čísla lisu (tonáž-pořadí, např. "P1100-03" ->
  // [1100, 3]), ne abecedně podle machine_code/cyclades_mac_refmac -
  // prostá abeceda by řadila "P1000-11" před "P220-002" (řetězcově '1' <
  // '2'), i když 220 < 1000. Stroje bez rozpoznatelného vzoru (zatím žádné)
  // spadnou na konec, seřazené abecedně mezi sebou.
  function pressSortKey(m) {
    var ref = m.cyclades_mac_refmac || m.machine_code || "";
    var match = /^P?(\d+)-(\d+)/.exec(ref);
    if (match) return [0, parseInt(match[1], 10), parseInt(match[2], 10), ref];
    return [1, 0, 0, ref];
  }

  function sortMachinesByNumber(machines) {
    return machines.slice().sort(function (a, b) {
      var ka = pressSortKey(a), kb = pressSortKey(b);
      for (var i = 0; i < ka.length; i++) {
        if (ka[i] < kb[i]) return -1;
        if (ka[i] > kb[i]) return 1;
      }
      return 0;
    });
  }

  function fmtPct(n, digits) {
    if (n === null || n === undefined || Number.isNaN(n)) return null;
    return Number(n).toFixed(digits === undefined ? 1 : digits) + " %";
  }

  // Radek se vykresli jen kdyz ma hodnotu - u 19 stroju bez vlastniho
  // EUROMAP63 sberu (cyklus/doba cyklu) a bez aktivni zakazky/formy/
  // zmetkovitosti to karty prirozene zkrati, misto aby byly plne pomlcek.
  function mcRow(label, value, extraClass) {
    if (value === null || value === undefined || value === "") return "";
    return '<div class="mc-row"><span class="mc-label">' + escapeHtml(label) + '</span>' +
      '<span class="mc-value' + (extraClass ? " " + extraClass : "") + '">' + value + '</span></div>';
  }

  function worstCavityValue(m) {
    var w = m.worst_cavity_scrap;
    if (!w || w.reject_pct === null || w.reject_pct === undefined) return null;
    var overTarget = w.target_pct !== null && w.target_pct !== undefined && w.reject_pct > w.target_pct;
    var text = escapeHtml(fmtPct(w.reject_pct));
    if (w.target_pct !== null && w.target_pct !== undefined) {
      text += ' <span class="muted">(cíl ' + escapeHtml(fmtPct(w.target_pct)) + ')</span>';
    }
    return { text: text, danger: overTarget };
  }

  // Realny (CYCLEMOYEN) a planovany (CYCLETHEO) cyklus z Cyclades
  // Resultat_equipe (viz api/main.py _cycle_times_from_row) - modre kdyz
  // je realny cyklus o vic nez 5 % rychlejsi nez planovany, zelene kdyz
  // je na cili (rychlejsi/stejny, ale ne o vic nez 5 %), cervene kdyz je
  // pomalejsi nez plan.
  function ctValue(m) {
    var real = m.cycle_time_real_s;
    var planned = m.cycle_time_planned_s;
    if (real === null || real === undefined) return null;
    var text = escapeHtml(fmt(real, 1)) + ' s';
    if (planned !== null && planned !== undefined) {
      text += ' <span class="muted">(cíl ' + escapeHtml(fmt(planned, 1)) + ' s)</span>';
    }
    var cls = null;
    if (planned !== null && planned !== undefined && planned > 0) {
      var betterPct = (planned - real) / planned * 100;
      cls = betterPct > 5 ? "mc-value--better" : (real <= planned ? "mc-value--ontarget" : "mc-value--danger");
    }
    return { text: text, cls: cls };
  }

  function renderGrid(machines) {
    gridEl.innerHTML = "";
    if (!machines.length) {
      gridEl.innerHTML = '<p class="muted">Žádné aktivní stroje.</p>';
      return;
    }
    machines.forEach(function (m) {
      var a = document.createElement("a");
      a.href = "machine.html?code=" + encodeURIComponent(m.machine_code);
      a.className = "machine-card state--" + (m.state || "neznamo");
      a.id = cardId(m.machine_code);

      var cavity = worstCavityValue(m);
      var ct = ctValue(m);

      var rows =
        mcRow("WO", m.order_ref ? escapeHtml(m.order_ref) : null) +
        mcRow("Forma", m.tool_ref ? escapeHtml(formatTool(m)) : null) +
        mcRow("CT", ct ? ct.text : null, ct ? ct.cls : null) +
        mcRow("Posl. št.", m.last_label ? escapeHtml(m.last_label) : null) +
        mcRow("Další št.", m.next_label ? escapeHtml(m.next_label) : null) +
        mcRow("Zmetk.", cavity ? cavity.text : null, cavity && cavity.danger ? "mc-value--danger" : null) +
        mcRow("Prostoj", m.state === "stoji" ? escapeHtml(m.stop_reason || "neurčeno") + (m.stop_duration_s ? " (" + escapeHtml(fmtDuration(m.stop_duration_s)) + ")" : "") : null, "mc-value--reason");

      a.innerHTML =
        '<div class="machine-card__head">' +
          '<span class="machine-card__name">' + escapeHtml(m.cyclades_mac_refmac || m.machine_name || m.machine_code) + '</span>' +
          '<span class="machine-card__state">' + (STATE_LABELS[m.state] || m.state || "–") + '</span>' +
        '</div>' +
        '<div class="machine-card__body">' + rows + '</div>';
      gridEl.appendChild(a);
    });
  }

  function updateCardLive(machineCode, data) {
    var card = document.getElementById(cardId(machineCode));
    if (!card) return;
    var cycleEl = card.querySelector('[data-field="cycle_count"]');
    var timeEl = card.querySelector('[data-field="cycle_time_s"]');
    if (cycleEl) cycleEl.textContent = data.cycle_count;
    if (timeEl) timeEl.textContent = fmt(data.cycle_time_s) + " s";
  }

  function openSocket(machineCode) {
    if (sockets[machineCode]) return;
    var ws = new WebSocket(WS_BASE + "/ws/cycles?machine=" + encodeURIComponent(machineCode));
    ws.onmessage = function (evt) {
      try {
        var msg = JSON.parse(evt.data);
        if (msg.type === "cycle") {
          latestByMachine[machineCode] = msg.data;
          updateCardLive(machineCode, msg.data);
        }
      } catch (e) { /* ignore */ }
    };
    ws.onclose = function () {
      delete sockets[machineCode];
      setTimeout(function () { openSocket(machineCode); }, 3000);
    };
    ws.onerror = function () { ws.close(); };
    sockets[machineCode] = ws;
  }

  function pollStatus() {
    fetchJson("/api/machines/status")
      .then(function (machines) {
        setStatus("ok", "Online");
        machines = sortMachinesByNumber(machines);
        renderGrid(machines);
        machines.forEach(function (m) { openSocket(m.machine_code); });
      })
      .catch(function (err) {
        setStatus("err", "Chyba: " + err.message);
      });
  }

  pollStatus();
  setInterval(pollStatus, 5000);
})();
