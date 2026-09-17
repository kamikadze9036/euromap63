(function () {
  "use strict";

  var cfg = window.EUROMAP63_CONFIG || {};
  var API_BASE = cfg.apiBaseUrl || "http://localhost:8091";

  var statusEl = document.getElementById("status");
  var form = document.getElementById("searchForm");
  var valueInput = document.getElementById("searchValue");
  var summaryEl = document.getElementById("resultSummary");
  var resultSection = document.getElementById("resultSection");
  var resultTbody = document.querySelector("#resultTable tbody");
  var detailSection = document.getElementById("detailSection");
  var detailTitle = document.getElementById("detailTitle");
  var detailTbody = document.querySelector("#detailTable tbody");

  var paramDefsByMachine = {};

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
      if (!res.ok) {
        return res.json().catch(function () { return {}; }).then(function (body) {
          throw new Error(body.detail || ("HTTP " + res.status));
        });
      }
      return res.json();
    });
  }

  function loadParamDefs(machine) {
    if (paramDefsByMachine[machine]) return Promise.resolve(paramDefsByMachine[machine]);
    return fetchJson("/api/parameters?machine=" + encodeURIComponent(machine))
      .then(function (rows) {
        var defs = {};
        rows.forEach(function (r) { defs[r.param_name] = { label: r.param_label, unit: r.param_unit }; });
        paramDefsByMachine[machine] = defs;
        return defs;
      })
      .catch(function () { return {}; });
  }

  function showDetail(cycle) {
    detailSection.hidden = false;
    detailTitle.textContent = "Parametry cyklu " + cycle.cycle_count + " (" + cycle.machine_code + ")";
    loadParamDefs(cycle.machine_code).then(function (defs) {
      detailTbody.innerHTML = "";
      var params = cycle.params || {};
      Object.keys(params).forEach(function (key) {
        var def = defs[key] || {};
        var tr = document.createElement("tr");
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
        detailTbody.appendChild(tr);
      });
      detailSection.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  }

  function renderResults(cycles) {
    resultTbody.innerHTML = "";
    cycles.forEach(function (c) {
      var tr = document.createElement("tr");
      tr.addEventListener("click", function () { showDetail(c); });

      var tdTime = document.createElement("td");
      tdTime.textContent = new Date(c.time).toLocaleString("cs-CZ");
      var tdMachine = document.createElement("td");
      tdMachine.textContent = c.machine_code;
      var tdCycle = document.createElement("td");
      tdCycle.className = "num";
      tdCycle.textContent = c.cycle_count;
      var tdTime2 = document.createElement("td");
      tdTime2.className = "num";
      tdTime2.textContent = fmt(c.cycle_time_s) + " s";

      tr.appendChild(tdTime);
      tr.appendChild(tdMachine);
      tr.appendChild(tdCycle);
      tr.appendChild(tdTime2);
      resultTbody.appendChild(tr);
    });
    resultSection.hidden = cycles.length === 0;
  }

  function showSummary(text, kind) {
    summaryEl.hidden = false;
    summaryEl.textContent = text;
    summaryEl.className = "result-summary result-summary--" + (kind || "ok");
  }

  function search(mode, value) {
    detailSection.hidden = true;
    resultSection.hidden = true;
    summaryEl.hidden = true;
    setStatus("waiting", "Hledám…");

    var req;
    if (mode === "order") {
      req = fetchJson("/api/cycles/by-order?order_ref=" + encodeURIComponent(value))
        .then(function (cycles) { return { order_ref: value, cycles: cycles }; });
    } else if (mode === "package") {
      req = fetchJson("/api/cycles/by-package?label=" + encodeURIComponent(value));
    } else {
      req = fetchJson("/api/cycles/by-label?label=" + encodeURIComponent(value));
    }

    req.then(function (result) {
      setStatus("ok", "Nalezeno");
      var cycles = result.cycles || [];
      var machines = Array.from(new Set(cycles.map(function (c) { return c.machine_code; })));
      var first = cycles[0], last = cycles[cycles.length - 1];
      var summary = "Zakázka " + result.order_ref + " — " + cycles.length + " cyklů";
      if (mode === "label") summary = "Štítek " + result.label + " → zakázka " + result.order_ref + " — " + cycles.length + " cyklů";
      if (mode === "package") {
        summary = "Balení (karton " + (result.carton_ref != null ? result.carton_ref : "?") + ", štítek " + result.label + ") → zakázka " + result.order_ref +
          " — nalezeno " + result.cycles_found + " cyklů, deklarováno " + fmt(result.declared_qty, 0) + " ks" +
          (result.operator_code ? ", operátor " + result.operator_code : "") +
          ", deklarace " + new Date(result.declared_at).toLocaleString("cs-CZ");
        if (result.cycles_found !== Math.round(result.declared_qty)) {
          summary += " ⚠ počet cyklů neodpovídá přesně deklarovanému množství (víc dutin/kavit na cyklus, nebo mezera bez předchozí deklarace)";
        }
      }
      if (mode !== "package" && machines.length) summary += ", stroj(e): " + machines.join(", ");
      if (mode !== "package" && first && last) {
        summary += ", " + new Date(first.time).toLocaleString("cs-CZ") + " – " + new Date(last.time).toLocaleString("cs-CZ");
      }
      showSummary(summary, "ok");
      renderResults(cycles);
    }).catch(function (err) {
      setStatus("err", "Chyba");
      showSummary(err.message || "Nic nenalezeno.", "err");
    });
  }

  form.addEventListener("submit", function (evt) {
    evt.preventDefault();
    var mode = form.querySelector('input[name="mode"]:checked').value;
    var value = valueInput.value.trim();
    if (!value) return;
    search(mode, value);
  });
})();
