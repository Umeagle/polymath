// -- State --
let ws = null;
let reconnectTimer = null;
const RECONNECT_DELAY = 3000;

// -- DOM refs --
const $ = (sel) => document.querySelector(sel);
const statusDot = $("#statusDot");
const statusText = $("#statusText");
const oppTableBody = $("#oppTableBody");
const matchTableBody = $("#matchTableBody");

// -- WebSocket --

function connectWS() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws`);

  ws.onopen = () => {
    statusDot.classList.add("connected");
    statusText.textContent = "Connected";
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  };

  ws.onclose = () => {
    statusDot.classList.remove("connected");
    statusText.textContent = "Reconnecting...";
    scheduleReconnect();
  };

  ws.onerror = () => {
    ws.close();
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.type === "scan_update") {
        renderOpportunities(data.opportunities || []);
        renderStats(data.stats || {});
      }
    } catch (e) {
      console.error("WS parse error:", e);
    }
  };
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectWS();
  }, RECONNECT_DELAY);
}

// -- Rendering --

function renderStats(stats) {
  $("#statKalshi").textContent = fmt(stats.kalshi_markets);
  $("#statPoly").textContent = fmt(stats.polymarket_markets);
  $("#statMatched").textContent = fmt(stats.matched_pairs);
  $("#statOpps").textContent = fmt(stats.active_opportunities);
  $("#statScans").textContent = fmt(stats.total_scans);
  $("#statLastScan").textContent = stats.last_scan
    ? new Date(stats.last_scan).toLocaleTimeString()
    : "--";
}

function fmtExpiry(iso) {
  if (!iso) return '<span class="dim">--</span>';
  const d = new Date(iso);
  const now = new Date();
  const diffMs = d - now;
  const diffH = Math.floor(diffMs / 3600000);
  const diffD = Math.floor(diffMs / 86400000);

  let label;
  if (diffH < 0) {
    label = "expired";
  } else if (diffH < 1) {
    label = `${Math.floor(diffMs / 60000)}m`;
  } else if (diffH < 24) {
    label = `${diffH}h`;
  } else {
    label = `${diffD}d`;
  }

  const color = diffH < 24 ? "yellow" : "";
  const dateStr = d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  return `<span class="${color}" title="${d.toLocaleString()}">${label} <span class="dim">(${dateStr})</span></span>`;
}

function renderOpportunities(opps) {
  $("#oppCount").textContent = `${opps.length} found`;

  if (!opps.length) {
    oppTableBody.innerHTML = `
      <tr><td colspan="10">
        <div class="empty-state">
          <div class="icon">&#x1F50D;</div>
          <p>No arbitrage opportunities detected yet. Scanning...</p>
        </div>
      </td></tr>`;
    return;
  }

  oppTableBody.innerHTML = opps.map((o) => `
    <tr>
      <td>
        <div style="max-width:260px;overflow:hidden;text-overflow:ellipsis" title="${esc(o.kalshi_title)}">
          <a href="${esc(o.kalshi_url)}" target="_blank">${esc(trunc(o.kalshi_title, 40))}</a>
        </div>
        <div class="dim" style="font-size:0.75rem;margin-top:2px" title="${esc(o.polymarket_title)}">
          <a href="${esc(o.polymarket_url)}" target="_blank">${esc(trunc(o.polymarket_title, 40))}</a>
        </div>
      </td>
      <td class="mono" style="font-size:0.78rem">${fmtExpiry(o.expiry)}</td>
      <td><span class="direction-tag">${esc(o.direction)}</span></td>
      <td class="mono">${o.kalshi_price.toFixed(2)}&cent;</td>
      <td class="mono">${o.polymarket_price.toFixed(2)}&cent;</td>
      <td class="mono">${o.cost.toFixed(4)}</td>
      <td><span class="profit-pill positive">+${(o.profit * 100).toFixed(1)}&cent;</span></td>
      <td><span class="roi-pill">${o.roi.toFixed(1)}%</span></td>
      <td class="mono dim">${o.max_size > 0 ? "$" + o.max_size.toFixed(0) : "--"}</td>
      <td class="mono dim">${o.similarity}%</td>
    </tr>
  `).join("");
}

function renderMatchedMarkets(matches) {
  $("#matchCount").textContent = `${matches.length} pairs`;

  if (!matches.length) {
    matchTableBody.innerHTML = `
      <tr><td colspan="6">
        <div class="empty-state"><p>No matched markets yet</p></div>
      </td></tr>`;
    return;
  }

  matchTableBody.innerHTML = matches.map((m) => `
    <tr>
      <td style="max-width:280px;overflow:hidden;text-overflow:ellipsis">
        <a href="${esc(m.kalshi_url)}" target="_blank" title="${esc(m.kalshi_title)}">${esc(trunc(m.kalshi_title, 50))}</a>
        <div class="dim" style="font-size:0.72rem">${esc(m.kalshi_ticker)}</div>
      </td>
      <td style="max-width:280px;overflow:hidden;text-overflow:ellipsis">
        <a href="${esc(m.polymarket_url)}" target="_blank" title="${esc(m.polymarket_title)}">${esc(trunc(m.polymarket_title, 50))}</a>
      </td>
      <td class="mono" style="font-size:0.78rem">${fmtExpiry(m.expiry)}</td>
      <td class="mono">${m.similarity.toFixed(0)}%</td>
      <td class="mono">${m.kalshi_yes.toFixed(2)} / ${m.kalshi_no.toFixed(2)}</td>
      <td class="mono">${m.poly_yes.toFixed(2)} / ${m.poly_no.toFixed(2)}</td>
    </tr>
  `).join("");
}

// -- Tabs --

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
    tab.classList.add("active");
    const target = tab.dataset.tab;
    $(`#tab-${target}`).classList.add("active");

    if (target === "matched") {
      fetchMatchedMarkets();
    }
  });
});

