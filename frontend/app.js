/**
 * ApexTrader AI — Production MT5 Trading Terminal Engine
 * Pure Live MT5 Python API Connection Workflow, Show/Hide Password Eye Toggle,
 * Live Bidirectional WebSocket Synchronization & Real Account Info Rendering
 */

"use strict";

// ─────────────────────────────────────────────────────────────
//  CONFIGURATION & GLOBAL STATE
// ─────────────────────────────────────────────────────────────
const API_BASE = "http://localhost:8000";
const WS_BASE  = "ws://localhost:8000";
const API_KEY  = "apex_secret_api_key_2026";

const TIMEFRAMES = {
  M1:  { label: "1m", title: "1-minute", minutes: 1 },
  M5:  { label: "5m", title: "5-minute", minutes: 5 },
  M15: { label: "15m", title: "15-minute", minutes: 15 },
  M30: { label: "30m", title: "30-minute", minutes: 30 },
  H1:  { label: "1h", title: "1-hour", minutes: 60 },
  H4:  { label: "4h", title: "4-hour", minutes: 240 },
  D1:  { label: "1d", title: "Daily", minutes: 1440 },
  W1:  { label: "1w", title: "Weekly", minutes: 10080 },
  MN1: { label: "1M", title: "Monthly", minutes: 43200 }
};

const state = {
  symbol:        "XAUUSD",
  timeframe:     "M15",
  botStatus:     "STOPPED",
  connectionState: "Disconnected",
  wsSocket:      null,
  wsConnected:   false,
  candles:       [],
  positions:     [],
  pendingOrders: [],
  history:       [],
  historyPage:   1,
  historyPageSize: 30,
  account:       null,
  riskSettings:  null,
  auditLog:      [],
  verifiedPreview: null,
  lastSequenceNumber: 0,
  lastPayloadTimestamp: "",
  
  // Drawing & Chart Controls
  activeTool:    "crosshairs",
  drawings:      [],
  drawingInProgress: null,
  
  // Indicator Toggles
  indicators: {
    showEMA:       true,
    showVWAP:      true,
    showBollinger: true,
    showOB:        true,
    showRSI:       true,
    showMACD:      true,
  },

  // Auto-Analysis Pipeline State
  isAnalysisRunning: false,
  lastAnalysisTimestamp: null,
  nextAnalysisCountdown: 3,
  autoAnalysisInterval: null,
  
  // Interactive Viewport State
  zoomCount: 60,
  panOffset: 0,
  mousePos:  null,
  isPanning: false,
  dragStart: null
};

// ─────────────────────────────────────────────────────────────
//  UTILITY HELPERS
// ─────────────────────────────────────────────────────────────
const el  = (id) => document.getElementById(id);
const fmt = (n, d=2) => {
  if (n === undefined || n === null || isNaN(n)) return "0.00";
  const parts = Number(n).toFixed(d).split(".");
  parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return parts.join(".");
};

function getDigits(symbol) {
  if (!symbol) return 5;
  const s = symbol.toUpperCase();

  // Indices
  const INDEX_SYMBOLS = ["US30", "US500", "USTEC", "US2000", "UK100", "GER40", "JP225", "GER30", "DE30", "NAS100", "SPX"];
  if (INDEX_SYMBOLS.some(idx => s.includes(idx))) {
    return 2;
  }

  // Crypto
  if (s.includes("BTC") || s.includes("ETH")) {
    return 2;
  }

  // Metals
  if (s.includes("XAU")) {
    return 2;
  }
  if (s.includes("XAG")) {
    return 3;
  }

  // JPY forex pairs
  if (s.includes("JPY")) {
    return 3;
  }

  // Standard forex pairs
  return 5;
}

function addAuditEntry(msg, color = "") {
  const now   = new Date().toLocaleTimeString("en-GB");
  const entry = { time: now, msg, color };
  state.auditLog.unshift(entry);
  if (state.auditLog.length > 300) state.auditLog.pop();
  renderAuditLog();
}

function updateConnectionIndicator(statusStr) {
  state.connectionState = statusStr;
  const dot = el("status-dot");
  const txt = el("status-mode");
  if (!dot || !txt) return;

  txt.textContent = statusStr;

  if (statusStr === "Connected") {
    dot.className = "pulse-dot green";
    txt.style.color = "var(--accent-emerald)";
  } else if (statusStr.includes("Connecting") || statusStr.includes("Validating") || statusStr.includes("Fetching") || statusStr.includes("Reconnecting")) {
    dot.className = "pulse-dot amber";
    txt.style.color = "var(--accent-amber)";
  } else {
    dot.className = "pulse-dot red";
    txt.style.color = "var(--accent-red)";
  }
}

function escHtml(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

// ─────────────────────────────────────────────────────────────
//  REST API HELPERS
// ─────────────────────────────────────────────────────────────
async function apiFetch(path, method = "GET", body = null) {
  try {
    const opts = {
      method,
      headers: { 
        "Content-Type": "application/json",
        "X-API-Key": API_KEY
      }
    };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(API_BASE + path, opts);
    if (!res.ok) {
      const errText = await res.text();
      throw new Error(`HTTP ${res.status} (${errText})`);
    }
    return await res.json();
  } catch (e) {
    addAuditEntry(`[API ERROR] ${path} → ${e.message}`, "var(--accent-red)");
    return null;
  }
}

async function setTimeframe(tf) {
  const target = (tf && TIMEFRAMES[tf.toUpperCase()]) ? tf.toUpperCase() : "M15";
  state.timeframe = target;
  
  // 1. Synchronize UI timeframe button active classes
  document.querySelectorAll(".btn-tf").forEach(btn => {
    if (btn.dataset.tf === target) {
      btn.classList.add("active");
    } else {
      btn.classList.remove("active");
    }
  });

  // 2. Synchronize chart title timeframe label
  const tfTitleEl = el("chart-tf-title");
  if (tfTitleEl) {
    tfTitleEl.textContent = target;
  }
  const symbolTitleEl = el("chart-symbol-title");
  if (symbolTitleEl) {
    symbolTitleEl.innerHTML = `${state.symbol} <span id="chart-tf-title">${target}</span>`;
  }

  // 3. Render chart background immediately
  renderFinancialChart();

  // 4. Reconnect WebSocket engine for target timeframe
  resetSLTPInputs();
  connectWebSocket();

  // 5. Fetch historical candles for target timeframe via REST API immediately
  const candles = await apiFetch(`/api/v1/market/candles?symbol=${state.symbol}&timeframe=${target}&count=100`);
  if (candles && candles.length > 0) {
    state.candles = candles;
    renderFinancialChart();
    addAuditEntry(`[TIMEFRAME] Synchronized chart timeframe to ${target} (${TIMEFRAMES[target].title}) — ${candles.length} candles rendered`, "var(--accent-cyan)");
  }

  // 6. Sync the bot's entry timeframe with the chart, except in Donchian Trend
  //    mode where entries always use H1 (the chart is only for viewing).
  if (state.riskSettings && state.riskSettings.strategy_profile === "DONCHIAN_TREND") {
    if (el("auto-trade-live-tf-badge")) el("auto-trade-live-tf-badge").textContent = `RUNNING (${state.riskSettings.donchian_timeframe || "H1"})`;
    return;
  }
  if (state.riskSettings) {
    state.riskSettings.entry_timeframe = target;
  }
  if (el("auto-trade-live-tf-badge")) {
    el("auto-trade-live-tf-badge").textContent = `RUNNING (${target})`;
  }

  await apiFetch("/api/v1/settings/risk", "POST", {
    ...(state.riskSettings || {}),
    entry_timeframe: target
  });
}

// Clears manual SL/TP inputs (global: also used by the timeframe switcher).
function resetSLTPInputs() {
  for (const id of ["manual-sl-price", "manual-tp-price"]) {
    const f = el(id);
    if (f) {
      f.value = "";
      f.dataset.autoPopulated = "true";
    }
  }
}

// ─────────────────────────────────────────────────────────────
//  WEBSOCKET ENGINE
// ─────────────────────────────────────────────────────────────
function connectWebSocket() {
  if (state.wsSocket) {
    state.wsSocket.onclose = null;
    state.wsSocket.close();
    state.wsSocket = null;
  }

  const url = `${WS_BASE}/ws/market?symbol=${state.symbol}&timeframe=${state.timeframe}&api_key=${API_KEY}`;
  addAuditEntry(`[WS] Connecting real-time stream (${state.symbol} ${state.timeframe})...`);

  const ws = new WebSocket(url);
  state.wsSocket = ws;

  ws.onopen = () => {
    state.wsConnected = true;
    addAuditEntry("[WS] Real-time market stream connected.", "var(--accent-emerald)");
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      handleWSPayload(data);
    } catch (e) {
      addAuditEntry("[WS] Payload parse error: " + e.message, "var(--accent-red)");
    }
  };

  ws.onerror = () => {
    state.wsConnected = false;
    updateConnectionIndicator("Connection Lost");
  };

  ws.onclose = () => {
    state.wsConnected = false;
    updateConnectionIndicator("Reconnecting...");
    addAuditEntry("[WS] Stream closed. Reconnecting in 3s...", "var(--accent-amber)");
    setTimeout(connectWebSocket, 3000);
  };
}

// Merge incoming candles into the chart instead of replacing the list.
// Sources send different counts (WebSocket 50, sync 80, timeframe switch 100) and
// replacing made the visible candle count, and so the candle width, jump every few
// seconds. Lists for a different timeframe than the chart are ignored.
function mergeChartCandles(incoming) {
  if (!incoming || !incoming.length) return false;
  const tf = (state.timeframe || "").toUpperCase();
  const inTf = (incoming[incoming.length - 1].timeframe || "").toUpperCase();
  if (tf && inTf && inTf !== tf && !(tf === "1M" && inTf.startsWith("MN"))) return false;
  const byTs = new Map((state.candles || []).map(c => [c.timestamp, c]));
  incoming.forEach(c => byTs.set(c.timestamp, c));
  state.candles = [...byTs.values()]
    .sort((a, b) => (a.timestamp < b.timestamp ? -1 : a.timestamp > b.timestamp ? 1 : 0))
    .slice(-300);
  return true;
}

function handleWSPayload(data) {
  if (data.sequence !== undefined) {
    if (data.sequence <= state.lastSequenceNumber) {
      console.log(`[UI_SYNC] Dropped stale/out-of-order message sequence ${data.sequence} (current max: ${state.lastSequenceNumber})`);
      return;
    }
    state.lastSequenceNumber = data.sequence;
  }

  let liveReason = data.auto_trade_live_reason;
  if (!liveReason && data.decision_report && data.decision_report.decision === "HOLD") {
    const rej = data.decision_report.rejection_reasons;
    if (rej && rej.length > 0 && rej[0] !== "NONE") {
      liveReason = `Trade Held: Strategy decision is HOLD. Rejection reasons: ${rej.join(", ")}`;
    }
  }

  updateBotStatusUI(data.bot_status, data.engine_state, data.engine_reason, liveReason);
  if (data.ticker) updateTicker(data.ticker);
  if (data.account) updateAccount(data.account);
  if (data.candles && data.candles.length > 0 && mergeChartCandles(data.candles)) {
    renderFinancialChart();
  }
  if (data.positions !== undefined) {
    state.positions = data.positions; // Authoritative direct replacement from MT5
    renderPositionsTable();
  }
  if (data.pending_orders !== undefined) {
    state.pendingOrders = data.pending_orders;
    renderPendingOrdersTable();
  }
  if (data.history !== undefined) {
    state.history = data.history;
    state.historyTotal = data.history_total !== undefined ? data.history_total : data.history.length;
    renderTradeHistoryTable();
  }
  if (data.decision_report) updateDecisionReport(data.decision_report);
  if (data.risk_settings) updateRiskSettingsUI(data.risk_settings, false);
  if (data.auto_trade_hold_event) {
    const ev = data.auto_trade_hold_event;
    if (ev.event_id && ev.event_id !== lastAutoTradeEventId) {
      lastAutoTradeEventId = ev.event_id;
      showAutoTradeHoldPopup(ev.signal, ev.reason, 4000);
    }
  }
}

// ─────────────────────────────────────────────────────────────
//  UI UPDATERS
// ─────────────────────────────────────────────────────────────
let lastRecordedAutoTradeReason = "";

function updateBotStatusUI(status, engineState, engineReason, autoTradeLiveReason = null) {
  state.botStatus = status;
  const badge = el("bot-status-badge");
  if (!badge) return;

  let displayStatus = engineState || status;
  badge.textContent = displayStatus;

  const baseState = engineState || status;
  const isAutoRunning = (status === "RUNNING" || ["RUNNING", "ANALYZING", "SIGNAL_READY", "EXECUTING", "POSITION_OPEN", "MANAGING_POSITION", "EXITING"].includes(baseState));

  badge.className = "badge-bot-status " + (
    baseState === "RUNNING" ? "status-running" :
    baseState === "PAUSED" ? "status-paused" :
    baseState === "EMERGENCY_HALT" ? "status-emergency" :
    baseState === "ERROR" ? "status-emergency" :
    ["ANALYZING", "SIGNAL_READY", "EXECUTING", "POSITION_OPEN", "MANAGING_POSITION", "EXITING"].includes(baseState) ? "status-running" : "status-stopped"
  );

  const liveBanner = el("auto-trade-live-status-banner");
  const liveReasonEl = el("auto-trade-live-reason");
  const liveBadge = el("auto-trade-live-tf-badge");
  const sidebarContainer = el("sidebar-auto-trade-reason-container");
  const sidebarReasonEl = el("sidebar-auto-trade-reason");

  const activeReason = autoTradeLiveReason || engineReason || "Scanning live market structure and indicators...";
  const tfStr = state.timeframe || "M15";

  if (isAutoRunning) {
    if (liveBanner && liveReasonEl) {
      liveReasonEl.textContent = activeReason;
      if (liveBadge) liveBadge.textContent = `RUNNING (${tfStr})`;
      liveBanner.classList.remove("hidden");

      // Dynamic Severity Accent Colors & Icon Styling
      const iconContainer = liveBanner.querySelector(".trade-alert-icon");
      if (activeReason.includes("Trade Blocked") || activeReason.includes("AI Execution Blocked") || activeReason.includes("Circuit Breaker") || activeReason.includes("High Spread") || activeReason.includes("Risk Guard")) {
        liveBanner.style.borderLeftColor = "#ef4444";
        liveBanner.style.boxShadow = "0 0 15px rgba(239, 68, 68, 0.2)";
        if (iconContainer) iconContainer.style.color = "#ef4444";
      } else if (activeReason.includes("Executing Trade") || activeReason.includes("Position Active")) {
        liveBanner.style.borderLeftColor = "#10b981";
        liveBanner.style.boxShadow = "0 0 15px rgba(16, 185, 129, 0.2)";
        if (iconContainer) iconContainer.style.color = "#10b981";
      } else {
        liveBanner.style.borderLeftColor = "#f59e0b";
        liveBanner.style.boxShadow = "0 0 15px rgba(245, 158, 11, 0.15)";
        if (iconContainer) iconContainer.style.color = "#f59e0b";
      }
    }
    if (sidebarContainer && sidebarReasonEl) {
      sidebarReasonEl.textContent = activeReason;
      sidebarContainer.classList.remove("hidden");
    }

    if (activeReason && activeReason !== lastRecordedAutoTradeReason) {
      lastRecordedAutoTradeReason = activeReason;
      const logColor = (activeReason.includes("Trade Blocked") || activeReason.includes("AI Execution Blocked")) ? "var(--accent-red)" : (activeReason.includes("Executing") ? "var(--accent-emerald)" : "var(--accent-amber)");
      addAuditEntry(`[AUTO-TRADE RUNTIME STATUS] (${tfStr}) → ${activeReason}`, logColor);
    }
  } else {
    if (liveBanner) liveBanner.classList.add("hidden");
    if (sidebarContainer) sidebarContainer.classList.add("hidden");
  }
}

function updateTicker(t) {
  const digits = t.digits || getDigits(t.symbol);
  el("price-bid").textContent    = fmt(t.bid, digits);
  el("price-ask").textContent    = fmt(t.ask, digits);
  const ptsStr = t.spread_points ? ` (${t.spread_points} pts)` : "";
  el("price-spread").textContent = fmt(t.spread_pips, 1) + " Pips" + ptsStr;
  el("chart-symbol-title").innerHTML = `${t.symbol} <span id="chart-tf-title">${state.timeframe}</span>`;
  el("smc-vwap").textContent = fmt(t.bid, digits);
}

function updateAccount(acc) {
  state.account = acc;
  const curr = acc.currency || "USD";
  el("acc-balance").textContent    = `${fmt(acc.balance)} ${curr}`;
  el("acc-equity").textContent     = `${fmt(acc.equity)} ${curr}`;
  el("acc-free-margin").textContent= `${fmt(acc.free_margin)} ${curr}`;
  el("acc-margin-level").textContent = fmt(acc.margin_level_pct, 1) + "%";
  el("acc-open-count").textContent = `${acc.open_positions_count} / ${acc.pending_orders_count}`;

  const dailyPnl = acc.daily_pnl_usd;
  const dailyPct = acc.balance > 0 ? (dailyPnl / acc.balance * 100) : 0;
  const dailyEl  = el("acc-daily-pnl");
  dailyEl.textContent = (dailyPnl >= 0 ? "+" : "") + `${fmt(Math.abs(dailyPnl))} ${curr} (` + fmt(dailyPct, 1) + "%)";
  dailyEl.style.color = dailyPnl >= 0 ? "var(--accent-emerald)" : "var(--accent-red)";

  const weeklyEl = el("acc-weekly-pnl");
  weeklyEl.textContent = (acc.weekly_pnl_usd >= 0 ? "+" : "") + `${fmt(Math.abs(acc.weekly_pnl_usd))} ${curr}`;
  weeklyEl.style.color = acc.weekly_pnl_usd >= 0 ? "var(--accent-emerald)" : "var(--accent-red)";

  updateConnectionIndicator(acc.detailed_status || (acc.is_connected ? "Connected" : "Disconnected"));
}

