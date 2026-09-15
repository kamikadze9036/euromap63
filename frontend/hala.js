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

  function fmt(n, digits) {
    if (n === null || n === undefined || Number.isNaN(n)) return "–";
    return Number(n).toFixed(digits === undefined ? 2 : digits);
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

      var latest = m.latest_cycle || {};
      a.innerHTML =
        '<div class="machine-card__head">' +
          '<span class="machine-card__name">' + (m.machine_name || m.machine_code) + '</span>' +
          '<span class="machine-card__state">' + (STATE_LABELS[m.state] || m.state || "–") + '</span>' +
        '</div>' +
        '<div class="machine-card__body">' +
          '<div class="machine-card__metric"><span class="mc-label">Cyklus</span><span class="mc-value" data-field="cycle_count">' + (latest.cycle_count || "–") + '</span></div>' +
          '<div class="machine-card__metric"><span class="mc-label">Doba cyklu</span><span class="mc-value" data-field="cycle_time_s">' + fmt(latest.cycle_time_s) + ' s</span></div>' +
          '<div class="machine-card__metric"><span class="mc-label">Zakázka</span><span class="mc-value">' + (m.order_ref || "–") + '</span></div>' +
          '<div class="machine-card__metric"><span class="mc-label">Forma</span><span class="mc-value">' + (m.tool_ref || "–") + '</span></div>' +
        '</div>';
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