// -- Settings --

$("#setAutoExecute").addEventListener("change", (e) => {
  $("#autoExecLabel").textContent = e.target.checked ? "Enabled" : "Disabled";
});

$("#saveSettings").addEventListener("click", async () => {
  const body = {
    scan_interval: parseInt($("#setScanInterval").value) || 5,
    min_profit_cents: parseFloat($("#setMinProfit").value) || 2,
    match_threshold: parseInt($("#setMatchThreshold").value) || 80,
    auto_execute: $("#setAutoExecute").checked,
    max_position_usd: parseFloat($("#setMaxPosition").value) || 100,
  };

  try {
    const resp = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (resp.ok) {
      $("#saveSettings").textContent = "Saved!";
      setTimeout(() => ($("#saveSettings").textContent = "Save Settings"), 1500);
    }
  } catch (e) {
    console.error("Failed to save settings:", e);
  }
});

// -- Data Fetching --

async function fetchMatchedMarkets() {
  try {
    const resp = await fetch("/api/matched-markets");
    const data = await resp.json();
    renderMatchedMarkets(data);
  } catch (e) {
    console.error("Failed to fetch matched markets:", e);
  }
}

async function fetchSettings() {
  try {
    const resp = await fetch("/api/stats");
    const data = await resp.json();
    $("#setScanInterval").value = data.scan_interval || 5;
    $("#setAutoExecute").checked = data.auto_execute || false;
    $("#autoExecLabel").textContent = data.auto_execute ? "Enabled" : "Disabled";
  } catch (e) {
    console.error("Failed to fetch settings:", e);
  }
}

// -- Helpers --

function fmt(n) {
  return n != null ? n.toLocaleString() : "--";
}

function esc(str) {
  if (!str) return "";
  const d = document.createElement("div");
  d.textContent = str;
  return d.innerHTML;
}

function trunc(str, len) {
  if (!str) return "";
  return str.length > len ? str.slice(0, len) + "..." : str;
}

// -- Init --

fetchSettings();
connectWS();