function updateDecisionReport(report) {
  if (!report) return;

  // Render Explainable AI Reasoning Banner
  if (el("ai-summary-text")) {
    el("ai-summary-text").textContent = report.summary_explanation || "Analyzing live market structure and indicators...";
  }
  if (el("ai-explanation-summary")) {
    el("ai-explanation-summary").textContent = report.summary_explanation || "Analyzing live market structure and indicators...";
  }

  const decBadge = el("ai-decision-badge");
  if (decBadge) {
    const d = report.decision || "HOLD";
    const primaryReason = (report.rejection_reasons && report.rejection_reasons.length > 0) ? report.rejection_reasons[0] : "";
    
    if (d === "HOLD" && primaryReason && primaryReason !== "NONE") {
      decBadge.textContent = `HOLD: ${primaryReason}`;
    } else {
      decBadge.textContent = d;
    }
    
    decBadge.className = d === "BUY" ? "badge-decision-buy" : (d === "SELL" ? "badge-decision-sell" : "badge-decision-hold");
  }

  const chipsContainer = el("ai-outweighing-chips");
  if (chipsContainer && report.outweighing_factors) {
    if (report.outweighing_factors.length > 0) {
      chipsContainer.innerHTML = report.outweighing_factors.map(f => `<span class="feature-tag"><span>${escHtml(f)}</span></span>`).join("");
      chipsContainer.classList.remove("hidden");
    } else {
      chipsContainer.innerHTML = "";
    }
  }

  if (report.suggested_sl && report.suggested_sl > 0) {
    const slEl = el("manual-sl-price");
    if (slEl && (!slEl.value || slEl.dataset.autoPopulated === "true")) {
      slEl.value = report.suggested_sl.toFixed(5);
      slEl.dataset.autoPopulated = "true";
    }
  }
  if (report.suggested_tp && report.suggested_tp > 0) {
    const tpEl = el("manual-tp-price");
    if (tpEl && (!tpEl.value || tpEl.dataset.autoPopulated === "true")) {
      tpEl.value = report.suggested_tp.toFixed(5);
      tpEl.dataset.autoPopulated = "true";
    }
  }

  // Legacy score boxes only exist when the old layout is present.
  if (report.breakdown && el("pil-trend")) {
    const bd = report.breakdown;
    
    // Choose active direction to display in the 5-pillar matrix
    const isSell = report.decision === "SELL" || (report.decision === "HOLD" && (bd.short_score ?? 0) > (bd.long_score ?? 0));
    
    const trendVal = isSell ? (bd.trend_score_short ?? bd.trend_score) : (bd.trend_score_long ?? bd.trend_score);
    const structVal = isSell ? (bd.market_structure_score_short ?? bd.market_structure_score) : (bd.market_structure_score_long ?? bd.market_structure_score);
    const momVal = isSell ? (bd.momentum_volume_score_short ?? bd.momentum_volume_score) : (bd.momentum_volume_score_long ?? bd.momentum_volume_score);
    const heuristicVal = isSell ? (bd.heuristic_score_short ?? bd.heuristic_score) : (bd.heuristic_score_long ?? bd.heuristic_score);
    
    const activeScore = isSell ? (bd.short_score ?? bd.total_score) : (bd.long_score ?? bd.total_score);

    el("ai-confidence-badge").textContent = fmt(activeScore * 100, 1) + "% SCORE";

    // Progress bars for BUY/SELL score
    const buyPct  = fmt((bd.heuristic_score_long !== undefined ? bd.heuristic_score_long : bd.heuristic_score) * 100, 1);
    const sellPct = fmt((bd.heuristic_score_short !== undefined ? bd.heuristic_score_short : (1 - bd.heuristic_score)) * 100, 1);
    el("prob-buy-fill").style.width  = buyPct  + "%";
    el("prob-sell-fill").style.width = sellPct + "%";
    el("prob-buy-pct").textContent   = buyPct  + "%";
    el("prob-sell-pct").textContent  = sellPct + "%";

    const toW = (v) => fmt(Math.min(1, Math.max(0, v)) * 100, 0) + "%";
    el("pil-trend").style.width  = toW(trendVal);
    el("pil-struct").style.width = toW(structVal);
    el("pil-mom").style.width    = toW(momVal);
    el("pil-ai").style.width     = toW(heuristicVal);
    el("pil-risk").style.width   = toW(bd.risk_news_score);

    el("val-trend").textContent  = fmt(trendVal * 100, 0) + "%";
    el("val-struct").textContent = fmt(structVal * 100, 0) + "%";
    el("val-mom").textContent    = fmt(momVal * 100, 0) + "%";
    el("val-ai").textContent     = fmt(heuristicVal * 100, 0) + "%";
    el("val-risk").textContent   = fmt(bd.risk_news_score * 100, 0) + "%";

    const scorePct = fmt(activeScore * 100, 1);
    const badge    = el("matrix-status-badge");
    badge.textContent = `SCORE: ${scorePct}% (${bd.passed ? "PASSED" : "FAILED"})`;
    badge.className   = bd.passed ? "badge-status-pass" : "badge-status-fail";
  }

  if (report.condition_matrix && report.condition_matrix.length > 0) {
    const matrixContainer = el("institutional-condition-matrix");
    const countBadge = el("matrix-conditions-count");
    if (matrixContainer) {
      const passedCount = report.condition_matrix.filter(c => c.passed).length;
      const totalCount = report.condition_matrix.length;
      if (countBadge) {
        countBadge.textContent = `${passedCount} / ${totalCount} PASSED`;
        countBadge.className = (passedCount === totalCount) ? "badge-status-pass" : "badge-status-fail";
      }

      matrixContainer.innerHTML = report.condition_matrix.map(c => `
        <div class="condition-item ${c.passed ? 'cond-passed' : 'cond-failed'}">
          <div class="cond-icon">
            <i class="fa-solid ${c.passed ? 'fa-circle-check' : 'fa-circle-xmark'}"></i>
          </div>
          <div class="cond-info">
            <div class="cond-header-row">
              <span class="cond-title">${escHtml(c.name)}</span>
              <span class="${c.passed ? 'badge-cond-pass' : 'badge-cond-fail'}">${c.passed ? 'PASSED' : 'FAILED'}</span>
            </div>
            <div class="cond-details">${escHtml(c.details)}</div>
          </div>
        </div>
      `).join("");
    }
  }
}

function updateRiskSettingsUI(cfg, forceModalUpdate = true) {
  if (!cfg) return;
  state.riskSettings = cfg;

  const modalVisible = el("modal-settings") && !el("modal-settings").classList.contains("hidden");
  if (el("cfg-max-risk") && (forceModalUpdate || !modalVisible)) {
    const setVal = (id, v) => { if (el(id)) el(id).value = v; };
    setVal("cfg-strategy-profile", cfg.strategy_profile === "DONCHIAN_TREND" ? "DONCHIAN_TREND" : "HYBRID_AUTO");
    setVal("cfg-donchian-tf", (cfg.donchian_timeframe || "H1").toUpperCase() === "M15" ? "M15" : "H1");
    setVal("cfg-news-filter", String(cfg.news_filter_enabled !== false));
    setVal("cfg-lot-mode", cfg.lot_sizing_mode ?? "DYNAMIC_RISK");
    setVal("cfg-max-risk", cfg.max_risk_per_trade_pct ?? 1.0);
    setVal("cfg-user-lot", cfg.user_configured_lot ?? 0.01);
    setVal("cfg-max-allowed-lot", cfg.max_allowed_lot ?? 0.05);
    setVal("cfg-daily-loss", cfg.daily_loss_limit_pct ?? 3.0);
    setVal("cfg-max-daily-loss-amt", cfg.max_daily_loss_amount ?? 0);
    setVal("cfg-weekly-loss", cfg.weekly_loss_limit_pct ?? 7.0);
    setVal("cfg-max-consecutive-losses", cfg.max_consecutive_losses ?? 0);
    setVal("cfg-max-positions", cfg.max_open_positions ?? 2);
    setVal("cfg-max-per-direction", cfg.max_positions_per_direction ?? 1);
    setVal("cfg-min-entry-gap", cfg.min_minutes_between_entries ?? 30);
    setVal("cfg-max-daily-trades", cfg.max_trades_per_day ?? 8);
    setVal("cfg-max-spread", cfg.max_spread_pips ?? 3.0);
    setVal("cfg-blocked-hours", (cfg.blocked_hours || []).join(","));
    setVal("cfg-auto-trade-enabled", String(cfg.auto_trade_enabled === true));
    setVal("cfg-enable-live-trading", String(cfg.enable_live_trading === true));
  }

  // Update Live Risk Monitor Sidebar Card Badges
  if (el("risk-strategy-badge")) {
    const isDon = cfg.strategy_profile === "DONCHIAN_TREND";
    el("risk-strategy-badge").textContent = isDon ? `DONCHIAN TREND (${cfg.donchian_timeframe || "H1"})` : "LEGACY MULTI-SETUP";
    el("risk-strategy-badge").className = isDon ? "text-emerald" : "text-amber";
  }
  if (el("risk-consec-badge")) {
    const n = cfg.max_consecutive_losses ?? 0;
    el("risk-consec-badge").textContent = n > 0 ? `After ${n} losses (24h)` : "Off";
    el("risk-consec-badge").className = n > 0 ? "text-amber" : "text-muted";
  }
  if (el("risk-stacking-badge")) {
    const per = cfg.max_positions_per_direction ?? 0;
    const gap = cfg.min_minutes_between_entries ?? 0;
    el("risk-stacking-badge").textContent = per > 0 ? `${per} per direction, ${gap} min gap` : `Off, ${gap} min gap`;
    el("risk-stacking-badge").className = per > 0 ? "text-emerald" : "text-amber";
  }
  if (el("risk-hours-badge")) {
    const hrs = cfg.blocked_hours || [];
    el("risk-hours-badge").textContent = hrs.length ? hrs.map(h => String(h).padStart(2, "0") + ":00").join(", ") : "None";
  }
  if (el("risk-lot-mode-badge")) el("risk-lot-mode-badge").textContent = (cfg.lot_sizing_mode ?? "FIXED").toUpperCase();
  if (el("risk-target-vol-badge")) {
    const isFixed = (cfg.lot_sizing_mode ?? "FIXED") === "FIXED";
    el("risk-target-vol-badge").textContent = isFixed ? `${fmt(cfg.user_configured_lot ?? 0.10, 2)} Lots` : "DYNAMIC RISK";
    el("risk-target-vol-badge").className = isFixed ? "text-emerald" : "text-cyan";
  }
  if (el("risk-max-lot-badge")) el("risk-max-lot-badge").textContent = `${fmt(cfg.max_allowed_lot ?? 1.00, 2)} Lots`;
  if (el("risk-pct-badge")) el("risk-pct-badge").textContent = `${cfg.max_risk_per_trade_pct ?? 1.0}% Equity`;
  if (el("risk-daily-limit-badge")) {
    const amt = cfg.max_daily_loss_amount ?? 0;
    el("risk-daily-limit-badge").textContent = `${cfg.daily_loss_limit_pct ?? 3.0}%` + (amt > 0 ? ` / $${amt}` : "");
  }
  if (el("risk-weekly-limit-badge")) el("risk-weekly-limit-badge").textContent = `${cfg.weekly_loss_limit_pct ?? 7.0}% Balance`;
  if (el("risk-live-trading-badge")) {
    const liveActive = cfg.enable_live_trading === true;
    el("risk-live-trading-badge").textContent = liveActive ? "LIVE ENABLED" : "DEMO ONLY";
    el("risk-live-trading-badge").className = liveActive ? "text-emerald" : "text-amber";
  }
}

// ─────────────────────────────────────────────────────────────
//  TABLE RENDERERS
// ─────────────────────────────────────────────────────────────
function renderPositionsTable() {
  const tbody = el("positions-table-body");
  if (el("count-pos")) el("count-pos").textContent = state.positions.length;
  if (el("acc-open-count")) el("acc-open-count").textContent = `${state.positions.length} / ${state.pendingOrders.length}`;

  if (state.positions.length === 0) {
    tbody.innerHTML = '<tr><td colspan="10" class="text-center">No active open positions.</td></tr>';
    return;
  }

  const digits = getDigits(state.symbol);
  const curr = state.account ? state.account.currency : "USD";

  tbody.innerHTML = state.positions.map(p => {
    const pnlColor = p.pnl_usd >= 0 ? "var(--accent-emerald)" : "var(--accent-red)";
    const sideColor = p.side === "BUY" ? "var(--accent-emerald)" : "var(--accent-red)";

    // Result if the SL is hit: negative = loss, positive = profit already locked by the stop.
    let slStr = fmt(p.sl_price, digits);
    const slPnl = Number(p.potential_loss_usd || 0);
    if (p.sl_price > 0 && slPnl !== 0) {
      const locked = slPnl > 0;
      slStr += ` <span style="font-size:0.75rem;color:var(${locked ? "--accent-emerald" : "--accent-red"});font-weight:600;"`
        + ` title="${locked ? "Profit locked if the stop is hit" : "Loss if the stop is hit"}">`
        + `(${locked ? "+" : "-"}$${fmt(Math.abs(slPnl))})</span>`;
    }

    let tpStr = fmt(p.tp_price, digits);
    const profitAmt = Math.abs(p.potential_profit_usd || p.planned_profit_usd || 0);
    if (p.tp_price > 0 && profitAmt > 0) {
      tpStr += ` <span style="font-size:0.75rem;color:var(--accent-emerald);font-weight:600;">(+$${fmt(profitAmt)})</span>`;
    }

    return `<tr>
      <td>${p.trade_id}</td>
      <td style="color:${sideColor};font-weight:700">${p.side}</td>
      <td>${p.symbol}</td>
      <td>${fmt(p.lots, 2)}</td>
      <td>${fmt(p.entry_price, digits)}</td>
      <td>${slStr}</td>
      <td>${tpStr}</td>
      <td style="color:${pnlColor};font-weight:700">${p.pnl_usd >= 0 ? "+" : ""}${fmt(p.pnl_usd)} ${curr}</td>
      <td style="color:${pnlColor}">${p.pnl_pips >= 0 ? "+" : ""}${fmt(p.pnl_pips, 1)}</td>
      <td style="white-space:nowrap">
        <button class="btn-action-sm btn-action-edit" onclick="openModifySLTP('${p.trade_id}')" title="Change stop loss / take profit">✎ SL/TP</button>
        <button class="btn-action-sm" onclick="closePosition('${p.trade_id}')">✕ Close</button>
      </td>
    </tr>`;
  }).join("");
}

function renderPendingOrdersTable() {
  const tbody = el("pending-table-body");
  el("count-pending").textContent = state.pendingOrders.length;

  if (state.pendingOrders.length === 0) {
    tbody.innerHTML = '<tr><td colspan="10" class="text-center">No pending orders active.</td></tr>';
    return;
  }

  const digits = getDigits(state.symbol);

  tbody.innerHTML = state.pendingOrders.map(o => {
    const timeStr = new Date(o.open_time).toLocaleTimeString("en-GB");
    return `<tr>
      <td>${o.order_id}</td>
      <td>${o.symbol}</td>
      <td class="text-cyan">${o.order_type}</td>
      <td>${fmt(o.lots, 2)}</td>
      <td style="font-weight:700">${fmt(o.target_price, digits)}</td>
      <td>${fmt(o.sl_price, digits)}</td>
      <td>${fmt(o.tp_price, digits)}</td>
      <td>${timeStr}</td>
      <td><span class="badge-status-pass">PENDING</span></td>
      <td><button class="btn-action-sm" onclick="cancelPendingOrder('${o.order_id}')">Cancel</button></td>
    </tr>`;
  }).join("");
}

function changeHistoryPage(delta) {
  const totalCount = state.historyTotal !== undefined ? state.historyTotal : (state.history ? state.history.length : 0);
  const pageSize = state.historyPageSize || 30;
  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));
  state.historyPage = Math.max(1, Math.min(state.historyPage + delta, totalPages));

  apiFetch(`/api/v1/trade/history?today_only=true&page=${state.historyPage}&page_size=${pageSize}`).then(histRes => {
    if (histRes) {
      if (Array.isArray(histRes)) {
        state.history = histRes;
        state.historyTotal = histRes.length;
      } else {
        state.history = histRes.trades || [];
        state.historyTotal = histRes.total !== undefined ? histRes.total : (histRes.trades ? histRes.trades.length : 0);
      }
      renderTradeHistoryTable();
    }
  });
}
window.changeHistoryPage = changeHistoryPage;

function renderTradeHistoryTable() {
  const tbody = el("history-table-body");
  const totalCount = state.historyTotal !== undefined ? state.historyTotal : (state.history ? state.history.length : 0);

  if (el("count-history")) el("count-history").textContent = totalCount;

  const pageSize = state.historyPageSize || 30;
  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));
  if (state.historyPage > totalPages) {
    state.historyPage = totalPages;
  }
  if (state.historyPage < 1) {
    state.historyPage = 1;
  }

  const showingEl = el("history-showing-text");
  const prevBtn = el("btn-history-prev");
  const nextBtn = el("btn-history-next");
  const pageIndicator = el("history-page-indicator");

  if (totalCount === 0) {
    if (showingEl) showingEl.textContent = "Showing 0 of 0";
    if (pageIndicator) pageIndicator.textContent = "Page 1 of 1";
    if (prevBtn) prevBtn.disabled = true;
    if (nextBtn) nextBtn.disabled = true;
    if (tbody) tbody.innerHTML = '<tr><td colspan="13" class="text-center">No closed trade history for today.</td></tr>';
    return;
  }

  const startIndex = (state.historyPage - 1) * pageSize;
  const pageRecords = (state.history && state.history.length > pageSize) 
    ? state.history.slice(startIndex, Math.min(startIndex + pageSize, state.history.length)) 
    : (state.history || []);

  const displayStart = startIndex + 1;
  const displayEnd = Math.min(startIndex + pageRecords.length, totalCount);

  if (showingEl) showingEl.textContent = `Showing ${displayStart}–${displayEnd} of ${totalCount}`;
  if (pageIndicator) pageIndicator.textContent = `Page ${state.historyPage} of ${totalPages}`;
  if (prevBtn) prevBtn.disabled = (state.historyPage <= 1);
  if (nextBtn) nextBtn.disabled = (state.historyPage >= totalPages);

  const digits = getDigits(state.symbol);
  const curr = state.account ? state.account.currency : "USD";

  if (tbody) {
    tbody.innerHTML = pageRecords.map(h => {
      const isWin = h.pnl_usd >= 0;
      const badgeClass = isWin ? "badge-pnl-win" : "badge-pnl-loss";
      const closeDate = h.close_time ? new Date(h.close_time).toLocaleDateString("en-GB") : "-";
      const closeTime = h.close_time ? new Date(h.close_time).toLocaleTimeString("en-GB") : "-";
      const setupName = h.setup_name || "Unknown";
      const timeframeName = h.timeframe || "M15";

      return `<tr>
        <td>#${h.trade_id}</td>
        <td><strong>${h.symbol}</strong></td>
        <td><span class="setup-name-cell" title="${escHtml(setupName)}">${escHtml(setupName)}</span></td>
        <td><span class="badge-tf">${escHtml(timeframeName)}</span></td>
        <td style="color:${h.side === 'BUY' ? 'var(--accent-emerald)' : 'var(--accent-red)'};font-weight:700">${h.side}</td>
        <td>${fmt(h.lots, 2)}</td>
        <td>${fmt(h.entry_price, digits)}</td>
        <td>${fmt(h.close_price, digits)}</td>
        <td>${closeDate}</td>
        <td>${closeTime}</td>
        <td><span class="${badgeClass}">${isWin ? "+" : ""}${fmt(h.pnl_usd)} ${curr}</span></td>
        <td><span class="${badgeClass}">${isWin ? "+" : ""}${fmt(h.pnl_pips, 1)} pips</span></td>
        <td><span class="sub-text">${h.exit_reason || "CLOSED"}</span></td>
      </tr>`;
    }).join("");
  }
}

function renderAuditLog() {
  const container = el("audit-log-content");
  if (!container) return;
  container.innerHTML = state.auditLog.slice(0, 100).map(e =>
    `<div class="log-entry">
      <span class="log-time">[${e.time}]</span>
      <span class="log-msg" style="${e.color ? "color:" + e.color : ""}">${escHtml(e.msg)}</span>
    </div>`
  ).join("");
}

// ─────────────────────────────────────────────────────────────
//  5-STEP VERIFIED CONNECTION MODAL WORKFLOW
// ─────────────────────────────────────────────────────────────
function addModalLog(msg) {
  const container = el("mt5-connection-log");
  if (!container) return;
  const now = new Date().toLocaleTimeString("en-GB");
  container.innerHTML += `<div class="log-entry"><span class="log-time">[${now}]</span> ${escHtml(msg)}</div>`;
  container.scrollTop = container.scrollHeight;
}

async function handleFetchAccountPreview() {
  const login = parseInt(el("mt5-input-login").value);
  const password = el("mt5-input-password").value;
  const server = el("mt5-input-server").value.trim();
  const terminal_path = el("mt5-input-terminal-path") ? el("mt5-input-terminal-path").value.trim() : "";

  if (!login || isNaN(login)) {
    el("error-title").textContent = "Invalid Login";
    el("error-detail-reason").textContent = "Please enter a valid numerical MT5 account number.";
    el("mt5-fetch-error-box").classList.remove("hidden");
    return;
  }
  if (!password || password.length < 3) {
    el("error-title").textContent = "Invalid Password";
    el("error-detail-reason").textContent = "Password field cannot be empty.";
    el("mt5-fetch-error-box").classList.remove("hidden");
    return;
  }
  if (!server || server.length < 2) {
    el("error-title").textContent = "Invalid Server";
    el("error-detail-reason").textContent = "Please enter a valid MT5 server name (e.g. MetaQuotes-Demo).";
    el("mt5-fetch-error-box").classList.remove("hidden");
    return;
  }

  // Reset UI
  el("mt5-fetch-error-box").classList.add("hidden");
  el("mt5-account-preview-card").classList.add("hidden");
  el("btn-mt5-connect-submit").disabled = true;
  updateConnectionIndicator("Fetching Account...");

  addModalLog(`[${new Date().toLocaleTimeString("en-GB")}] Starting MT5 diagnostics for Account ${login} on ${server}...`);

  let preview = null;
  try {
    const opts = {
      method: "POST",
      headers: { 
        "Content-Type": "application/json",
        "X-API-Key": API_KEY
      },
      body: JSON.stringify({ login, password, server, terminal_path: terminal_path || null })
    };
    const res = await fetch(API_BASE + "/api/v1/account/fetch-preview", opts);
    if (res.ok) {
      preview = await res.json();
    } else {
      const errText = await res.text().catch(() => "");
      const errMsg = errText ? `HTTP ${res.status}: ${errText}` : `HTTP ${res.status} error from server`;
      updateConnectionIndicator("Disconnected");
      el("error-title").textContent = "Backend Error";
      el("error-detail-reason").textContent = errMsg;
      el("mt5-fetch-error-box").classList.remove("hidden");
      addModalLog(`[ERROR] Backend responded with: ${errMsg}`);
      addAuditEntry(`[MT5 CONNECT] Backend error: ${errMsg}`, "var(--accent-red)");
      return;
    }
  } catch (networkErr) {
    updateConnectionIndicator("Disconnected");
    el("error-title").textContent = "Cannot Reach Backend";
    el("error-detail-reason").textContent = `Cannot connect to backend server at ${API_BASE}. Make sure the server is running.`;
    el("mt5-fetch-error-box").classList.remove("hidden");
    addModalLog(`[ERROR] Network error reaching backend: ${networkErr.message}`);
    addAuditEntry(`[MT5 CONNECT] Cannot reach backend: ${networkErr.message}`, "var(--accent-red)");
    return;
  }

  // Display diagnostic logs from backend (step-by-step MT5 checks)
  if (preview && preview.diagnostic_logs && preview.diagnostic_logs.length > 0) {
    preview.diagnostic_logs.forEach(logLine => {
      const container = el("mt5-connection-log");
      if (container) {
        container.innerHTML += `<div class="log-entry"><span class="log-msg" style="${logLine.includes("ERROR") || logLine.includes("FAILED") ? "color:var(--accent-red)" : logLine.includes("WARNING") ? "color:var(--accent-amber)" : logLine.includes("SUCCESS") || logLine.includes("OK") ? "color:var(--accent-emerald)" : ""}">${escHtml(logLine)}</span></div>`;
        container.scrollTop = container.scrollHeight;
      }
    });
  }

  // Popup MUST NEVER close automatically on fetch-preview!
  if (!preview || !preview.success) {
    updateConnectionIndicator("Disconnected");
    const errReason = (preview && preview.error_reason)
      ? preview.error_reason
      : "Could not reach the backend server. Make sure the Python backend is running.";

    // Display prominent error alert box inside open modal
    el("error-title").textContent = "Account Verification Failed";
    el("error-detail-reason").textContent = errReason;
    el("mt5-fetch-error-box").classList.remove("hidden");

    addModalLog(`[ERROR] Verification Failed: ${errReason}`);
    addAuditEntry(`[MT5 CONNECT] Verification failed: ${errReason}`, "var(--accent-red)");
    return;
  }

  // Credentials Validated! Render Step 3 Account Preview Card with Real Raw MT5 Data
  state.verifiedPreview = preview;
  const curr = preview.currency || "USD";
  el("prev-login").textContent = preview.account_number;
  el("prev-name").textContent = preview.account_name || "Account " + preview.account_number;
  el("prev-broker").textContent = preview.broker;
  el("prev-server").textContent = preview.server;
  el("prev-type").textContent = preview.account_type;
  el("prev-currency").textContent = curr;
  el("prev-leverage").textContent = preview.leverage;
  el("prev-balance").textContent = `${fmt(preview.balance)} ${curr}`;
  el("prev-equity").textContent = `${fmt(preview.equity)} ${curr}`;
  el("prev-freemargin").textContent = `${fmt(preview.free_margin)} ${curr}`;
  el("prev-margin").textContent = `${fmt(preview.margin)} ${curr}`;
  el("prev-marginlevel").textContent = fmt(preview.margin_level_pct, 1) + "%";
  el("prev-company").textContent = preview.company;
  el("prev-build").textContent = preview.terminal_build;

  el("mt5-account-preview-card").classList.remove("hidden");

  // Enable Step 3 Connect Account button
  el("btn-mt5-connect-submit").disabled = false;
  updateConnectionIndicator("Connecting...");
  addModalLog(`[SUCCESS] Credentials verified for ${preview.account_name} (${preview.balance} ${curr})! Click 'Step 3: Connect Account' to complete setup.`);
  addAuditEntry(`[MT5 CONNECT] Verified account ${preview.account_name} (${preview.balance} ${curr}) preview. Ready to connect.`, "var(--accent-emerald)");
}


async function handleConnectAccountSubmit(e) {
  e.preventDefault();
  const login = parseInt(el("mt5-input-login").value);
  const password = el("mt5-input-password").value;
  const server = el("mt5-input-server").value.trim();
  const terminal_path = el("mt5-input-terminal-path") ? el("mt5-input-terminal-path").value.trim() : "";

  addModalLog(`Step 3: Creating live MT5 session for Account ${login}...`);
  updateConnectionIndicator("Connecting...");

  const res = await apiFetch("/api/v1/account/connect", "POST", { login, password, server, terminal_path: terminal_path || null });

  // Popup closes ONLY after confirmed success!
  if (res && res.success) {
    updateConnectionIndicator("Connected");
    addModalLog(`[CONNECTED] Live session created for Account ${login}!`);
    addAuditEntry(`[MT5 CONNECT] Connected session for Account ${login}`, "var(--accent-emerald)");
    
    // Close modal ONLY after success!
    el("modal-mt5-manager").classList.add("hidden");
    
    // Refresh Account Dashboard immediately
    await refreshAccountData();
  } else {
    updateConnectionIndicator("Reconnect Failed");
    const errMsg = res ? res.message : "Connection Failed";
    el("error-title").textContent = "Connection Failed";
    el("error-detail-reason").textContent = errMsg;
    el("mt5-fetch-error-box").classList.remove("hidden");
    addModalLog(`[ERROR] ${errMsg}`);
  }
}


async function refreshAccountData() {
  addAuditEntry("[REFRESH] Pulling latest account, positions & orders...", "var(--accent-cyan)");
  const res = await apiFetch("/api/v1/account/refresh", "POST");
  if (res && res.success) {
    if (res.account) updateAccount(res.account);
    const pos = await apiFetch("/api/v1/trade/positions");
    if (pos) { state.positions = pos; renderPositionsTable(); }
    const pnd = await apiFetch("/api/v1/trade/pending");
    if (pnd) { state.pendingOrders = pnd; renderPendingOrdersTable(); }
    addAuditEntry("[REFRESH] Refresh complete.", "var(--accent-emerald)");
  }
}

async function loadSavedAccounts() {
  const accounts = await apiFetch("/api/v1/account/saved");
  const container = el("saved-accounts-container");
  if (!container) return;

  if (!accounts || accounts.length === 0) {
    container.innerHTML = '<p class="text-center text-muted">No saved accounts stored.</p>';
    return;
  }

  container.innerHTML = accounts.map(a => `
    <div class="saved-account-card">
      <div class="saved-info">
        <h5>${escHtml(a.name)} (Account #${a.login})</h5>
        <p>${escHtml(a.server)} | ${escHtml(a.broker)}</p>
      </div>
      <div class="modal-actions-row">
        <button class="btn btn-sm btn-cyan" onclick="switchAccount('${a.id}')">Switch</button>
        <button class="btn btn-sm btn-outline text-red" onclick="removeSavedAccount('${a.id}')">Remove</button>
      </div>
    </div>
  `).join("");
}

async function switchAccount(accId) {
  addAuditEntry(`[ACCOUNT SWITCH] Switching to saved account ${accId}...`, "var(--accent-cyan)");
  const res = await apiFetch(`/api/v1/account/saved/${accId}/connect`, "POST");
  if (res && res.success) {
    addAuditEntry(`[ACCOUNT SWITCH] Switched to ${accId}`, "var(--accent-emerald)");
    el("modal-mt5-manager").classList.add("hidden");
    await refreshAccountData();
  }
}
window.switchAccount = switchAccount;

async function removeSavedAccount(accId) {
  const res = await apiFetch(`/api/v1/account/saved/${accId}`, "DELETE");
  if (res && res.success) {
    await loadSavedAccounts();
  }
}
window.removeSavedAccount = removeSavedAccount;

async function loadLoginHistory() {
  const history = await apiFetch("/api/v1/account/login-history");
  const tbody = el("login-history-tbody");
  if (!tbody) return;

  if (!history || history.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="text-center">No login history entries.</td></tr>';
    return;
  }

  tbody.innerHTML = history.map(h => `
    <tr>
      <td>${new Date(h.timestamp).toLocaleString("en-GB")}</td>
      <td>${h.account_number}</td>
      <td>${escHtml(h.broker)}</td>
      <td>${escHtml(h.server)}</td>
      <td><span class="${h.success ? "badge-status-pass" : "badge-status-fail"}">${h.success ? "SUCCESS" : "FAILED"}</span></td>
      <td><span class="sub-text">${escHtml(h.reason)}</span></td>
    </tr>
  `).join("");
}

// ─────────────────────────────────────────────────────────────
//  FINANCIAL CANVAS CHART ENGINE WITH INDICATORS & DRAWING TOOLS
// ─────────────────────────────────────────────────────────────
function renderFinancialChart() {
  const canvas = el("financialChartCanvas");
  if (!canvas) return;

  const holder = canvas.parentElement;
  canvas.width  = holder.clientWidth  || 800;
  canvas.height = holder.clientHeight || 350;

  const ctx = canvas.getContext("2d");
  const W   = canvas.width;
  const H   = canvas.height;

  ctx.clearRect(0, 0, W, H);

  // Background gradient
  const bgGrad = ctx.createLinearGradient(0, 0, 0, H);
  bgGrad.addColorStop(0, "#080d14");
  bgGrad.addColorStop(1, "#0a111a");
  ctx.fillStyle = bgGrad;
  ctx.fillRect(0, 0, W, H);

  if (!state.candles || state.candles.length === 0) {
    ctx.fillStyle = "#4a6080";
    ctx.font = "12px JetBrains Mono, monospace";
    ctx.textAlign = "center";
    ctx.fillText(`[ Loading ${state.timeframe || "M15"} Market Data... ]`, W / 2, H / 2);
    ctx.textAlign = "left";
    return;
  }

  const total = state.candles.length;
  const count = Math.min(total, Math.max(15, state.zoomCount));
  const endIdx = Math.max(count, Math.min(total, total - state.panOffset));
  const startIdx = Math.max(0, endIdx - count);
  const visible = state.candles.slice(startIdx, endIdx);

  if (visible.length === 0) return;

  const hasRSI  = state.indicators.showRSI;
  const hasMACD = state.indicators.showMACD;
  const subchartCount = (hasRSI ? 1 : 0) + (hasMACD ? 1 : 0);
  const subchartH     = subchartCount > 0 ? Math.min(75, (H * 0.2) / subchartCount) : 0;
  const mainH         = H - (subchartH * subchartCount) - 30;

  const highs = visible.map(c => c.high);
  const lows  = visible.map(c => c.low);
  const minP  = Math.min(...lows);
  const maxP  = Math.max(...highs);
  const rangeP = maxP - minP || 0.001;

  const priceToY = (p) => mainH - ((p - minP) / rangeP) * (mainH - 40) - 20;

  const gridLines = 6;
  const digits = getDigits(state.symbol);

  for (let i = 0; i <= gridLines; i++) {
    const y = 20 + (i / gridLines) * (mainH - 40);
    ctx.beginPath();
    ctx.moveTo(60, y);
    ctx.lineTo(W - 10, y);
    ctx.strokeStyle = "rgba(255,255,255,0.04)";
    ctx.lineWidth = 1;
    ctx.stroke();

    const price = maxP - (i / gridLines) * rangeP;
    ctx.fillStyle = "#4a6080";
    ctx.font = "10px JetBrains Mono, monospace";
    ctx.fillText(price.toFixed(digits), 5, y + 4);
  }

  const gap     = (W - 75) / visible.length;
  const candleW = Math.max(2, gap - 2);

  // Candlesticks & Volume Bars
  const maxVol = Math.max(...visible.map(c => c.volume)) || 1;
  const volScale = (mainH * 0.15) / maxVol;

  visible.forEach((c, i) => {
    const x      = 60 + i * gap;
    const isUp   = c.close >= c.open;
    const color  = isUp ? "#00e5a0" : "#ff4d6d";
    const body_h = Math.max(1, Math.abs(priceToY(c.open) - priceToY(c.close)));
    const body_y = Math.min(priceToY(c.open), priceToY(c.close));
    const cx     = x + gap / 2;

    const volH = c.volume * volScale;
    ctx.fillStyle = isUp ? "rgba(0,229,160,0.18)" : "rgba(255,77,109,0.18)";
    ctx.fillRect(x + 1, mainH - volH, candleW - 2, volH);

    ctx.beginPath();
    ctx.moveTo(cx, priceToY(c.high));
    ctx.lineTo(cx, priceToY(c.low));
    ctx.strokeStyle = color;
    ctx.lineWidth   = 1;
    ctx.stroke();

    ctx.fillStyle = isUp ? "rgba(0,229,160,0.85)" : "rgba(255,77,109,0.85)";
    ctx.fillRect(x + 1, body_y, candleW - 2, body_h);
  });

  // Open Position Lines
  state.positions.forEach(p => {
    if (p.symbol === state.symbol) {
      const y = priceToY(p.entry_price);
      ctx.beginPath();
      ctx.moveTo(60, y);
      ctx.lineTo(W - 10, y);
      ctx.strokeStyle = p.side === "BUY" ? "rgba(0,229,160,0.8)" : "rgba(255,77,109,0.8)";
      ctx.lineWidth = 1.2;
      ctx.setLineDash([6, 3]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  });

  renderUserDrawings(ctx, mainH, minP, rangeP, W, visible, startIdx, gap, digits);

  // Crosshair Overlay
  if (state.mousePos && state.mousePos.x >= 60 && state.mousePos.x <= W - 15 && state.mousePos.y <= mainH) {
    const mx = state.mousePos.x;
    const my = state.mousePos.y;

    ctx.beginPath();
    ctx.moveTo(mx, 0); ctx.lineTo(mx, mainH);
    ctx.moveTo(60, my); ctx.lineTo(W - 15, my);
    ctx.strokeStyle = "rgba(0,209,255,0.5)";
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.stroke();
    ctx.setLineDash([]);

    const hoverP = maxP - ((my - 20) / (mainH - 40)) * rangeP;
    ctx.fillStyle = "rgba(0,209,255,0.9)";
    ctx.fillRect(5, my - 9, 52, 18);
    ctx.fillStyle = "#000";
    ctx.font = "10px JetBrains Mono, monospace";
    ctx.fillText(hoverP.toFixed(digits), 8, my + 4);
  }
}

function renderUserDrawings(ctx, mainH, minP, rangeP, W, visible, startIdx, gap, digits) {
  const priceToY = (p) => mainH - ((p - minP) / rangeP) * (mainH - 40) - 20;
  const indexToX = (idx) => 60 + (idx - startIdx) * gap + gap / 2;

  const allDrawings = [...state.drawings];
  if (state.drawingInProgress) {
    allDrawings.push(state.drawingInProgress);
  }

  allDrawings.forEach(d => {
    ctx.strokeStyle = d.color || "#00d1ff";
    ctx.lineWidth = 1.5;

    if (d.type === "hline") {
      const y = priceToY(d.price);
      ctx.beginPath();
      ctx.moveTo(60, y); ctx.lineTo(W - 15, y);
      ctx.stroke();

      ctx.fillStyle = d.color || "#00d1ff";
      ctx.fillRect(W - 65, y - 9, 50, 18);
      ctx.fillStyle = "#000";
      ctx.font = "10px JetBrains Mono, monospace";
      ctx.fillText(d.price.toFixed(digits), W - 62, y + 4);
    } else if (d.type === "trendline") {
      const x1 = indexToX(d.p1.idx);
      const y1 = priceToY(d.p1.price);
      const x2 = indexToX(d.p2.idx);
      const y2 = priceToY(d.p2.price);

      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();
    } else if (d.type === "rectangle") {
      const x1 = indexToX(d.p1.idx);
      const y1 = priceToY(d.p1.price);
      const x2 = indexToX(d.p2.idx);
      const y2 = priceToY(d.p2.price);

      const rx = Math.min(x1, x2);
      const ry = Math.min(y1, y2);
      const rw = Math.abs(x2 - x1);
      const rh = Math.abs(y2 - y1);

      ctx.fillStyle = "rgba(0, 209, 255, 0.15)";
      ctx.fillRect(rx, ry, rw, rh);
      ctx.strokeRect(rx, ry, rw, rh);
    } else if (d.type === "fibo") {
      const pMin = Math.min(d.p1.price, d.p2.price);
      const pMax = Math.max(d.p1.price, d.p2.price);
      const diff = pMax - pMin;

      const levels = [
        { pct: 0.0, label: "0.0%" },
        { pct: 0.236, label: "23.6%" },
        { pct: 0.382, label: "38.2%" },
        { pct: 0.500, label: "50.0%" },
        { pct: 0.618, label: "61.8%" },
        { pct: 0.786, label: "78.6%" },
        { pct: 1.000, label: "100.0%" }
      ];

      levels.forEach(lvl => {
        const lvlPrice = pMax - diff * lvl.pct;
        const ly = priceToY(lvlPrice);

        ctx.beginPath();
        ctx.moveTo(60, ly);
        ctx.lineTo(W - 15, ly);
        ctx.strokeStyle = lvl.pct === 0.5 ? "#fbbf24" : "rgba(0, 209, 255, 0.6)";
        ctx.setLineDash([4, 4]);
        ctx.stroke();
        ctx.setLineDash([]);

        ctx.fillStyle = "#8ba0bb";
        ctx.font = "9px JetBrains Mono, monospace";
        ctx.fillText(`${lvl.label} (${lvlPrice.toFixed(digits)})`, 65, ly - 3);
      });
    }
  });
}

function initCanvasInteractions() {
  const canvas = el("financialChartCanvas");
  if (!canvas) return;

  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    state.zoomCount = e.deltaY < 0 ? Math.max(15, state.zoomCount - 5) : Math.min(200, state.zoomCount + 5);
    renderFinancialChart();
  });

  canvas.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return; // Primary click only
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    const total = state.candles.length;
    if (total === 0) return;
    const count = Math.min(total, Math.max(15, state.zoomCount));
    const endIdx = Math.max(count, Math.min(total, total - state.panOffset));
    const startIdx = Math.max(0, endIdx - count);
    const visible = state.candles.slice(startIdx, endIdx);
    if (visible.length === 0) return;

    const W = canvas.width;
    const H = canvas.height;
    const hasRSI  = state.indicators.showRSI;
    const hasMACD = state.indicators.showMACD;
    const subchartCount = (hasRSI ? 1 : 0) + (hasMACD ? 1 : 0);
    const subchartH     = subchartCount > 0 ? Math.min(75, (H * 0.2) / subchartCount) : 0;
    const mainH         = H - (subchartH * subchartCount) - 30;

    const highs = visible.map(c => c.high);
    const lows  = visible.map(c => c.low);
    const minP  = Math.min(...lows);
    const maxP  = Math.max(...highs);
    const rangeP = maxP - minP || 0.001;

    const gap = (W - 75) / visible.length;
    const hoverP = maxP - ((my - 20) / (mainH - 40)) * rangeP;
    const hoverIdx = Math.max(startIdx, Math.min(endIdx - 1, startIdx + Math.floor((mx - 60) / gap)));

    if (state.activeTool === "crosshairs") {
      state.isPanning = true;
      state.dragStart = { x: e.clientX, panOffset: state.panOffset };
    } else if (state.activeTool === "hline") {
      state.drawings.push({ type: "hline", price: hoverP, color: "#00d1ff" });
      addAuditEntry(`[DRAWING] Placed Horizontal Line @ ${hoverP.toFixed(5)}`);
      renderFinancialChart();
    } else if (["trendline", "rectangle", "fibo"].includes(state.activeTool)) {
      if (!state.drawingInProgress) {
        state.drawingInProgress = {
          type: state.activeTool,
          p1: { idx: hoverIdx, price: hoverP },
          p2: { idx: hoverIdx, price: hoverP },
          color: "#00d1ff"
        };
      } else {
        state.drawingInProgress.p2 = { idx: hoverIdx, price: hoverP };
        state.drawings.push({ ...state.drawingInProgress });
        addAuditEntry(`[DRAWING] Completed ${state.activeTool.toUpperCase()} drawing`);
        state.drawingInProgress = null;
        renderFinancialChart();
      }
    }
  });

  canvas.addEventListener("mousemove", (e) => {
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    state.mousePos = { x: mx, y: my };

    if (state.isPanning && state.dragStart) {
      const deltaX = e.clientX - state.dragStart.x;
      const gap = (canvas.width - 75) / Math.max(1, state.zoomCount);
      const deltaBars = Math.round(deltaX / gap);
      const newOffset = Math.max(0, Math.min(state.candles.length - state.zoomCount, state.dragStart.panOffset + deltaBars));
      if (newOffset !== state.panOffset) {
        state.panOffset = newOffset;
      }
    }

    if (state.drawingInProgress) {
      const total = state.candles.length;
      const count = Math.min(total, Math.max(15, state.zoomCount));
      const endIdx = Math.max(count, Math.min(total, total - state.panOffset));
      const startIdx = Math.max(0, endIdx - count);
      const visible = state.candles.slice(startIdx, endIdx);
      if (visible.length > 0) {
        const W = canvas.width;
        const H = canvas.height;
        const mainH = H - ((state.indicators.showRSI ? 75 : 0) + (state.indicators.showMACD ? 75 : 0)) - 30;
        const highs = visible.map(c => c.high);
        const lows  = visible.map(c => c.low);
        const minP  = Math.min(...lows);
        const maxP  = Math.max(...highs);
        const rangeP = maxP - minP || 0.001;
        const gap = (W - 75) / visible.length;

        const hoverP = maxP - ((my - 20) / (mainH - 40)) * rangeP;
        const hoverIdx = Math.max(startIdx, Math.min(endIdx - 1, startIdx + Math.floor((mx - 60) / gap)));
        state.drawingInProgress.p2 = { idx: hoverIdx, price: hoverP };
      }
    }

    renderFinancialChart();
  });

  const stopPanning = () => {
    state.isPanning = false;
    state.dragStart = null;
  };
  canvas.addEventListener("mouseup", stopPanning);
  canvas.addEventListener("mouseleave", stopPanning);
}

// ─────────────────────────────────────────────────────────────
//  ORDER EXECUTION & BOT CONTROLLER
// ─────────────────────────────────────────────────────────────
function showTradeAlert(type, title, message, instructions = null) {
  const alertEl = el("trade-execution-alert");
  if (!alertEl) return;

  alertEl.className = "trade-alert-banner alert-" + type;
  el("trade-alert-title").textContent = title;
  el("trade-alert-message").textContent = message;

  const iconEl = el("trade-alert-icon");
  if (type === "rejected") {
    iconEl.className = "fa-solid fa-triangle-exclamation";
  } else if (type === "success") {
    iconEl.className = "fa-solid fa-circle-check";
  } else {
    iconEl.className = "fa-solid fa-circle-info";
  }

  const instEl = el("trade-alert-instructions");
  if (instructions && instructions.trim()) {
    instEl.innerHTML = `<strong><i class="fa-solid fa-wrench"></i> Action Required:</strong> ${escHtml(instructions)}`;
    instEl.classList.remove("hidden");
  } else {
    instEl.classList.add("hidden");
  }

  alertEl.classList.remove("hidden");

  if (type === "success") {
    setTimeout(() => {
      alertEl.classList.add("hidden");
    }, 8000);
  }
}

function closeTradeAlert() {
  const alertEl = el("trade-execution-alert");
  if (alertEl) alertEl.classList.add("hidden");
}
window.closeTradeAlert = closeTradeAlert;

async function closePosition(tradeId) {
  addAuditEntry(`[TRADE] Closing position ${tradeId}...`, "var(--accent-amber)");
  const res = await apiFetch(`/api/v1/trade/positions/close?trade_id=${tradeId}`, "POST");
  if (res) {
    if (res.account) updateAccount(res.account);
    if (res.positions) { state.positions = res.positions; renderPositionsTable(); }
    if (res.success) {
      addAuditEntry(`[TRADE] ${res.message}`, "var(--accent-emerald)");
      showTradeAlert("success", "Position Closed", res.message);
    } else {
      addAuditEntry(`[TRADE] Close position failed: ${res.message}`, "var(--accent-red)");
      showTradeAlert("rejected", "Close Position Failed", res.message);
    }
  }
  await refreshAccountData();
}
window.closePosition = closePosition;

// ─────────────────────────────────────────────────────────────
//  MODIFY SL / TP OF AN OPEN POSITION
// ─────────────────────────────────────────────────────────────
const sltpEdit = { pos: null, bid: 0, ask: 0, timer: null, saving: false };

function sltpIsBuy() { return sltpEdit.pos && String(sltpEdit.pos.side).toUpperCase() === "BUY"; }

// USD gained/lost per 1.0 price move for this position.
function sltpValuePerUnit(p) {
  const pairs = [[p.potential_loss_usd, p.sl_price], [p.potential_profit_usd, p.tp_price]];
  for (const [usd, px] of pairs) {
    const dist = Math.abs((px || 0) - p.entry_price);
    if (px > 0 && dist > 0 && Math.abs(usd || 0) > 0) return Math.abs(usd) / dist;
  }
  const sym = String(p.symbol || "").toUpperCase();
  const contract = sym.startsWith("XAU") ? 100 : sym.startsWith("XAG") ? 5000 : 100000;
  return contract * (p.lots || 0);
}

// Signed USD result if price reaches `px` (positive = profit).
function sltpResultAt(px) {
  const p = sltpEdit.pos;
  if (!p || !(px > 0)) return null;
  const move = sltpIsBuy() ? px - p.entry_price : p.entry_price - px;
  return move * sltpValuePerUnit(p);
}

function sltpShowResult(id, usd, label) {
  const box = el(id);
  if (usd === null) { box.textContent = ""; return; }
  box.textContent = `${label}: ${usd >= 0 ? "+" : "-"}$${fmt(Math.abs(usd))}`;
  box.style.color = usd >= 0 ? "var(--accent-emerald)" : "var(--accent-red)";
}

// Same rules as the backend; returns an error message or "".
function sltpValidate(sl, tp) {
  const digits = getDigits(sltpEdit.pos.symbol);
  const { bid, ask } = sltpEdit;
  if (!(sl > 0)) return "Stop Loss is required.";
  if (tp < 0 || isNaN(tp)) return "Take Profit must be 0 or a positive price.";
  if (!(bid > 0 && ask > 0)) return "";  // no live price yet: the server will check
  if (sltpIsBuy()) {
    if (sl >= bid) return `For a BUY, the stop loss must be below the current price (${fmt(bid, digits)}).`;
    if (tp > 0 && tp <= ask) return `For a BUY, the take profit must be above the current price (${fmt(ask, digits)}).`;
  } else {
    if (sl <= ask) return `For a SELL, the stop loss must be above the current price (${fmt(ask, digits)}).`;
    if (tp > 0 && tp >= bid) return `For a SELL, the take profit must be below the current price (${fmt(bid, digits)}).`;
  }
  return "";
}

function sltpRefreshPreview() {
  const p = sltpEdit.pos;
  if (!p) return;
  const sl = parseFloat(el("msl-new-sl").value);
  const tpRaw = el("msl-new-tp").value.trim();
  const tp = tpRaw === "" ? 0 : parseFloat(tpRaw);

  sltpShowResult("msl-sl-result", sl > 0 ? sltpResultAt(sl) : null, "If SL is hit");
  sltpShowResult("msl-tp-result", tp > 0 ? sltpResultAt(tp) : null, "If TP is hit");
  if (!(tp > 0)) { el("msl-tp-result").textContent = "No take profit: trailing stop decides the exit"; el("msl-tp-result").style.color = "var(--text-muted)"; }

  const err = sltpValidate(sl, tp);
  const errBox = el("msl-error");
  errBox.textContent = err;
  errBox.classList.toggle("hidden", !err);

  // Warn when the change adds risk compared to the current stop.
  const warn = el("msl-warning");
  const widened = p.sl_price > 0 && sl > 0 && (sltpIsBuy() ? sl < p.sl_price : sl > p.sl_price);
  if (!err && widened) {
    const extra = Math.abs(sl - p.sl_price) * sltpValuePerUnit(p);
    warn.textContent = `This moves the stop further away and adds about $${fmt(extra)} of risk. `
      + "The bot's trailing stop may pull it back at the next candle close.";
    warn.classList.remove("hidden");
  } else {
    warn.classList.add("hidden");
  }

  const unchanged = Math.abs(sl - (p.sl_price || 0)) < 1e-9 && Math.abs(tp - (p.tp_price || 0)) < 1e-9;
  el("msl-btn-save").disabled = !!err || unchanged || sltpEdit.saving;
}

function sltpRenderHeader() {
  const p = sltpEdit.pos;
  const digits = getDigits(p.symbol);
  const curr = state.account ? state.account.currency : "USD";
  el("msl-ticket").textContent = `#${p.trade_id}`;
  el("msl-side").innerHTML = `<span style="color:var(${sltpIsBuy() ? "--accent-emerald" : "--accent-red"})">${p.side}</span> ${p.symbol} · ${fmt(p.lots, 2)} lot`;
  el("msl-entry").textContent = fmt(p.entry_price, digits);
  el("msl-price").textContent = sltpEdit.bid > 0 ? `${fmt(sltpEdit.bid, digits)} / ${fmt(sltpEdit.ask, digits)}` : "loading...";
  el("msl-pnl").innerHTML = `<span style="color:var(${p.pnl_usd >= 0 ? "--accent-emerald" : "--accent-red"})">${p.pnl_usd >= 0 ? "+" : ""}${fmt(p.pnl_usd)} ${curr}</span>`;
  el("msl-cur-sl").textContent = p.sl_price > 0 ? fmt(p.sl_price, digits) : "none";
  el("msl-cur-tp").textContent = p.tp_price > 0 ? fmt(p.tp_price, digits) : "none";
}

async function sltpPollPrice() {
  const p = sltpEdit.pos;
  if (!p) return;
  const t = await apiFetch(`/api/v1/market/ticker?symbol=${encodeURIComponent(p.symbol)}`);
  if (!sltpEdit.pos || sltpEdit.pos.trade_id !== p.trade_id) return;  // modal closed meanwhile
  if (t && t.bid > 0 && t.ask > 0) { sltpEdit.bid = t.bid; sltpEdit.ask = t.ask; }
  // Keep the live P&L and current SL (the trailing stop may move it) in step.
  const fresh = state.positions.find(x => String(x.trade_id) === String(p.trade_id));
  if (!fresh) {
    closeModifySLTP();
    showTradeAlert("rejected", "Position Closed", `Position ${p.trade_id} is no longer open.`);
    return;
  }
  sltpEdit.pos = fresh;
  sltpRenderHeader();
  sltpRefreshPreview();
}

function sltpFillInputs() {
  const p = sltpEdit.pos;
  const digits = getDigits(p.symbol);
  el("msl-new-sl").value = p.sl_price > 0 ? Number(p.sl_price).toFixed(digits) : "";
  el("msl-new-tp").value = p.tp_price > 0 ? Number(p.tp_price).toFixed(digits) : "0";
}

function openModifySLTP(tradeId) {
  const p = state.positions.find(x => String(x.trade_id) === String(tradeId));
  if (!p) { showTradeAlert("rejected", "Not Found", `Position ${tradeId} is no longer open.`); return; }
  sltpEdit.pos = p;
  sltpEdit.bid = 0; sltpEdit.ask = 0; sltpEdit.saving = false;
  const step = Math.pow(10, -getDigits(p.symbol));
  el("msl-new-sl").step = step; el("msl-new-tp").step = step;
  sltpFillInputs();
  sltpRenderHeader();
  sltpRefreshPreview();
  el("modal-modify-sltp").classList.remove("hidden");
  clearInterval(sltpEdit.timer);
  sltpPollPrice();
  sltpEdit.timer = setInterval(sltpPollPrice, 2000);
}
window.openModifySLTP = openModifySLTP;

function closeModifySLTP() {
  clearInterval(sltpEdit.timer);
  sltpEdit.timer = null;
  sltpEdit.pos = null;
  el("modal-modify-sltp").classList.add("hidden");
}

async function saveModifySLTP(ev) {
  ev.preventDefault();
  const p = sltpEdit.pos;
  if (!p || sltpEdit.saving) return;
  const sl = parseFloat(el("msl-new-sl").value);
  const tpRaw = el("msl-new-tp").value.trim();
  const tp = tpRaw === "" ? 0 : parseFloat(tpRaw);
  const err = sltpValidate(sl, tp);
  if (err) { el("msl-error").textContent = err; el("msl-error").classList.remove("hidden"); return; }

  sltpEdit.saving = true;
  const btn = el("msl-btn-save");
  btn.disabled = true;
  btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Saving...';
  addAuditEntry(`[TRADE] Modifying #${p.trade_id}: SL ${sl}, TP ${tp || "none"}...`, "var(--accent-amber)");

  const res = await apiFetch(`/api/v1/trade/positions/modify?trade_id=${encodeURIComponent(p.trade_id)}&sl=${sl}&tp=${tp}`, "POST");

  sltpEdit.saving = false;
  btn.innerHTML = '<i class="fa-solid fa-floppy-disk"></i> Save SL / TP';
  if (res && res.positions) { state.positions = res.positions; renderPositionsTable(); }

  if (res && res.success) {
    addAuditEntry(`[TRADE] ${res.message}`, "var(--accent-emerald)");
    showTradeAlert("success", "SL / TP Updated", res.message);
    closeModifySLTP();
    await refreshAccountData();
  } else {
    const msg = res ? res.message : "Could not reach the server.";
    addAuditEntry(`[TRADE] Modify failed: ${msg}`, "var(--accent-red)");
    sltpRefreshPreview();  // re-enables Save; then show the server's reason
    el("msl-error").textContent = msg;
    el("msl-error").classList.remove("hidden");
  }
}

function initModifySLTP() {
  if (!el("modal-modify-sltp")) return;
  el("btn-close-modify-sltp").addEventListener("click", closeModifySLTP);
  el("msl-btn-cancel").addEventListener("click", closeModifySLTP);
  el("modal-modify-sltp").addEventListener("click", e => { if (e.target.id === "modal-modify-sltp") closeModifySLTP(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && sltpEdit.pos) closeModifySLTP(); });
  el("form-modify-sltp").addEventListener("submit", saveModifySLTP);
  el("msl-new-sl").addEventListener("input", sltpRefreshPreview);
  el("msl-new-tp").addEventListener("input", sltpRefreshPreview);
  el("msl-btn-breakeven").addEventListener("click", () => {
    el("msl-new-sl").value = Number(sltpEdit.pos.entry_price).toFixed(getDigits(sltpEdit.pos.symbol));
    sltpRefreshPreview();
  });
  el("msl-btn-no-tp").addEventListener("click", () => { el("msl-new-tp").value = "0"; sltpRefreshPreview(); });
  el("msl-btn-reset").addEventListener("click", () => { sltpFillInputs(); sltpRefreshPreview(); });
}

async function cancelPendingOrder(orderId) {
  addAuditEntry(`[PENDING] Cancelling order ${orderId}...`, "var(--accent-amber)");
  const res = await apiFetch(`/api/v1/trade/pending/cancel?order_id=${orderId}`, "POST");
  if (res) {
    if (res.account) updateAccount(res.account);
    if (res.pending_orders) { state.pendingOrders = res.pending_orders; renderPendingOrdersTable(); }
    if (res.success) {
      addAuditEntry(`[PENDING] ${res.message}`, "var(--accent-emerald)");
      showTradeAlert("success", "Pending Order Cancelled", res.message);
    } else {
      addAuditEntry(`[PENDING] Cancel order failed: ${res.message}`, "var(--accent-red)");
      showTradeAlert("rejected", "Cancel Order Failed", res.message);
    }
  }
  await refreshAccountData();
}
window.cancelPendingOrder = cancelPendingOrder;

async function executeManualTrade(side) {
  if (state.connectionState !== "Connected" && (!state.account || !state.account.is_connected)) {
    addAuditEntry("[MANUAL ORDER] Execution blocked: MT5 Account is not connected.", "var(--accent-red)");
    showTradeAlert("rejected", "MT5 Account Not Connected", "Please connect your MT5 account via the 'Verified Connection' modal before placing trades.");
    alert("MT5 Account is not connected. Please connect your MT5 account via 'Verified Connection' modal before placing trades.");
    return;
  }

  const symbol = state.symbol;
  const orderType = el("manual-order-type").value;
  const lots = parseFloat(el("manual-lots").value) || 0.10;
  const price = parseFloat(el("manual-target-price").value) || 0.0;
  const sl = parseFloat(el("manual-sl-price").value) || 0.0;
  const tp = parseFloat(el("manual-tp-price").value) || 0.0;

  if (lots <= 0 || lots > 100) {
    addAuditEntry("[MANUAL ORDER] Invalid volume / lot size.", "var(--accent-red)");
    showTradeAlert("rejected", "Invalid Volume", "Enter a valid lot size between 0.01 and 100.0.");
    return;
  }

  const btnBuy = el("btn-manual-buy");
  const btnSell = el("btn-manual-sell");
  btnBuy.disabled = true;
  btnSell.disabled = true;

  addAuditEntry(`[MANUAL ORDER] Submitting ${side} ${orderType} ${lots} lots on ${symbol}...`, "var(--accent-cyan)");
  const res = await apiFetch("/api/v1/trade/manual", "POST", { symbol, side, order_type: orderType, lots, price, sl, tp });

  btnBuy.disabled = false;
  btnSell.disabled = false;

  if (res) {
    if (res.account) updateAccount(res.account);
    if (res.positions) { state.positions = res.positions; renderPositionsTable(); }
    if (res.pending_orders) { state.pendingOrders = res.pending_orders; renderPendingOrdersTable(); }
    
    if (res.success) {
      addAuditEntry(`[MANUAL ORDER] Success: ${res.message}`, "var(--accent-emerald)");
      showTradeAlert("success", "MT5 Order Executed", res.message);
    } else {
      const msg = res.message || "Order execution failed";
      addAuditEntry(`[MANUAL ORDER] Rejected: ${msg}`, "var(--accent-red)");
      
      let inst = null;
      if (msg.includes("10027") || msg.includes("Algo Trading is DISABLED")) {
        inst = "Open MetaTrader 5 desktop software, click the 'Algo Trading' button in the top main toolbar (or press Ctrl+E) to allow automated/API orders, then try your trade again.";
      } else if (msg.includes("10016") || msg.includes("Invalid Stop Loss")) {
        inst = "Check your Stop Loss and Take Profit prices. They must be set beyond the broker's minimum stop level distance from current market price.";
      } else if (msg.includes("10019") || msg.includes("Insufficient margin")) {
        inst = "Lower your volume/lot size or close existing active positions to free up margin.";
      } else if (msg.includes("10018") || msg.includes("Market is closed")) {
        inst = "The financial market for this symbol is currently closed. Wait for market session opening.";
      }

      showTradeAlert("rejected", "MT5 Trade Rejected", msg, inst);
    }
  } else {
    addAuditEntry("[MANUAL ORDER] Failed: No response from backend server", "var(--accent-red)");
    showTradeAlert("rejected", "Server Error", "No response received from backend trading engine.");
  }
  await refreshAccountData();
}

async function sendBotCommand(cmd) {
  addAuditEntry(`[BOT COMMAND] Executing /bot/${cmd}...`, "var(--accent-cyan)");
  let url = `/api/v1/bot/${cmd}`;
  if (cmd === "start") {
    url += `?symbol=${state.symbol}&timeframe=${state.timeframe}`;
  }
  const res = await apiFetch(url, "POST");
  if (res && res.success) {
    updateBotStatusUI(res.status);
    addAuditEntry(`[BOT COMMAND] Status updated → ${res.status}: ${res.message}`, "var(--accent-emerald)");
  }
}

// ─────────────────────────────────────────────────────────────
//  RISK SETTINGS CONTEXTUAL HELP TOOLTIP ENGINE
// ─────────────────────────────────────────────────────────────
const RISK_HELP_DICTIONARY = {
  "cfg-lot-mode": {
    title: "Position Sizing Mode",
    summary: "Controls how trade volume (lots) is calculated—either static user-defined FIXED lots or DYNAMIC RISK based on Stop Loss distance.",
    increase: "<strong>DYNAMIC RISK:</strong> Scales order volume dynamically so dollar risk matches your exact risk % per trade.",
    decrease: "<strong>FIXED LOT:</strong> Uses a constant volume (e.g. 0.10 lots) regardless of account size or Stop Loss distance.",
    execution: "Determines exact lot size submitted to MetaTrader 5.",
    limit: "Calculated lot size is capped at Maximum Allowed Lot Cap if it exceeds the limit.",
    example: "With $10,000 equity, 1% risk ($100), and a 50-pip SL, DYNAMIC RISK automatically calculates 0.20 lots."
  },
  "cfg-user-lot": {
    title: "Fixed Lot Size (Lots)",
    summary: "Defines the exact static trade volume used when Position Sizing Mode is set to FIXED.",
    increase: "<strong>HIGHER LOT SIZE:</strong> Increases dollar profit and loss per pip move.",
    decrease: "<strong>LOWER LOT SIZE:</strong> Reduces capital risk and P&L fluctuation per trade.",
    execution: "Directly sets order volume submitted to MT5 in FIXED mode.",
    limit: "Capped automatically if it exceeds Maximum Allowed Lot Cap or available free margin.",
    example: "Trading 0.10 lots on XAUUSD risks $10 for every $1.00 price move ($1.00 per pip)."
  },
  "cfg-max-allowed-lot": {
    title: "Maximum Allowed Lot Cap",
    summary: "Establishes a hard maximum ceiling on position volume allowed for any trade across all modes.",
    increase: "<strong>HIGHER CAP:</strong> Allows larger position sizes for tight Stop Loss setups.",
    decrease: "<strong>LOWER CAP:</strong> Enforces strict protection against over-leveraging or oversized trade entries.",
    execution: "Automatically trims order volume down to this cap if risk calculations generate a larger lot size.",
    limit: "Order volume cannot exceed this cap under any circumstances.",
    example: "If dynamic sizing calculates 2.50 lots but your cap is 1.00 lot, the order is safely trimmed to 1.00 lot."
  },
  "cfg-max-risk": {
    title: "Max Risk Per Trade (%)",
    summary: "Defines the maximum percentage of your account equity that can be placed at risk on a single trade.",
    increase: "<strong>HIGHER VALUE (e.g. 3.0%):</strong> Allows larger position sizes and potential profit, with greater account drawdown risk.",
    decrease: "<strong>LOWER VALUE (e.g. 0.5%):</strong> Reduces exposure per trade, protecting account equity conservatively.",
    execution: "Used in DYNAMIC RISK mode to compute lot size based on Stop Loss distance.",
    limit: "Blocks trade execution if FIXED lot risks more than this percentage or if SL distance makes min lot size exceed this limit.",
    example: "On a $10,000 account, a 1.0% setting limits your maximum loss to $100 if Stop Loss is hit."
  },
  "cfg-daily-loss": {
    title: "Daily Loss Circuit Breaker (%)",
    summary: "Sets the maximum cumulative loss percentage allowed in a single trading day based on starting daily equity.",
    increase: "<strong>HIGHER PERCENTAGE:</strong> Allows wider daily drawdown before halting automated trading.",
    decrease: "<strong>LOWER PERCENTAGE:</strong> Halts trading quickly during adverse market conditions to preserve capital.",
    execution: "Continuously monitors daily realized and unrealized P&L.",
    limit: "Locks automated trading for the remainder of the day when daily loss reaches this limit.",
    example: "On a $10,000 account, a 3.0% daily limit locks trading after $300 total daily loss until the daily reset."
  },
  "cfg-max-daily-loss-amt": {
    title: "Max Daily Loss Amount ($)",
    summary: "Sets a fixed monetary dollar ceiling for maximum cumulative loss allowed in one trading day.",
    increase: "<strong>HIGHER DOLLAR AMOUNT:</strong> Permits larger absolute dollar drawdown before triggering circuit breaker.",
    decrease: "<strong>LOWER DOLLAR AMOUNT:</strong> Stops trading earlier in currency terms regardless of account percentage.",
    execution: "Monitors daily dollar P&L alongside percentage daily limits.",
    limit: "Locks automated trading when daily dollar losses reach or exceed this threshold.",
    example: "Setting $500 locks the bot if total daily losses hit $500, even if the percentage limit has not been reached."
  },
  "cfg-weekly-loss": {
    title: "Weekly Loss Circuit Breaker (%)",
    summary: "Sets the maximum cumulative loss percentage allowed across a full trading week, calculated against starting weekly balance.",
    increase: "<strong>HIGHER PERCENTAGE:</strong> Accommodates multi-day market choppiness and drawdowns.",
    decrease: "<strong>LOWER PERCENTAGE:</strong> Prevents deep multi-day equity drawdowns during unfavorable market regimes.",
    execution: "Tracks rolling weekly account performance.",
    limit: "Blocks all automated trades for the remainder of the week when weekly loss limit is reached.",
    example: "A 7.0% limit on a $10,000 balance halts all trade execution once weekly loss reaches $700."
  },
  "cfg-max-positions": {
    title: "Maximum Open Trades",
    summary: "Limits how many trades can remain open at the same time across all connected symbols and strategies.",
    increase: "<strong>HIGHER LIMIT:</strong> Allows holding more positions concurrently, increasing total market exposure.",
    decrease: "<strong>LOWER LIMIT:</strong> Reduces simultaneous exposure and margin usage conservatively.",
    execution: "Checks active MT5 position count before placing new orders.",
    limit: "Blocks new trade entries when active open positions equal or exceed this number.",
    example: "If maximum open trades is 5 and 5 trades are active, any new trade signal is safely rejected."
  },
  "cfg-auto-trade-enabled": {
    title: "Automated Trading Engine",
    summary: "Master switch enabling or disabling fully autonomous order execution on MetaTrader 5.",
    increase: "<strong>ENABLED:</strong> Bot automatically opens orders 24/7 when signals pass all risk filters.",
    decrease: "<strong>DISABLED:</strong> Bot operates in signal-only mode, generating alerts without placing live trades.",
    execution: "Directly enables or disables automated MT5 order placement.",
    limit: "Stops all automated trade placement when set to DISABLED.",
    example: "When DISABLED, AI signals display on dashboard and audit log, but no live orders are placed automatically."
  },
  "cfg-max-spread": {
    title: "Max Allowed Spread (Pips)",
    summary: "Sets the maximum broker bid/ask spread allowed in pips for trade execution.",
    increase: "<strong>HIGHER SPREAD (e.g. 5.0 pips):</strong> Permits execution during wider spread conditions (e.g. news or market open).",
    decrease: "<strong>LOWER SPREAD (e.g. 1.5 pips):</strong> Prevents trading during low liquidity or high slippage events.",
    execution: "Checks real-time broker spread before placing orders.",
    limit: "Blocks trade execution if live broker spread exceeds this threshold.",
    example: "If maximum spread is 2.5 pips and news spikes spread to 4.0 pips, trade execution is blocked."
  },
  "cfg-max-daily-trades": {
    title: "Max Automated Trades Per Day",
    summary: "Limits the total number of automated trades allowed within a single trading day.",
    increase: "<strong>HIGHER LIMIT:</strong> Permits higher trading frequency throughout the day.",
    decrease: "<strong>LOWER LIMIT:</strong> Caps daily trade count, protecting against over-trading and excessive commission fees.",
    execution: "Counts automated trades executed since midnight UTC / daily reset.",
    limit: "Blocks any additional automated trades once daily trade count reaches limit.",
    example: "With a limit of 10, after 10 trades execute today, the 11th signal is blocked until tomorrow's reset."
  },
  "cfg-enable-live-trading": {
    title: "Live Trading Safety Lock (ENABLE_LIVE_TRADING)",
    summary: "Master safety toggle allowing order placement on REAL live MT5 accounts vs DEMO accounts only.",
    increase: "<strong>TRUE (LIVE ENABLED):</strong> Allows automated order placement on REAL broker accounts.",
    decrease: "<strong>FALSE (DEMO ONLY):</strong> Hard locks trade execution to DEMO accounts only, blocking real money risk.",
    execution: "Performs strict safety verification before sending orders to live broker servers.",
    limit: "Completely blocks order placement if connected to a REAL account while set to FALSE.",
    example: "If connected to a live real account ($50,000) and lock is FALSE, order execution is blocked for safety."
  },
  "cfg-strategy-profile": {
    title: "Trading Strategy",
    summary: "Which rules create entry signals. DONCHIAN TREND is the backtested H1 strategy; LEGACY is the old multi-setup engine that lost money on demo.",
    increase: "<strong>DONCHIAN TREND:</strong> ~60 trades/year, 34-40% win rate, profit comes from a few large trend moves.",
    decrease: "<strong>LEGACY:</strong> many M15 trades from 15 unverified setups. Not recommended.",
    execution: "Decides when a BUY or SELL signal is produced.",
    limit: "All risk limits below still apply to every signal.",
    example: "A close above the last 100 H1 highs opens a BUY with a 3xATR stop."
  },
  "cfg-max-consecutive-losses": {
    title: "Pause After Consecutive Losses",
    summary: "Pauses new entries after this many losing trades in a row. The pause ends automatically 24 hours after the last loss.",
    increase: "<strong>Higher / 0:</strong> fewer pauses. 0 turns it off.",
    decrease: "<strong>Lower:</strong> pauses sooner. Trend strategies often lose 5-15 in a row, so a low value can skip the winning trade.",
    execution: "Checked before every new entry.",
    limit: "Open trades are not closed by this setting.",
    example: "With 3, after 3 losses the bot waits 24h before trading again."
  },
  "cfg-max-per-direction": {
    title: "Max Positions Per Direction",
    summary: "Maximum open trades in the same direction (BUY or SELL) on one symbol.",
    increase: "<strong>Higher:</strong> allows adding trades in the same direction (stacking).",
    decrease: "<strong>1:</strong> one BUY and/or one SELL at a time. Stacking caused most past losses.",
    execution: "Checked before every new entry.",
    limit: "0 turns this guard off.",
    example: "With 1, a second BUY is blocked while a BUY is open."
  },
  "cfg-min-entry-gap": {
    title: "Minimum Minutes Between Entries",
    summary: "Minimum time since the last trade was opened before a new one is allowed. Read from the trade database, so it survives restarts.",
    increase: "<strong>Higher:</strong> fewer, more spaced-out entries.",
    decrease: "<strong>Lower / 0:</strong> entries can follow each other quickly.",
    execution: "Checked before every new entry.",
    limit: "0 turns this guard off.",
    example: "With 30, a trade opened at 10:00 blocks new entries until 10:30."
  },
  "cfg-blocked-hours": {
    title: "Blocked Hours",
    summary: "Hours (PC clock, 0-23) when no new trades open. Leave empty to allow all hours.",
    increase: "<strong>More hours:</strong> fewer trading hours.",
    decrease: "<strong>Empty:</strong> trade at any hour (the backtest used all hours).",
    execution: "Checked before every new entry.",
    limit: "Open trades are still managed during blocked hours.",
    example: "18,19 blocks new entries from 18:00 to 19:59."
  }
};

let activeTooltipElem = null;

function getOrCreateRiskTooltip() {
  if (!activeTooltipElem) {
    activeTooltipElem = document.createElement("div");
    activeTooltipElem.className = "risk-tooltip-popup";
    document.body.appendChild(activeTooltipElem);
  }
  return activeTooltipElem;
}

function showRiskHelpTooltip(fieldKey, targetElement, event) {
  const tooltip = getOrCreateRiskTooltip();
  let info = RISK_HELP_DICTIONARY[fieldKey];
  
  if (!info) {
    // Dynamic fallback for any unlisted field added in future
    const labelElem = targetElement.querySelector ? targetElement.querySelector("label") : null;
    const rawTitle = (labelElem ? labelElem.textContent : "") || targetElement.title || fieldKey;
    const rawDesc = targetElement.title || "Configures custom risk parameter settings.";
    info = {
      title: rawTitle.replace(/\*/g, "").trim(),
      summary: rawDesc,
      increase: "<strong>HIGHER VALUE:</strong> Adjusts parameter sensitivity or limit threshold upward.",
      decrease: "<strong>LOWER VALUE:</strong> Adjusts parameter sensitivity or limit threshold downward.",
      execution: "Evaluates risk parameter rules prior to order placement.",
      limit: "Blocks or modifies trade execution when parameter limit is reached.",
      example: "Adjust value to customize your trading risk profile."
    };
  }

  tooltip.innerHTML = `
    <div class="risk-tooltip-header">
      <i class="fa-solid fa-shield-halved"></i>
      <span>${escHtml(info.title)}</span>
    </div>
    <div class="risk-tooltip-summary">${info.summary}</div>
    <div class="risk-tooltip-grid">
      <div class="risk-tooltip-badge badge-increase">
        <strong>📈 Higher Value</strong>
        ${info.increase}
      </div>
      <div class="risk-tooltip-badge badge-decrease">
        <strong>📉 Lower Value</strong>
        ${info.decrease}
      </div>
    </div>
    <div class="risk-tooltip-section section-execution">
      <strong>⚡ Execution Impact:</strong> ${info.execution}
    </div>
    <div class="risk-tooltip-section section-limit">
      <strong>🛑 Breached / Limit Impact:</strong> ${info.limit}
    </div>
    <div class="risk-tooltip-example">
      <strong>💡 Practical Example:</strong> ${info.example}
    </div>
  `;

  positionRiskHelpTooltip(tooltip, targetElement, event);
  tooltip.classList.add("visible");
}

function positionRiskHelpTooltip(tooltip, targetElement, event) {
  const rect = targetElement.getBoundingClientRect();
  const ttWidth = 320;
  const viewportW = window.innerWidth;
  const viewportH = window.innerHeight;

  let left = rect.right + 12;
  if (left + ttWidth > viewportW - 20) {
    left = rect.left - ttWidth - 12;
  }
  if (left < 10) {
    left = Math.max(10, (viewportW - ttWidth) / 2);
  }

  let top = rect.top - 6;
  const ttHeight = tooltip.offsetHeight || 260;
  if (top + ttHeight > viewportH - 20) {
    top = viewportH - ttHeight - 20;
  }
  if (top < 10) {
    top = 10;
  }

  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

function hideRiskHelpTooltip() {
  if (activeTooltipElem) {
    activeTooltipElem.classList.remove("visible");
  }
}

function initRiskSettingsTooltips() {
  const elements = document.querySelectorAll("[data-risk-field], #form-risk-settings .control-group");
  
  elements.forEach(elem => {
    let fieldKey = elem.dataset.riskField;
    if (!fieldKey) {
      const inputOrSelect = elem.querySelector("input, select");
      if (inputOrSelect && inputOrSelect.id) {
        fieldKey = inputOrSelect.id;
        elem.dataset.riskField = fieldKey;
      }
    }
    if (!fieldKey) return;

    // Inject info icon next to label if not present
    const label = elem.querySelector("label");
    if (label && !label.querySelector(".risk-help-icon")) {
      const icon = document.createElement("i");
      icon.className = "fa-solid fa-circle-info risk-help-icon";
      icon.title = "Hover for contextual risk explanation";
      label.appendChild(icon);
    }

    const handleEnter = (e) => showRiskHelpTooltip(fieldKey, elem, e);
    const handleLeave = () => hideRiskHelpTooltip();

    elem.addEventListener("mouseenter", handleEnter);
    elem.addEventListener("mouseleave", handleLeave);

    const children = elem.querySelectorAll("label, input, select, .risk-help-icon, span, strong");
    children.forEach(child => {
      child.addEventListener("mouseenter", handleEnter);
      child.addEventListener("mouseleave", handleLeave);
    });
  });
}

// Extensible public API to add/register new risk setting help dynamically
window.registerRiskHelp = function(fieldKey, helpConfig) {
  RISK_HELP_DICTIONARY[fieldKey] = helpConfig;
  initRiskSettingsTooltips();
};

// ─────────────────────────────────────────────────────────────
//  CONTROLS & BINDINGS
// ─────────────────────────────────────────────────────────────
function initControls() {
  // Show/Hide Password Eye Icon Toggle
  el("btn-toggle-password-vis").addEventListener("click", () => {
    const field = el("mt5-input-password");
    const icon = el("icon-password-eye");
    if (field.type === "password") {
      field.type = "text";
      icon.className = "fa-solid fa-eye-slash";
    } else {
      field.type = "password";
      icon.className = "fa-solid fa-eye";
    }
  });

  // MT5 Manager Modal
  el("btn-open-mt5-manager").addEventListener("click", () => el("modal-mt5-manager").classList.remove("hidden"));
  el("btn-close-mt5-manager").addEventListener("click", () => el("modal-mt5-manager").classList.add("hidden"));

  // Step 2 Fetch Account Button (Validation only, popup stays open)
  el("btn-mt5-fetch").addEventListener("click", handleFetchAccountPreview);

  // Step 4 Connect Account Form Submit (Closes modal ONLY after success)
  el("form-mt5-connect").addEventListener("submit", handleConnectAccountSubmit);

  el("btn-mt5-disconnect").addEventListener("click", async () => {
    addAuditEntry("[MT5 DISCONNECT] Disconnecting account...", "var(--accent-amber)");
    await apiFetch("/api/v1/account/disconnect", "POST");
    updateConnectionIndicator("Disconnected");
  });

  // Refresh Account Button
  el("btn-refresh-account").addEventListener("click", refreshAccountData);

  // Modal Sub-Tabs (Connection, Saved Accounts, Login History)
  document.querySelectorAll(".modal-tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".modal-tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".modal-tab-content").forEach(c => c.classList.add("hidden"));
      btn.classList.add("active");
      const target = btn.dataset.modaltab;
      if (target) {
        el(target).classList.remove("hidden");
        if (target === "mt5-tab-saved") loadSavedAccounts();
        if (target === "mt5-tab-history") loadLoginHistory();
      }
    });
  });

  // Risk Settings Modal
  el("btn-open-settings").addEventListener("click", async () => {
    if (state.riskSettings) {
      updateRiskSettingsUI(state.riskSettings, true);
    }
    el("modal-settings").classList.remove("hidden");
    initRiskSettingsTooltips();
    const riskCfg = await apiFetch("/api/v1/settings/risk");
    if (riskCfg) updateRiskSettingsUI(riskCfg, true);
  });
  el("btn-close-settings").addEventListener("click", () => el("modal-settings").classList.add("hidden"));
  initModifySLTP();

  const getFloat = (id, defVal) => {
    const elem = el(id);
    if (!elem || elem.value === "" || elem.value === null || elem.value === undefined) return defVal;
    const v = parseFloat(elem.value);
    return isNaN(v) ? defVal : v;
  };

  const getInt = (id, defVal) => {
    const elem = el(id);
    if (!elem || elem.value === "" || elem.value === null || elem.value === undefined) return defVal;
    const v = parseInt(elem.value, 10);
    return isNaN(v) ? defVal : v;
  };

  el("form-risk-settings").addEventListener("submit", async (e) => {
    e.preventDefault();
    const prevCfg = state.riskSettings || {};
    const pick = (id) => el(id) ? el(id).value : null;

    // Blocked hours: "18, 19,20" -> [18, 19, 20]; invalid entries are dropped.
    const hoursRaw = (pick("cfg-blocked-hours") || "").split(",").map(x => x.trim()).filter(x => x !== "");
    const blockedHours = [...new Set(hoursRaw.map(Number).filter(h => Number.isInteger(h) && h >= 0 && h <= 23))].sort((x, y) => x - y);
    if (blockedHours.length !== hoursRaw.length) {
      alert("Blocked Hours: use whole numbers 0-23 separated by commas (e.g. 18,19,20).");
      return;
    }

    const strategy = pick("cfg-strategy-profile") || prevCfg.strategy_profile || "DONCHIAN_TREND";

    // Only the fields shown in the form are changed; everything else keeps its
    // saved value (the old form overwrote disabled_setups and strategy on every save).
    const payload = {
      ...prevCfg,
      strategy_profile: strategy,
      donchian_timeframe: pick("cfg-donchian-tf") || prevCfg.donchian_timeframe || "H1",
      entry_timeframe: strategy === "DONCHIAN_TREND" ? (pick("cfg-donchian-tf") || "H1") : (prevCfg.entry_timeframe || "M15"),
      lot_sizing_mode: pick("cfg-lot-mode") || "DYNAMIC_RISK",
      max_risk_per_trade_pct: getFloat("cfg-max-risk", 1.0),
      user_configured_lot: getFloat("cfg-user-lot", 0.01),
      max_allowed_lot: getFloat("cfg-max-allowed-lot", 0.05),
      daily_loss_limit_pct: getFloat("cfg-daily-loss", 3.0),
      max_daily_loss_amount: getFloat("cfg-max-daily-loss-amt", 0),
      weekly_loss_limit_pct: getFloat("cfg-weekly-loss", 7.0),
      max_consecutive_losses: getInt("cfg-max-consecutive-losses", 0),
      max_open_positions: getInt("cfg-max-positions", 2),
      max_positions_per_direction: getInt("cfg-max-per-direction", 1),
      min_minutes_between_entries: getFloat("cfg-min-entry-gap", 30),
      max_trades_per_day: getInt("cfg-max-daily-trades", 8),
      max_spread_pips: getFloat("cfg-max-spread", 3.0),
      blocked_hours: blockedHours,
      news_filter_enabled: pick("cfg-news-filter") !== "false",
      auto_trade_enabled: pick("cfg-auto-trade-enabled") === "true",
      enable_live_trading: pick("cfg-enable-live-trading") === "true"
    };

    if (payload.max_allowed_lot < payload.user_configured_lot) {
      alert("Maximum Lot must be at least the Fixed Lot Size.");
      return;
    }

    if (payload.weekly_loss_limit_pct <= 0) {
      addAuditEntry("[SETTINGS ERROR] Weekly Loss Limit must be greater than 0%.", "var(--accent-red)");
      alert("Weekly Loss Limit must be greater than 0%.");
      return;
    }

    state.riskSettings = payload;
    updateRiskSettingsUI(payload, true);

    addAuditEntry("[SETTINGS] Saving Risk Settings & Protection Engine configuration...", "var(--accent-cyan)");
    const res = await apiFetch("/api/v1/settings/risk", "POST", payload);
    if (res && res.success) {
      const savedCfg = res.settings || payload;
      updateRiskSettingsUI(savedCfg, true);
      addAuditEntry("[SETTINGS] Risk management settings saved successfully.", "var(--accent-emerald)");
      el("modal-settings").classList.add("hidden");
    } else {
      const why = (res && res.message) ? res.message : "Failed to save risk settings on backend. Check server log.";
      addAuditEntry(`[SETTINGS] ${why}`, "var(--accent-red)");
      if (res && res.settings) updateRiskSettingsUI(res.settings, true);  // show what is really active
      alert(why);
    }
  });

  const handleResetWeeklyLossLock = async () => {
    if (!confirm("Are you sure you want to reset the Weekly Loss Lock? This will clear the current weekly loss counter for testing and allow normal risk evaluation again.")) {
      return;
    }
    addAuditEntry("[RISK RESET] Triggering manual reset of Weekly Loss Lock...", "var(--accent-amber)");
    const res = await apiFetch("/api/v1/settings/risk/reset-weekly-loss-lock", "POST");
    if (res && res.success) {
      const msg = res.message || "WEEKLY LOSS LOCK RESET — NORMAL RISK CHECKS ACTIVE";
      addAuditEntry(`[RISK RESET] ${msg}`, "var(--accent-emerald)");
      showTradeAlert("success", "Weekly Loss Lock Reset", msg);
      if (res.account) updateAccount(res.account);
      await refreshAccountData();
    } else {
      const errMsg = res ? res.message : "Failed to reset Weekly Loss Lock";
      addAuditEntry(`[RISK RESET ERROR] ${errMsg}`, "var(--accent-red)");
      showTradeAlert("rejected", "Reset Failed", errMsg);
    }
  };

  if (el("btn-reset-weekly-loss")) el("btn-reset-weekly-loss").addEventListener("click", handleResetWeeklyLossLock);
  if (el("btn-sidebar-reset-weekly-loss")) el("btn-sidebar-reset-weekly-loss").addEventListener("click", handleResetWeeklyLossLock);

  // Bot Controller Buttons (if present)
  if (el("btn-bot-start")) el("btn-bot-start").addEventListener("click", () => sendBotCommand("start"));
  if (el("btn-bot-pause")) el("btn-bot-pause").addEventListener("click", () => sendBotCommand("pause"));
  if (el("btn-bot-resume")) el("btn-bot-resume").addEventListener("click", () => sendBotCommand("resume"));
  if (el("btn-bot-stop")) el("btn-bot-stop").addEventListener("click", () => sendBotCommand("stop"));
  if (el("btn-bot-emergency")) {
    el("btn-bot-emergency").addEventListener("click", () => {
      if (confirm("WARNING: Trigger EMERGENCY STOP? This will immediately close ALL positions and cancel pending orders!")) {
        sendBotCommand("emergency-stop");
      }
    });
  }

  // Manual Trading Buttons
  el("btn-manual-buy").addEventListener("click", () => executeManualTrade("BUY"));
  el("btn-manual-sell").addEventListener("click", () => executeManualTrade("SELL"));

  el("btn-lot-minus").addEventListener("click", () => {
    let l = Math.max(0.01, parseFloat(el("manual-lots").value) - 0.01);
    el("manual-lots").value = l.toFixed(2);
  });
  el("btn-lot-plus").addEventListener("click", () => {
    let l = parseFloat(el("manual-lots").value) + 0.01;
    el("manual-lots").value = l.toFixed(2);
  });

  el("manual-order-type").addEventListener("change", (e) => {
    const val = e.target.value;
    el("pending-price-group").style.display = val.includes("LIMIT") || val.includes("STOP") ? "block" : "none";
  });

  el("btn-close-all-pos").addEventListener("click", async () => {
    if (confirm("Close all active positions?")) {
      const res = await apiFetch("/api/v1/trade/positions/close-all", "POST");
      if (res) addAuditEntry(`[TRADE] ${res.message}`, "var(--accent-emerald)");
      await refreshAccountData();
      await refreshAccountData();
    }
  });

  // Manual SL/TP interaction logic to prevent auto-fill overrides
  const slEl = el("manual-sl-price");
  const tpEl = el("manual-tp-price");
  if (slEl) {
    slEl.addEventListener("input", () => {
      if (slEl.value.trim() === "") {
        slEl.dataset.autoPopulated = "true";
      } else {
        slEl.dataset.autoPopulated = "false";
      }
    });
  }
  if (tpEl) {
    tpEl.addEventListener("input", () => {
      if (tpEl.value.trim() === "") {
        tpEl.dataset.autoPopulated = "true";
      } else {
        tpEl.dataset.autoPopulated = "false";
      }
    });
  }

  // Symbol Selector
  el("select-symbol").addEventListener("change", (e) => {
    state.symbol = e.target.value;
    addAuditEntry(`[CONFIG] Symbol switched to ${state.symbol}`);
    resetSLTPInputs();
    connectWebSocket();
  });

  el("btn-apply-custom-symbol").addEventListener("click", () => {
    const custom = el("custom-symbol-input").value.trim().toUpperCase();
    if (custom) {
      state.symbol = custom;
      addAuditEntry(`[CONFIG] Custom Symbol applied: ${state.symbol}`);
      resetSLTPInputs();
      connectWebSocket();
    }
  });

  // Timeframe Selector Buttons
  document.querySelectorAll(".btn-tf").forEach(btn => {
    btn.addEventListener("click", () => {
      setTimeframe(btn.dataset.tf);
    });
  });

  // Action Buttons
  el("btn-evaluate-now").addEventListener("click", async () => {
    state.nextAnalysisCountdown = 3;
    await runMarketEvaluation(true);
  });

  el("btn-execute-now").addEventListener("click", async () => {
    if (!confirm(`Execute AI Signal for ${state.symbol} (${state.timeframe})?`)) return;
    addAuditEntry(`[TRADE] Executing AI Signal for ${state.symbol}...`, "var(--accent-cyan)");
    const res = await apiFetch(`/api/v1/trade/execute?symbol=${state.symbol}&timeframe=${state.timeframe}`, "POST");
    if (res) {
      if (res.account) updateAccount(res.account);
      if (res.positions) { state.positions = res.positions; renderPositionsTable(); }
      if (res.pending_orders) { state.pendingOrders = res.pending_orders; renderPendingOrdersTable(); }
      if (res.report) updateDecisionReport(res.report);
      
      const msg = res.message || "AI Signal execution completed";
      if (res.success) {
        addAuditEntry(`[TRADE] ${msg}`, "var(--accent-emerald)");
        showTradeAlert("success", "AI Signal Executed", msg);
      } else {
        addAuditEntry(`[TRADE] ${msg}`, "var(--accent-amber)");
        let inst = null;
        if (res.risk_reasons && res.risk_reasons.length > 0) {
          res.risk_reasons.forEach(reason => addAuditEntry(`  └─ [RISK REASON] ${reason}`, "var(--accent-red)"));
          inst = res.risk_reasons.join(" | ");
        }
        if (msg.includes("10027") || msg.includes("Algo Trading is DISABLED")) {
          inst = "Open MetaTrader 5 desktop software, click the 'Algo Trading' button in the top main toolbar (or press Ctrl+E) to allow automated/API orders, then try your trade again.";
        }
        showTradeAlert("rejected", "AI Execution Blocked", msg, inst);
      }
    }
    await refreshAccountData();
  });

  function switchBacktestTab(tabName) {
    const execBtn = el("btn-bt-tab-executed");
    const blockBtn = el("btn-bt-tab-blocked");
    const execView = el("bt-view-executed");
    const blockView = el("bt-view-blocked");
    
    if (tabName === "executed") {
      if (execBtn) execBtn.classList.add("active");
      if (blockBtn) blockBtn.classList.remove("active");
      if (execView) execView.classList.remove("hidden");
      if (blockView) blockView.classList.add("hidden");
    } else {
      if (execBtn) execBtn.classList.remove("active");
      if (blockBtn) blockBtn.classList.add("active");
      if (execView) execView.classList.add("hidden");
      if (blockView) blockView.classList.remove("hidden");
    }
  }
  window.switchBacktestTab = switchBacktestTab;

  function populateBacktestFormDefaults() {
    if (el("bt-cfg-symbol")) el("bt-cfg-symbol").value = state.symbol || "XAUUSD";
    if (el("bt-cfg-timeframe")) el("bt-cfg-timeframe").value = state.timeframe || "M15";
    
    if (state.riskSettings) {
      const cfg = state.riskSettings;
      if (el("bt-cfg-balance") && (!el("bt-cfg-balance").value || el("bt-cfg-balance").value == "10000")) {
        el("bt-cfg-balance").value = state.account ? (state.account.balance || 10000) : 10000;
      }
      if (el("bt-cfg-risk-pct")) el("bt-cfg-risk-pct").value = cfg.max_risk_per_trade_pct ?? 1.0;
      if (el("bt-cfg-lot-mode")) el("bt-cfg-lot-mode").value = cfg.lot_sizing_mode ?? "FIXED";
      if (el("bt-cfg-user-lot")) el("bt-cfg-user-lot").value = cfg.user_configured_lot ?? 0.10;
      if (el("bt-cfg-cooldown")) el("bt-cfg-cooldown").value = cfg.auto_trade_cooldown_seconds ?? 300;
      if (el("bt-cfg-daily-loss")) el("bt-cfg-daily-loss").value = cfg.daily_loss_limit_pct ?? 3.0;
      if (el("bt-cfg-max-consec")) el("bt-cfg-max-consec").value = cfg.max_consecutive_losses ?? 3;
      if (el("bt-cfg-max-spread")) el("bt-cfg-max-spread").value = cfg.max_spread_pips ?? 2.5;
    }
  }

  el("btn-run-backtest").addEventListener("click", async () => {
    populateBacktestFormDefaults();
    el("modal-backtest").classList.remove("hidden");
  });

  el("btn-close-backtest").addEventListener("click", () => el("modal-backtest").classList.add("hidden"));

  el("form-backtest-config").addEventListener("submit", async (e) => {
    e.preventDefault();
    
    const btn = el("btn-run-backtest-submit");
    const errBanner = el("bt-error-banner");
    const errMsg = el("bt-error-msg");
    const statusBanner = el("bt-status-banner");
    const statusMsg = el("bt-status-msg");

    if (errBanner) errBanner.classList.add("hidden");
    if (statusBanner) statusBanner.classList.add("hidden");

    const fromVal = el("bt-cfg-from-date") && el("bt-cfg-from-date").value ? el("bt-cfg-from-date").value : null;
    const toVal = el("bt-cfg-to-date") && el("bt-cfg-to-date").value ? el("bt-cfg-to-date").value : null;
    
    if (fromVal && toVal && new Date(fromVal) > new Date(toVal)) {
      if (errBanner && errMsg) {
        errMsg.textContent = "Invalid Date Range: From Date must be earlier than or equal to To Date.";
        errBanner.classList.remove("hidden");
      } else {
        alert("Invalid Date Range: From Date must be earlier than or equal to To Date.");
      }
      return;
    }

    const rawBal = el("bt-cfg-balance") ? el("bt-cfg-balance").value : "500";
    const initBal = (rawBal !== "" && !isNaN(rawBal)) ? parseFloat(rawBal) : 500.0;

    const payload = {
      symbol: (el("bt-cfg-symbol") ? el("bt-cfg-symbol").value.trim() : state.symbol) || "XAUUSD",
      timeframe: (el("bt-cfg-timeframe") ? el("bt-cfg-timeframe").value : state.timeframe) || "M15",
      from_date: fromVal,
      to_date: toVal,
      initial_balance: initBal,
      risk_pct: parseFloat(el("bt-cfg-risk-pct") ? el("bt-cfg-risk-pct").value : "1.0") || 1.0,
      lot_sizing_mode: el("bt-cfg-lot-mode") ? el("bt-cfg-lot-mode").value : "FIXED",
      user_configured_lot: parseFloat(el("bt-cfg-user-lot") ? el("bt-cfg-user-lot").value : "0.10") || 0.10,
      auto_trade_cooldown_seconds: parseInt(el("bt-cfg-cooldown") ? el("bt-cfg-cooldown").value : "300", 10) || 300,
      daily_loss_limit_pct: parseFloat(el("bt-cfg-daily-loss") ? el("bt-cfg-daily-loss").value : "3.0") || 3.0,
      max_consecutive_losses: parseInt(el("bt-cfg-max-consec") ? el("bt-cfg-max-consec").value : "3", 10) || 3,
      max_spread_pips: parseFloat(el("bt-cfg-max-spread") ? el("bt-cfg-max-spread").value : "2.5") || 2.5
    };

    if (btn) {
      btn.disabled = true;
      btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> RUNNING SIMULATION...`;
    }

    try {
      addAuditEntry(`[BACKTEST] Executing simulation engine for ${payload.symbol} (${payload.timeframe}) Balance=$${payload.initial_balance}...`, "var(--accent-purple)");
      const data = await apiFetch("/api/v1/backtest/run", "POST", payload);
      
      if (!data || data.error) {
        const errorText = data ? data.error : "Failed to run strategy backtest simulation.";
        if (errBanner && errMsg) {
          errMsg.textContent = errorText;
          errBanner.classList.remove("hidden");
        } else {
          alert(errorText);
        }
        return;
      }

      const curr = state.account ? state.account.currency : "USD";
      const digits = getDigits(payload.symbol);

      // 1. Performance Metrics
      el("bt-init-balance").textContent = "$" + fmt(data.initial_balance !== undefined ? data.initial_balance : payload.initial_balance);
      el("bt-final-balance").textContent = "$" + fmt(data.final_balance !== undefined ? data.final_balance : payload.initial_balance);
      const netProfit = data.net_profit_usd !== undefined ? data.net_profit_usd : 0;
      const profitSign = netProfit > 0 ? "+" : (netProfit < 0 ? "-" : "");
      el("bt-net-profit").textContent = profitSign + "$" + fmt(Math.abs(netProfit)) + " " + curr;

      const roiPct = data.net_roi_pct !== undefined ? data.net_roi_pct : 0;
      const roiSign = roiPct > 0 ? "+" : (roiPct < 0 ? "-" : "");
      el("bt-roi-pct").textContent = roiSign + fmt(Math.abs(roiPct), 1) + "%";
      el("bt-win-rate").textContent = fmt(data.win_rate_pct || 0, 1) + "%";
      el("bt-total-trades").textContent = data.total_trades !== undefined ? data.total_trades : 0;
      if (el("bt-winning-trades")) el("bt-winning-trades").textContent = data.winning_trades !== undefined ? data.winning_trades : 0;
      if (el("bt-losing-trades")) el("bt-losing-trades").textContent = data.losing_trades !== undefined ? data.losing_trades : 0;
      if (el("bt-breakeven-trades")) el("bt-breakeven-trades").textContent = data.break_even_trades !== undefined ? data.break_even_trades : 0;
      el("bt-profit-factor").textContent = fmt(data.profit_factor || 0, 2);
      el("bt-sharpe").textContent = typeof data.sharpe_ratio === "number" ? fmt(data.sharpe_ratio, 2) : "0.00";
      el("bt-sortino").textContent = typeof data.sortino_ratio === "number" ? fmt(data.sortino_ratio, 2) : "0.00";
      el("bt-max-dd").textContent = fmt(data.max_drawdown_pct || 0, 2) + "%";
      el("bt-total-trades").textContent = data.total_trades !== undefined ? data.total_trades : 0;
      el("bt-max-consec-losses").textContent = data.max_consecutive_losses !== undefined ? data.max_consecutive_losses : 0;

      // Status Banner Feedback
      if (statusBanner && statusMsg) {
        if (!data.total_trades || data.total_trades === 0) {
          statusBanner.style.borderColor = "rgba(168, 85, 247, 0.4)";
          statusBanner.style.background = "rgba(168, 85, 247, 0.12)";
          statusBanner.style.color = "#e9d5ff";
          statusMsg.innerHTML = `<i class="fa-solid fa-shield-halved" style="color:var(--accent-purple);margin-right:6px;"></i><strong>Simulation Completed:</strong> Evaluated historical range (${data.from_date || payload.from_date || "All"} to ${data.to_date || payload.to_date || "All"}). <strong>0 trades executed</strong> because market conditions during this period were in low-confidence consolidation (<72% confidence threshold) and toxic setups (BB Midline/BOS) are disabled to protect capital. <span style="display:block;margin-top:4px;color:var(--accent-cyan);"><i class="fa-solid fa-lightbulb"></i> <strong>Tip:</strong> Switch Timeframe to <strong>M5</strong> or widen the date range (e.g. from 09/01/2026) to see active execution setups!</span>`;
        } else {
          statusBanner.style.borderColor = "rgba(16, 185, 129, 0.4)";
          statusBanner.style.background = "rgba(16, 185, 129, 0.12)";
          statusBanner.style.color = "#a7f3d0";
          statusMsg.innerHTML = `<i class="fa-solid fa-circle-check" style="color:var(--accent-emerald);margin-right:6px;"></i><strong>Simulation Completed:</strong> Successfully executed <strong>${data.total_trades} trades</strong> across historical window with <strong>${fmt(data.win_rate_pct, 1)}% Win Rate</strong>! Net Profit: <strong>${profitSign}$${fmt(Math.abs(netProfit))} ${curr}</strong>.`;
        }
        statusBanner.classList.remove("hidden");
      }

      if (data.robustness_stress_test && el("bt-stress-pf")) {
        el("bt-stress-pf").textContent = fmt(data.robustness_stress_test.min_profit_factor || 0, 2);
      }

      // 2. Rejection Analytics Card
      el("bt-total-opps").textContent = data.total_opportunities !== undefined ? data.total_opportunities : ((data.total_trades || 0) + (data.blocked_count || 0));
      el("bt-executed-count").textContent = data.executed_count !== undefined ? data.executed_count : (data.total_trades || 0);
      el("bt-blocked-count").textContent = data.blocked_count !== undefined ? data.blocked_count : 0;

      const rej = data.rejection_breakdown || {};
      if (el("rej-cooldown-count")) el("rej-cooldown-count").textContent = rej.COOLDOWN || 0;
      if (el("rej-spread-count")) el("rej-spread-count").textContent = rej.SPREAD_TOO_WIDE || 0;
      if (el("rej-daily-loss-count")) el("rej-daily-loss-count").textContent = rej.DAILY_LOSS_LIMIT || 0;
      if (el("rej-consec-loss-count")) el("rej-consec-loss-count").textContent = rej.CONSECUTIVE_LOSS_LIMIT || 0;
      if (el("rej-low-conf-count")) el("rej-low-conf-count").textContent = rej.CONFIDENCE_TOO_LOW || 0;
      if (el("rej-rr-count")) el("rej-rr-count").textContent = rej.INSUFFICIENT_RISK_REWARD || 0;

      if (el("count-bt-executed")) el("count-bt-executed").textContent = data.total_trades || 0;
      if (el("count-bt-blocked")) el("count-bt-blocked").textContent = data.blocked_opportunities ? data.blocked_opportunities.length : 0;

      // 3. Equity Curve Canvas
      renderBacktestEquityCurve(data.equity_curve || [payload.initial_balance]);

      // 4. Executed Trades Table
      const tbodyExec = el("backtest-trades-table-body");
      if (tbodyExec) {
        if (!data.trades || data.trades.length === 0) {
          tbodyExec.innerHTML = '<tr><td colspan="10" class="text-center" style="padding:14px;color:var(--text-secondary);">No trades executed during historical simulation. All setup criteria evaluated cleanly.</td></tr>';
        } else {
          tbodyExec.innerHTML = data.trades.map(t => `
            <tr>
              <td>#${t.id}</td>
              <td style="color:${t.side === 'BUY' ? 'var(--accent-emerald)' : 'var(--accent-red)'};font-weight:700">${t.side}</td>
              <td>${t.entry_time}</td>
              <td>${t.exit_time}</td>
              <td>${fmt(t.entry_price, digits)}</td>
              <td>${fmt(t.exit_price, digits)}</td>
              <td>${fmt(t.lots, 2)}</td>
              <td><span class="${t.pnl_usd >= 0 ? 'badge-pnl-win' : 'badge-pnl-loss'}">${t.pnl_usd >= 0 ? '+' : ''}$${fmt(t.pnl_usd)} ${curr}</span></td>
              <td><span class="${t.pnl_pips >= 0 ? 'badge-pnl-win' : 'badge-pnl-loss'}">${t.pnl_pips >= 0 ? '+' : ''}${fmt(t.pnl_pips, 1)} pips</span></td>
              <td><span class="sub-text">${t.exit_reason}</span></td>
            </tr>
          `).join("");
        }
      }

      // 5. Blocked Opportunities Table
      const tbodyBlock = el("backtest-blocked-table-body");
      if (tbodyBlock) {
        if (!data.blocked_opportunities || data.blocked_opportunities.length === 0) {
          tbodyBlock.innerHTML = '<tr><td colspan="8" class="text-center" style="padding:14px;color:var(--text-secondary);">No blocked setup opportunities recorded. All valid setups met execution criteria.</td></tr>';
        } else {
          tbodyBlock.innerHTML = data.blocked_opportunities.map(b => `
            <tr>
              <td>${b.time}</td>
              <td><strong>${b.symbol}</strong></td>
              <td style="color:${b.signal === 'BUY' ? 'var(--accent-emerald)' : 'var(--accent-red)'};font-weight:700">${b.signal}</td>
              <td>${fmt(b.confidence, 1)}%</td>
              <td><span class="badge badge-warning">BLOCKED</span></td>
              <td><strong style="color: var(--accent-red); font-size: 11px;">${b.reason}</strong></td>
              <td><span class="sub-text">${b.details}</span></td>
              <td>${fmt(b.price, digits)}</td>
            </tr>
          `).join("");
        }
      }

      switchBacktestTab('executed');
    } catch (err) {
      console.error("Backtest execution error:", err);
      if (errBanner && errMsg) {
        errMsg.textContent = err.message || "An unexpected error occurred during backtest execution.";
        errBanner.classList.remove("hidden");
      }
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = `<i class="fa-solid fa-play"></i> RUN HISTORICAL SIMULATION ENGINE`;
      }
    }
  });

function renderBacktestEquityCurve(curve) {
  const canvas = el("backtestEquityCanvas");
  if (!canvas || !curve || curve.length === 0) return;
  const holder = canvas.parentElement;
  canvas.width = holder.clientWidth || 500;
  canvas.height = 140;

  const ctx = canvas.getContext("2d");
  const W = canvas.width;
  const H = canvas.height;

  ctx.clearRect(0, 0, W, H);

  const minV = Math.min(...curve);
  const maxV = Math.max(...curve);
  const range = maxV - minV || 1.0;

  const valToY = (v) => H - 15 - ((v - minV) / range) * (H - 30);
  const idxToX = (i) => 40 + (i / (curve.length - 1)) * (W - 55);

  ctx.strokeStyle = "rgba(255, 255, 255, 0.05)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = 15 + (i / 4) * (H - 30);
    ctx.beginPath();
    ctx.moveTo(40, y);
    ctx.lineTo(W - 10, y);
    ctx.stroke();

    const val = maxV - (i / 4) * range;
    ctx.fillStyle = "#64748b";
    ctx.font = "9px JetBrains Mono, monospace";
    ctx.fillText("$" + Math.round(val), 5, y + 3);
  }

  ctx.beginPath();
  curve.forEach((v, i) => {
    const x = idxToX(i);
    const y = valToY(v);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });

  const isProfit = curve[curve.length - 1] >= curve[0];
  ctx.strokeStyle = isProfit ? "#00e5a0" : "#ff4d6d";
  ctx.lineWidth = 2;
  ctx.stroke();
}

  // Chart Tool Toggles
  el("toggle-ema").addEventListener("click", () => { state.indicators.showEMA = !state.indicators.showEMA; el("toggle-ema").classList.toggle("active", state.indicators.showEMA); renderFinancialChart(); });
  el("toggle-vwap").addEventListener("click", () => { state.indicators.showVWAP = !state.indicators.showVWAP; el("toggle-vwap").classList.toggle("active", state.indicators.showVWAP); renderFinancialChart(); });
  el("toggle-bollinger").addEventListener("click", () => { state.indicators.showBollinger = !state.indicators.showBollinger; el("toggle-bollinger").classList.toggle("active", state.indicators.showBollinger); renderFinancialChart(); });
  el("toggle-ob").addEventListener("click", () => { state.indicators.showOB = !state.indicators.showOB; el("toggle-ob").classList.toggle("active", state.indicators.showOB); renderFinancialChart(); });
  el("toggle-rsi").addEventListener("click", () => { state.indicators.showRSI = !state.indicators.showRSI; el("toggle-rsi").classList.toggle("active", state.indicators.showRSI); renderFinancialChart(); });
  el("toggle-macd").addEventListener("click", () => { state.indicators.showMACD = !state.indicators.showMACD; el("toggle-macd").classList.toggle("active", state.indicators.showMACD); renderFinancialChart(); });

  // Zoom & Fullscreen Controls
  if (el("btn-zoom-in")) el("btn-zoom-in").addEventListener("click", () => { state.zoomCount = Math.max(15, state.zoomCount - 10); renderFinancialChart(); });
  if (el("btn-zoom-out")) el("btn-zoom-out").addEventListener("click", () => { state.zoomCount = Math.min(200, state.zoomCount + 10); renderFinancialChart(); });
  if (el("btn-zoom-reset")) el("btn-zoom-reset").addEventListener("click", () => { state.zoomCount = 60; state.panOffset = 0; renderFinancialChart(); });
  if (el("btn-fullscreen")) el("btn-fullscreen").addEventListener("click", () => {
    const holder = el("canvas-holder");
    if (!document.fullscreenElement) {
      if (holder.requestFullscreen) holder.requestFullscreen();
    } else {
      if (document.exitFullscreen) document.exitFullscreen();
    }
  });

  // Drawing Tools
  el("tool-crosshairs").addEventListener("click", () => { state.activeTool = "crosshairs"; updateDrawingToolsUI(); });
  el("tool-trendline").addEventListener("click", () => { state.activeTool = "trendline"; updateDrawingToolsUI(); });
  el("tool-hline").addEventListener("click", () => { state.activeTool = "hline"; updateDrawingToolsUI(); });
  el("tool-rectangle").addEventListener("click", () => { state.activeTool = "rectangle"; updateDrawingToolsUI(); });
  el("tool-fibo").addEventListener("click", () => { state.activeTool = "fibo"; updateDrawingToolsUI(); });
  el("tool-clear-drawings").addEventListener("click", () => { state.drawings = []; state.drawingInProgress = null; addAuditEntry("[DRAWING] Cleared all drawings"); renderFinancialChart(); });

  // Bottom Tabs
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach(c => c.classList.add("hidden"));
      btn.classList.add("active");
      const target = btn.dataset.tab;
      if (target) {
        el(target).classList.remove("hidden");
        if (target === "tab-ml-models") {
          loadMlModels();
        }
      }
    });
  });

  // Train Model Button
  if (el("btn-ml-train")) {
    el("btn-ml-train").addEventListener("click", async () => {
      const versionInput = el("ml-train-version");
      const version = versionInput ? versionInput.value.trim() : "";
      
      addAuditEntry(`[ML] Initiating chronological walk-forward training...`, "var(--accent-cyan)");
      el("ml-training-status").classList.remove("hidden");
      
      let path = "/api/v1/ml/train";
      if (version) {
        path += `?version=${version}`;
      }
      
      const res = await apiFetch(path, "POST");
      if (res && res.success) {
        addAuditEntry(`[ML] ${res.message}`, "var(--accent-emerald)");
        if (versionInput) versionInput.value = "";
        
        // Poll training status every 5 seconds
        const pollInterval = setInterval(async () => {
          const botStatus = await apiFetch("/api/v1/bot/status");
          if (botStatus && !botStatus.training_active) {
            clearInterval(pollInterval);
            el("ml-training-status").classList.add("hidden");
            addAuditEntry("[ML] Model training completed successfully! Updating models list...", "var(--accent-emerald)");
            await loadMlModels();
          }
        }, 5000);
      } else {
        el("ml-training-status").classList.add("hidden");
        addAuditEntry("[ML] Failed to initiate model training.", "var(--accent-red)");
      }
    });
  }
}

async function loadMlModels() {
  const models = await apiFetch("/api/v1/ml/models");
  const tbody = el("ml-models-table-body");
  if (!tbody) return;

  if (!models || models.length === 0) {
    tbody.innerHTML = '<tr><td colspan="9" class="text-center">No trained machine learning models found.</td></tr>';
    return;
  }

  tbody.innerHTML = models.map(m => {
    const isBest = m.is_active === true || (state.riskSettings && state.riskSettings.active_model_version === m.version);
    const f1 = m.test_metrics && m.test_metrics.xgboost ? m.test_metrics.xgboost.f1 : (m.test_metrics && m.test_metrics.lightgbm ? m.test_metrics.lightgbm.f1 : (m.test_metrics && m.test_metrics.f1 ? m.test_metrics.f1 : 0.0));
    const acc = m.test_metrics && m.test_metrics.xgboost ? m.test_metrics.xgboost.accuracy : (m.test_metrics && m.test_metrics.lightgbm ? m.test_metrics.lightgbm.accuracy : (m.test_metrics && m.test_metrics.accuracy ? m.test_metrics.accuracy : 0.0));
    
    return `<tr>
      <td><strong>${escHtml(m.version)}</strong></td>
      <td>${new Date(m.timestamp).toLocaleString("en-GB")}</td>
      <td><span class="sub-text">${escHtml(m.date_range)}</span></td>
      <td>${m.features_count}</td>
      <td><span class="badge-ai" style="background:#8b5cf6">${escHtml(m.model_type || "xgboost")}</span></td>
      <td><strong>${fmt(f1 * 100, 1)}%</strong></td>
      <td>${fmt(acc * 100, 1)}%</td>
      <td><span class="${isBest ? 'badge-status-pass' : 'badge-status-fail'}">${isBest ? 'ACTIVE' : 'INACTIVE'}</span></td>
      <td>
        <button class="btn-action-sm" onclick="activateMlModel('${escHtml(m.version)}')">${isBest ? 'Active' : 'Activate'}</button>
      </td>
    </tr>`;
  }).join("");
}

async function activateMlModel(version, force = false) {
  addAuditEntry(`[ML] Activating model version: ${version}${force ? ' (FORCE OVERRIDE)' : ''}...`, "var(--accent-cyan)");
  let url = `/api/v1/ml/models/activate?version=${version}`;
  if (force) url += "&force=true";
  const res = await apiFetch(url, "POST");
  if (res && res.success) {
    addAuditEntry(`[ML] ${res.message}`, "var(--accent-emerald)");
    
    // Request updated risk settings to sync active model version
    const riskCfg = await apiFetch("/api/v1/settings/risk");
    if (riskCfg) updateRiskSettingsUI(riskCfg);
    
    await loadMlModels();
  } else {
    addAuditEntry(`[ML] Failed to activate model version: ${version}`, "var(--accent-red)");
    if (!force) {
      const confirmForce = confirm(`Model Quality Gate Rejected '${version}'.\n\nIts performance metrics (F1 / Accuracy) are below the minimum acceptable quality threshold (F1 >= 45%, Accuracy >= 40%).\n\nDo you want to FORCE activate this model anyway?`);
      if (confirmForce) {
        await activateMlModel(version, true);
      }
    }
  }
}
window.activateMlModel = activateMlModel;


function updateDrawingToolsUI() {
  document.querySelectorAll(".btn-drawing-tool").forEach(btn => btn.classList.remove("active"));
  if (state.activeTool === "crosshairs") el("tool-crosshairs").classList.add("active");
  else if (state.activeTool === "trendline") el("tool-trendline").classList.add("active");
  else if (state.activeTool === "hline") el("tool-hline").classList.add("active");
  else if (state.activeTool === "rectangle") el("tool-rectangle").classList.add("active");
  else if (state.activeTool === "fibo") el("tool-fibo").classList.add("active");
}

// ─────────────────────────────────────────────────────────────
//  AUTO-REFRESHING MARKET EVALUATION ENGINE (3-SECOND SCHEDULER)
// ─────────────────────────────────────────────────────────────
async function runMarketEvaluation(isManual = false) {
  // Concurrency Lock: Prevent overlapping evaluation requests
  if (state.isAnalysisRunning) {
    if (isManual) {
      addAuditEntry("[ANALYSIS] Evaluation already in progress. Skipping duplicate request.", "var(--accent-amber)");
    }
    return;
  }

  state.isAnalysisRunning = true;

  if (isManual) {
    addAuditEntry("[ANALYSIS] Running market evaluation pipeline...", "var(--accent-cyan)");
  }

  try {
    const report = await apiFetch(`/api/v1/trade/evaluate?symbol=${state.symbol}&timeframe=${state.timeframe}`);
    if (report) {
      updateDecisionReport(report);

      if (isManual) {
        addAuditEntry(`[ANALYSIS] Market evaluation updated — Decision: ${report.decision} | Confidence: ${(report.confidence * 100).toFixed(1)}% | Score: ${(report.confirmation_score * 100).toFixed(1)}%`, report.decision !== "HOLD" ? "var(--accent-emerald)" : "var(--accent-amber)");
        if (report.reasoning && report.reasoning.length > 0) {
          report.reasoning.forEach(r => addAuditEntry(`  ├─ ${r}`, "var(--text-secondary)"));
        }
        if (report.rejection_reasons && report.rejection_reasons.length > 0) {
          report.rejection_reasons.forEach(rr => addAuditEntry(`  └─ [REJECTION] ${rr}`, "var(--accent-red)"));
        }
      }
    }
  } catch (err) {
    console.error("Market evaluation auto-refresh error:", err);
    if (isManual) {
      addAuditEntry("[ANALYSIS] Error executing market evaluation pipeline", "var(--accent-red)");
    }
  } finally {
    state.isAnalysisRunning = false;
  }
}

function startAutoAnalysisScheduler() {
  if (state.autoAnalysisInterval) {
    clearInterval(state.autoAnalysisInterval);
  }

  // Run initial evaluation automatically on load
  runMarketEvaluation(false);

  // 3-second background refresh interval
  state.autoAnalysisInterval = setInterval(async () => {
    if (!state.isAnalysisRunning) {
      await runMarketEvaluation(false);
    }
  }, 3000);
}

async function handleResetTestingRiskBaseline() {
  if (!confirm("Are you sure you want to reset the testing session risk baseline?\n\nThis will clear current circuit breaker locks, reset daily PnL counter and peak equity for testing, without altering any configured risk limits.")) {
    return;
  }
  try {
    addAuditEntry("[RISK RESET] Executing safe testing session risk baseline reset...", "var(--accent-cyan)");
    const res = await apiFetch("/api/v1/settings/risk/reset-testing-baseline", "POST");
    if (res && res.success) {
      addAuditEntry(`[RISK RESET] ${res.message}`, "var(--accent-emerald)");
      showTradeAlert("success", "Testing Risk Baseline Reset", res.message);
      if (res.account) updateAccount(res.account);
    } else {
      const msg = (res && res.detail) ? res.detail : (res && res.message) ? res.message : "Failed to reset testing risk baseline.";
      addAuditEntry(`[RISK RESET] ${msg}`, "var(--accent-red)");
      showTradeAlert("rejected", "Reset Blocked", msg);
    }
  } catch (err) {
    console.error("Reset testing risk baseline error:", err);
    addAuditEntry("[RISK RESET] Error executing testing risk baseline reset.", "var(--accent-red)");
  }
}
window.handleResetTestingRiskBaseline = handleResetTestingRiskBaseline;

// ─────────────────────────────────────────────────────────────
//  BOOTSTRAP INITIALIZATION
// ─────────────────────────────────────────────────────────────
async function bootstrap() {
  addAuditEntry("[SYSTEM] ApexTrader AI Production MT5 Terminal Bootstrapping...");

  const health = await apiFetch("/api/v1/health");
  if (health) {
    updateConnectionIndicator(health.is_connected ? "Connected" : "Disconnected");
    updateBotStatusUI(health.bot_status);
    addAuditEntry(`[SYSTEM] Backend online — ${health.project} v${health.version}`, "var(--accent-emerald)");
  } else {
    updateConnectionIndicator("Disconnected");
    addAuditEntry("[SYSTEM] Backend unreachable. Check server status.", "var(--accent-red)");
  }

  const riskCfg = await apiFetch("/api/v1/settings/risk");
  if (riskCfg) updateRiskSettingsUI(riskCfg);

  initControls();
  initRiskSettingsTooltips();
  initPktScheduleModal();

  if (el("btn-reset-testing-risk-baseline")) {
    el("btn-reset-testing-risk-baseline").addEventListener("click", handleResetTestingRiskBaseline);
  }
  if (el("btn-sidebar-reset-testing-risk-baseline")) {
    el("btn-sidebar-reset-testing-risk-baseline").addEventListener("click", handleResetTestingRiskBaseline);
  }
  initCanvasInteractions();

  const holder = el("canvas-holder");
  if (holder) {
    new ResizeObserver(() => renderFinancialChart()).observe(holder);
  }

  // Initial timeframe synchronization (M15 default)
  setTimeframe(state.timeframe);

  async function syncDashboardData() {
    try {
      const botStatus = await apiFetch("/api/v1/bot/status");
      if (botStatus) {
        updateBotStatusUI(botStatus.status, botStatus.engine_state, botStatus.engine_reason, botStatus.auto_trade_live_reason);
      }

      const acc = await apiFetch("/api/v1/account");
      if (acc) updateAccount(acc);
      
      const ticker = await apiFetch(`/api/v1/market/ticker?symbol=${state.symbol}`);
      if (ticker) updateTicker(ticker);

      const candles = await apiFetch(`/api/v1/market/candles?symbol=${state.symbol}&timeframe=${state.timeframe}&count=80`);
      if (candles && candles.length > 0 && mergeChartCandles(candles)) {
        renderFinancialChart();
      }

      const pos = await apiFetch("/api/v1/trade/positions");
      if (pos) { state.positions = pos; renderPositionsTable(); }

      const pnd = await apiFetch("/api/v1/trade/pending");
      if (pnd) { state.pendingOrders = pnd; renderPendingOrdersTable(); }

      const histRes = await apiFetch(`/api/v1/trade/history?today_only=true&page=${state.historyPage}&page_size=${state.historyPageSize}`);
      if (histRes) {
        if (Array.isArray(histRes)) {
          state.history = histRes;
          state.historyTotal = histRes.length;
        } else {
          state.history = histRes.trades || [];
          state.historyTotal = histRes.total !== undefined ? histRes.total : (histRes.trades ? histRes.trades.length : 0);
        }
        renderTradeHistoryTable();
      }
    } catch (e) {
      console.error("Dashboard sync error:", e);
    }
  }

  // Fast fallback polling loop when WebSockets are disconnected
  setInterval(async () => {
    if (!state.wsConnected) {
      await syncDashboardData();
    }
  }, 1500);

  // Periodic background check even when WebSocket is connected to prevent stale UI states
  setInterval(async () => {
    if (state.wsConnected) {
      await syncDashboardData();
    }
  }, 4000);

  startAutoAnalysisScheduler();

  refreshDonchianPanel();
  setInterval(refreshDonchianPanel, 5000);

  const popupCloseBtn = el("popup-close-btn");
  if (popupCloseBtn) {
    popupCloseBtn.addEventListener("click", hideAutoTradeHoldPopup);
  }

  addAuditEntry("[SYSTEM] Initialization complete. Live MT5 synchronization engine active.", "var(--accent-emerald)");
}

// ─────────────────────────────────────────────────────────────
//  AUTO-TRADE HELD / REJECTED 4-SECOND POPUP MANAGER
// ─────────────────────────────────────────────────────────────
let autoTradePopupTimer = null;
let lastAutoTradeEventId = "";

function showAutoTradeHoldPopup(signal, reason, durationMs = 4000) {
  const container = el("auto-trade-popup-container");
  const card = el("auto-trade-popup-card");
  const badge = el("popup-signal-badge");
  const reasonText = el("popup-reason-text");
  const progressBar = el("popup-progress-bar");

  if (!container || !card || !reasonText || !progressBar) return;

  if (autoTradePopupTimer) {
    clearTimeout(autoTradePopupTimer);
    autoTradePopupTimer = null;
  }

  const sigUpper = (signal || "SIGNAL").toUpperCase();
  if (badge) {
    badge.textContent = `${sigUpper} SIGNAL`;
    if (sigUpper === "BUY") {
      badge.className = "popup-signal-badge badge-buy";
    } else if (sigUpper === "SELL") {
      badge.className = "popup-signal-badge badge-sell";
    } else {
      badge.className = "popup-signal-badge";
    }
  }

  const titleEl = document.querySelector("#auto-trade-popup-card .popup-title");
  if (titleEl) {
    if (reason && (reason.includes("Circuit Breaker") || reason.includes("Daily Loss"))) {
      titleEl.textContent = "CIRCUIT BREAKER TRIGGERED";
    } else if (reason && (reason.includes("Next-candle") || reason.includes("confirmation") || reason.includes("delay") || reason.includes("Trade held"))) {
      titleEl.textContent = "ENTRY CONFIRMATION HELD";
    } else {
      titleEl.textContent = "AUTO-TRADE EXECUTION BLOCKED";
    }
  }

  reasonText.textContent = reason || "Trade cannot be executed automatically due to backend limitations or risk controls.";

  card.classList.remove("popup-hide");
  container.classList.remove("hidden");

  progressBar.classList.remove("animate-4s");
  void progressBar.offsetWidth;
  progressBar.classList.add("animate-4s");

  addAuditEntry(`[AUTO-TRADE POPUP] (${sigUpper} Signal Held/Rejected) ${reason}`, "var(--accent-amber)");

  autoTradePopupTimer = setTimeout(() => {
    hideAutoTradeHoldPopup();
  }, durationMs);
}

function hideAutoTradeHoldPopup() {
  const container = el("auto-trade-popup-container");
  const card = el("auto-trade-popup-card");

  if (autoTradePopupTimer) {
    clearTimeout(autoTradePopupTimer);
    autoTradePopupTimer = null;
  }

  if (card) {
    card.classList.add("popup-hide");
    setTimeout(() => {
      if (container) container.classList.add("hidden");
      card.classList.remove("popup-hide");
    }, 300);
  } else if (container) {
    container.classList.add("hidden");
  }
}

// ─────────────────────────────────────────────────────────────
//  PAKISTAN STANDARD TIME (PKT) TIMETABLE MODAL WIDGET
// ─────────────────────────────────────────────────────────────
function initPktScheduleModal() {
  const btnOpen = el("btn-open-pkt-schedule");
  const btnClose = el("btn-close-pkt-schedule");
  const modal = el("modal-pkt-schedule");

  if (btnOpen && modal) {
    btnOpen.addEventListener("click", () => {
      modal.classList.remove("hidden");
      updatePKTClockAndHighlight();
    });
  }

  if (btnClose && modal) {
    btnClose.addEventListener("click", () => {
      modal.classList.add("hidden");
    });
  }

  if (modal) {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) {
        modal.classList.add("hidden");
      }
    });
  }

  // Update clock & active session highlight every 1 second
  setInterval(updatePKTClockAndHighlight, 1000);
  updatePKTClockAndHighlight();
}

function updatePKTClockAndHighlight() {
  const now = new Date();
  const utc = now.getTime() + (now.getTimezoneOffset() * 60000);
  const pktTime = new Date(utc + (3600000 * 5));

  const hrs = String(pktTime.getHours()).padStart(2, '0');
  const mins = String(pktTime.getMinutes()).padStart(2, '0');
  const secs = String(pktTime.getSeconds()).padStart(2, '0');

  const clockEl = el("live-pkt-clock");
  if (clockEl) clockEl.textContent = `PKT: ${hrs}:${mins}:${secs}`;

  const modal = el("modal-pkt-schedule");
  if (!modal || modal.classList.contains("hidden")) return;

  const hour = pktTime.getHours();

  const cardGolden = el("pkt-card-golden");
  const cardLondon = el("pkt-card-london");
  const cardNY = el("pkt-card-ny");
  const cardAsian = el("pkt-card-asian");

  const titleEl = el("pkt-active-session-title");
  const badgeEl = el("pkt-active-session-badge");
  const bannerEl = el("pkt-current-session-banner");

  [cardGolden, cardLondon, cardNY, cardAsian].forEach(c => {
    if (c) {
      c.style.transform = "scale(1)";
      c.style.boxShadow = "none";
      c.style.opacity = "0.75";
    }
  });

  if (hour >= 17 && hour < 21) {
    // 5:00 PM - 9:00 PM PKT: GOLDEN WINDOW
    if (cardGolden) {
      cardGolden.style.transform = "scale(1.02)";
      cardGolden.style.boxShadow = "0 0 20px rgba(245, 158, 11, 0.4)";
      cardGolden.style.opacity = "1";
    }
    if (titleEl) titleEl.textContent = "🔥 GOLDEN WINDOW ACTIVE (London & NY Overlap)";
    if (badgeEl) { badgeEl.textContent = "OPTIMAL SCALPING NOW"; badgeEl.style.background = "#f59e0b"; badgeEl.style.color = "#0f172a"; }
    if (bannerEl) bannerEl.style.borderColor = "#f59e0b";
  } else if (hour >= 12 && hour < 17) {
    // 12:00 PM - 5:00 PM PKT: LONDON SESSION
    if (cardLondon) {
      cardLondon.style.transform = "scale(1.02)";
      cardLondon.style.boxShadow = "0 0 20px rgba(16, 185, 129, 0.4)";
      cardLondon.style.opacity = "1";
    }
    if (titleEl) titleEl.textContent = "🟢 LONDON SESSION ACTIVE (High Volume)";
    if (badgeEl) { badgeEl.textContent = "HIGH LIQUIDITY"; badgeEl.style.background = "#10b981"; badgeEl.style.color = "#0f172a"; }
    if (bannerEl) bannerEl.style.borderColor = "#10b981";
  } else if (hour >= 21 && hour < 23) {
    // 9:00 PM - 11:00 PM PKT: EARLY NEW YORK
    if (cardNY) {
      cardNY.style.transform = "scale(1.02)";
      cardNY.style.boxShadow = "0 0 20px rgba(56, 189, 248, 0.4)";
      cardNY.style.opacity = "1";
    }
    if (titleEl) titleEl.textContent = "🟡 EARLY NEW YORK ACTIVE (Good Volume)";
    if (badgeEl) { badgeEl.textContent = "GOOD VOLUME"; badgeEl.style.background = "#38bdf8"; badgeEl.style.color = "#0f172a"; }
    if (bannerEl) bannerEl.style.borderColor = "#38bdf8";
  } else {
    // ASIAN / NIGHT
    if (cardAsian) {
      cardAsian.style.transform = "scale(1.02)";
      cardAsian.style.boxShadow = "0 0 20px rgba(239, 68, 68, 0.4)";
      cardAsian.style.opacity = "1";
    }
    if (titleEl) titleEl.textContent = "🔴 ASIAN & NIGHT (Low Volatility / Spreads Widen)";
    if (badgeEl) { badgeEl.textContent = "AVOID M1 SCALPING"; badgeEl.style.background = "#ef4444"; badgeEl.style.color = "#ffffff"; }
    if (bannerEl) bannerEl.style.borderColor = "#ef4444";
  }
}

// ─────────────────────────────────────────────────────────────
//  DONCHIAN TREND STRATEGY PANEL
// ─────────────────────────────────────────────────────────────
async function refreshDonchianPanel() {
  if (!el("dt-panel")) return;
  let d = null;
  try {
    const res = await fetch(API_BASE + `/api/v1/strategy/donchian/status?symbol=${encodeURIComponent(state.symbol || "XAUUSD")}`,
      { headers: { "X-API-Key": API_KEY } });
    if (res.ok) d = await res.json();
  } catch (e) { /* backend offline: keep last values */ }
  if (!d) return;
  renderDonchianPanel(d);
}

function renderDonchianPanel(d) {
  const cur = d.currency || "USD";
  const money = (v) => (v === null || v === undefined) ? "--" : `${v < 0 ? "-" : ""}$${fmt(Math.abs(v), 2)}`;
  const sig = el("dt-signal");
  const chip = el("dt-strategy-chip");

  if (chip) {
    chip.textContent = d.active ? "DONCHIAN TREND · ACTIVE" : "NOT ACTIVE (Legacy selected)";
    chip.className = "dt-chip" + (d.active ? "" : " dt-signal-off");
  }
  if (!d.ok) {
    if (sig) { sig.textContent = "NO DATA"; sig.className = "dt-signal dt-signal-off"; }
    if (el("dt-signal-text")) el("dt-signal-text").textContent = d.message || "Waiting for MT5 data...";
    return;
  }

  const trendEl = el("dt-trend");
  if (trendEl) {
    const t = d.daily_trend || "UNKNOWN";
    const allowed = t === "UP" ? "only BUY breakouts are taken" : t === "DOWN" ? "only SELL breakouts are taken" : "no trades until the daily trend is known";
    trendEl.innerHTML = `Daily trend: <strong class="${t === "UP" ? "up" : t === "DOWN" ? "down" : ""}">${t}</strong> &rarr; ${allowed}`;
    trendEl.title = d.daily_trend_text || "";
    // dim the side that the filter blocks
    const hi = document.querySelector(".dt-level-high"), lo = document.querySelector(".dt-level-low");
    if (hi) hi.style.opacity = t === "DOWN" ? 0.4 : 1;
    if (lo) lo.style.opacity = t === "UP" ? 0.4 : 1;
  }
  const newsEl = el("dt-news");
  if (newsEl && d.news) {
    const n = d.news;
    let txt;
    if (!n.enabled) txt = `News filter: <strong>OFF</strong>`;
    else if (n.blocked) txt = `News: <strong class="down">BLOCKED</strong> &rarr; ${escHtml(n.message)}`;
    else if (n.next) {
      const m = n.next.minutes_from_now;
      const when = m >= 120 ? `in ${Math.round(m / 60)} h` : m >= 0 ? `in ${m} min` : `${-m} min ago`;
      txt = `Next big USD news: <strong>${escHtml(n.next.name)}</strong> ${when} (trades paused 30 min before/after)`;
    } else txt = n.status === "LIVE" && !/unavailable/i.test(n.message || "") ? "News: no high-impact USD news coming up" : "News: calendar unavailable (not blocking)";
    newsEl.innerHTML = txt;
  }
  if (el("dt-sub")) el("dt-sub").textContent = `${d.timeframe || "H1"} · ${d.channel_bars || 100} candles`;
  el("dt-high").textContent = fmt(d.channel_high, 2);
  el("dt-low").textContent = fmt(d.channel_low, 2);
  el("dt-to-buy").textContent = d.to_buy > 0 ? `+$${fmt(d.to_buy, 2)} away` : "BROKEN";
  el("dt-to-sell").textContent = d.to_sell > 0 ? `-$${fmt(d.to_sell, 2)} away` : "BROKEN";
  const pct = Math.max(0, Math.min(100, d.position_pct));
  el("dt-bar-fill").style.width = pct + "%";
  const mk = el("dt-bar-marker");
  mk.style.left = pct + "%";
  // keep the label inside the bar near the edges
  mk.style.transform = pct < 12 ? "translate(0, -50%)" : pct > 88 ? "translate(-100%, -50%)" : "translate(-50%, -50%)";
  el("dt-close").textContent = fmt(d.last_close, 2);

  if (sig) {
    if (d.signal === "BUY") { sig.textContent = "BUY BREAKOUT"; sig.className = "dt-signal dt-signal-buy"; }
    else if (d.signal === "SELL") { sig.textContent = "SELL BREAKOUT"; sig.className = "dt-signal dt-signal-sell"; }
    else { sig.textContent = "NO BREAKOUT"; sig.className = "dt-signal dt-signal-none"; }
  }
  const cndl = d.last_candle_server || "--";
  const closedNote = d.market_open === false ? " Market is closed right now (no new prices), so nothing updates until it reopens." : "";
  el("dt-signal-text").textContent = (d.signal === "NONE"
    ? `Last ${d.timeframe || "H1"} candle ${cndl} · next check ~${d.next_check_server || "--"} (MT5 time)`
    : `${d.signal} signal on the ${cndl} candle (MT5 server time). The bot enters if all risk checks pass.`) + closedNote;
  if (sig && d.market_open === false && d.signal === "NONE") { sig.textContent = "MARKET CLOSED"; sig.className = "dt-signal dt-signal-off"; }

  if (el("dt-atr-label")) el("dt-atr-label").textContent = `ATR (14, ${d.timeframe || "H1"})`;
  el("dt-atr").textContent = `$${fmt(d.atr, 2)}`;
  el("dt-stop").textContent = `$${fmt(d.stop_distance, 2)}`;
  el("dt-trail").textContent = `$${fmt(d.trail_distance, 2)}`;
  el("dt-lot").textContent = fmt(d.next_lot, 2);
  el("dt-lot-note").textContent = d.lot_capped ? "capped by Max Lot setting" : "from Risk % setting";
  el("dt-risk").textContent = `${money(d.next_risk_usd)} ${cur}` + (d.next_risk_pct !== null ? ` (${fmt(d.next_risk_pct, 2)}% of equity)` : "");

  const box = el("dt-positions");
  const pos = d.positions || [];
  el("dt-pos-count").textContent = `${pos.length} OPEN`;
  if (!pos.length) {
    box.innerHTML = `<p class="dt-note">No open trades. The bot waits for a breakout, then manages the stop automatically.</p>`;
    return;
  }
  box.innerHTML = pos.map(p => {
    const buy = p.side === "BUY";
    const pnlColor = (p.pnl_usd || 0) >= 0 ? "var(--accent-emerald)" : "var(--accent-red)";
    const locked = p.locked_usd === null ? "no stop" : (p.locked_usd >= 0 ? `locks ${money(p.locked_usd)} profit` : `risk ${money(-p.locked_usd)}`);
    const moves = buy ? p.next_trail > p.sl : (p.sl === 0 || p.next_trail < p.sl);
    return `<div class="dt-pos">
      <span class="side ${buy ? "buy" : "sell"}">${p.side}</span>
      <span class="meta">#${escHtml(String(p.ticket))} · ${fmt(p.lots, 2)} lot @ ${fmt(p.entry, 2)}</span>
      <span class="pnl" style="color:${pnlColor}">${money(p.pnl_usd)}</span>
      <span class="sub">Stop ${fmt(p.sl, 2)} (${locked}) · next trail ${fmt(p.next_trail, 2)}${moves ? " ↑ will move" : ""}</span>
    </div>`;
  }).join("");
}

document.addEventListener("DOMContentLoaded", bootstrap);

