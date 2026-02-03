from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import base64
import json
import os
import time
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

app = FastAPI(title="Analytics Dashboard")

# Allow browser calls from your frontend (set CORS_ORIGINS in Render)
cors_origins = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in cors_origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= GOOGLE SHEETS =================
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

def load_credentials():
    raw_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    raw_b64 = os.getenv("GOOGLE_SERVICE_ACCOUNT_B64")

    if raw_json:
        info = json.loads(raw_json)
        return Credentials.from_service_account_info(info, scopes=SCOPES)

    if raw_b64:
        decoded = base64.b64decode(raw_b64).decode("utf-8")
        info = json.loads(decoded)
        return Credentials.from_service_account_info(info, scopes=SCOPES)

    # Local fallback
    return Credentials.from_service_account_file("creds.json", scopes=SCOPES)

creds = load_credentials()
client = gspread.authorize(creds)

SHEET_ID = "19Obv4OQS9lxFatwrm4AhRJ1ycXzKFgFOkGA2BPzSkCA"
workbook = client.open_by_key(SHEET_ID)
users_master_sheet = workbook.worksheet("USERS_MASTER")
subscriptions_sheet = workbook.worksheet("SUBSCRIPTIONS")

# ================= DATA =================
def _parse_timestamp(df: pd.DataFrame) -> pd.DataFrame:
    if "Timestamp" in df.columns:
        df["Timestamp"] = pd.to_datetime(df["Timestamp"])
    return df

def _df_to_records(df: pd.DataFrame):
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%dT%H:%M:%S")
    return df.where(pd.notnull(df), None).to_dict(orient="records")

def load_users_master():
    df = pd.DataFrame(users_master_sheet.get_all_records())
    return _parse_timestamp(df)

def load_subscriptions():
    df = pd.DataFrame(subscriptions_sheet.get_all_records())
    return _parse_timestamp(df)

# ================= SIMPLE IN-MEMORY CACHE =================
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "600"))
_cache = {
    "users_master": {"ts": 0, "data": None},
    "subscriptions": {"ts": 0, "data": None},
}

def _get_cached(key, loader):
    now = time.time()
    entry = _cache[key]
    if entry["data"] is None or (now - entry["ts"]) > CACHE_TTL_SECONDS:
        entry["data"] = loader()
        entry["ts"] = now
    return entry["data"]

def get_users_master():
    return _get_cached("users_master", load_users_master)

def get_subscriptions():
    return _get_cached("subscriptions", load_subscriptions)

# ================= ROOT DASHBOARD =================
@app.get("/", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML

# ================= USER DASHBOARD =================
@app.get("/users", response_class=HTMLResponse)
def users_page():
    return USER_HTML

# ================= API =================
@app.get("/summary")
def summary():
    df = get_users_master()
    total = df.TelegramUserID.nunique()
    completed = df[df.RegistrationStatus == "Completed"].TelegramUserID.nunique()
    verified = df[df.EmailVerified == "Yes"].TelegramUserID.nunique()
    return {
        "total": total,
        "completed": completed,
        "verified": verified,
        "conversion": round(completed / total * 100, 2)
    }

@app.get("/plans")
def plans():
    df = get_users_master()
    return (
        df[df.RegistrationStatus == "Completed"]
        .groupby("InvestmentPlanSelected").TelegramUserID.nunique()
        .sort_values(ascending=False)
        .to_dict()
    )

@app.get("/risks")
def risks():
    df = get_users_master()
    return (
        df[df.RegistrationStatus == "Completed"]
        .groupby("RiskOptionSelected").TelegramUserID.nunique()
        .sort_values(ascending=False)
        .to_dict()
    )

@app.get("/users-list")
def users_list():
    df = get_users_master()
    return df.TelegramUsername.dropna().unique().tolist()

@app.get("/user/{username}")
def user_data(username: str):
    df = get_users_master()
    u = df[df.TelegramUsername == username]
    last = u.sort_values("Timestamp").iloc[-1]
    return {
        "profile": {
            "name": last.FullName,
            "email": last.Email,
            "status": last.RegistrationStatus,
            "plan": last.InvestmentPlanSelected,
            "risk": last.RiskOptionSelected
        },
        "timeline": u.groupby(u.Timestamp.dt.date).size().to_dict()
    }

@app.get("/users-master")
def users_master():
    df = get_users_master()
    return _df_to_records(df)

@app.get("/subscriptions")
def subscriptions():
    df = get_subscriptions()
    return _df_to_records(df)

@app.get("/data")
def all_data():
    return {
        "users_master": _df_to_records(get_users_master()),
        "subscriptions": _df_to_records(get_subscriptions()),
    }

# ================= HTML =================

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Analytics Command Center</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>

<style>
:root {
  --ink: #0b1220;
  --navy: #0f172a;
  --slate: #1f2937;
  --muted: #667085;
  --cream: #f7f3ea;
  --sand: #efe6d8;
  --mint: #10b981;
  --sky: #38bdf8;
  --coral: #f97316;
  --rose: #e11d48;
  --violet: #6366f1;
  --card: rgba(255, 255, 255, 0.9);
  --stroke: rgba(15, 23, 42, 0.08);
  --shadow: 0 20px 60px rgba(2, 8, 23, 0.12);
}

* { box-sizing: border-box; }

body {
  margin: 0;
  font-family: "Space Grotesk", system-ui, sans-serif;
  background: radial-gradient(1200px 800px at 10% -10%, #fff6e7, transparent),
              radial-gradient(900px 700px at 90% 0%, #e8f7ff, transparent),
              linear-gradient(160deg, #f8f4ed 0%, #eef2ff 40%, #f4f7f2 100%);
  color: var(--ink);
}

.page {
  max-width: 1400px;
  margin: 0 auto;
  padding: 32px 28px 60px;
}

.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  background: var(--card);
  border: 1px solid var(--stroke);
  border-radius: 18px;
  box-shadow: var(--shadow);
  backdrop-filter: blur(12px);
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.logo {
  width: 42px;
  height: 42px;
  border-radius: 12px;
  background: conic-gradient(from 200deg, var(--violet), var(--sky), var(--mint), var(--coral), var(--violet));
  box-shadow: inset 0 0 0 2px rgba(255,255,255,0.7);
}

.brand h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 0.3px;
}

.nav {
  display: flex;
  gap: 14px;
}

.nav a {
  text-decoration: none;
  color: var(--navy);
  font-weight: 600;
  padding: 8px 14px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.05);
}

.nav a:hover {
  background: rgba(15, 23, 42, 0.1);
}

.hero {
  margin-top: 26px;
  display: grid;
  grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr);
  gap: 24px;
}

.hero-card {
  background: var(--card);
  border: 1px solid var(--stroke);
  border-radius: 24px;
  padding: 26px;
  box-shadow: var(--shadow);
}

.hero h2 {
  margin: 0 0 10px;
  font-size: 32px;
  font-weight: 700;
}

.hero p {
  margin: 0;
  color: var(--muted);
  font-size: 15px;
  line-height: 1.6;
}

.hero-actions {
  display: flex;
  gap: 12px;
  margin-top: 18px;
}

button {
  border: none;
  border-radius: 12px;
  padding: 12px 16px;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
}

.btn-primary {
  background: var(--navy);
  color: #fff;
}

.btn-ghost {
  background: rgba(15, 23, 42, 0.06);
  color: var(--navy);
}

.kpi-grid {
  margin-top: 24px;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 16px;
}

.kpi {
  background: var(--card);
  border: 1px solid var(--stroke);
  border-radius: 18px;
  padding: 18px;
  box-shadow: var(--shadow);
}

.kpi h4 {
  margin: 0;
  font-size: 12px;
  color: var(--muted);
  letter-spacing: 0.4px;
  text-transform: uppercase;
}

.kpi p {
  margin: 8px 0 0;
  font-size: 24px;
  font-weight: 700;
}

.kpi small {
  display: block;
  margin-top: 6px;
  color: var(--muted);
}

.section-title {
  margin: 34px 0 16px;
  font-size: 18px;
  font-weight: 700;
}

.charts-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 20px;
}

.chart-card {
  background: var(--card);
  border: 1px solid var(--stroke);
  border-radius: 20px;
  padding: 18px 18px 12px;
  box-shadow: var(--shadow);
  min-height: 320px;
  display: flex;
  flex-direction: column;
}

.chart-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.chart-title {
  font-size: 14px;
  font-weight: 700;
}

.chart-meta {
  font-size: 12px;
  color: var(--muted);
}

.chart-empty {
  margin: auto;
  color: var(--muted);
  font-size: 14px;
  display: none;
}

canvas {
  width: 100% !important;
  height: 100% !important;
  flex: 1;
}

@media (max-width: 980px) {
  .hero {
    grid-template-columns: 1fr;
  }
}
</style>
</head>

<body>
<div class="page">
  <div class="topbar">
    <div class="brand">
      <div class="logo"></div>
      <h1>Analytics Command Center</h1>
    </div>
    <div class="nav">
      <a href="/">Overview</a>
      <a href="/users">Users</a>
    </div>
  </div>

  <section class="hero">
    <div class="hero-card">
      <h2>Live view of users and subscriptions</h2>
      <p>
        One unified dashboard from your Google Sheets data. Spot adoption patterns,
        subscription momentum, and risk distribution at a glance.
      </p>
      <div class="hero-actions">
        <button id="reloadBtn" class="btn-primary" onclick="reload()">Refresh data</button>
        <button class="btn-ghost" onclick="window.scrollTo({top: 900, behavior: 'smooth'})">Jump to charts</button>
      </div>
    </div>
    <div class="hero-card">
      <div class="section-title" style="margin:0 0 10px;">Data sources</div>
      <p>USERS_MASTER and SUBSCRIPTIONS are loaded from the spreadsheet and merged in this dashboard.</p>
      <div class="kpi-grid" style="margin-top:16px;">
        <div class="kpi">
          <h4>Data sync</h4>
          <p id="syncStatus">Ready</p>
          <small id="syncTime">Waiting for refresh</small>
        </div>
        <div class="kpi">
          <h4>Rows loaded</h4>
          <p id="rowsTotal">--</p>
          <small id="rowsDetail">Users + subscriptions</small>
        </div>
      </div>
    </div>
  </section>

  <div class="kpi-grid">
    <div class="kpi"><h4>Total users</h4><p id="kpiUsers">--</p><small>Unique users</small></div>
    <div class="kpi"><h4>Completed</h4><p id="kpiCompleted">--</p><small>Registration status</small></div>
    <div class="kpi"><h4>Verified</h4><p id="kpiVerified">--</p><small>Email verified</small></div>
    <div class="kpi"><h4>Conversion</h4><p id="kpiConversion">--</p><small>Completed / total</small></div>
    <div class="kpi"><h4>Total subscriptions</h4><p id="kpiSubs">--</p><small>All records</small></div>
    <div class="kpi"><h4>Active subs</h4><p id="kpiActive">--</p><small>Status based</small></div>
  </div>

  <div class="section-title">Users breakdown</div>
  <div class="charts-grid">
    <div class="chart-card">
      <div class="chart-header">
        <div class="chart-title">Users by plan</div>
        <div class="chart-meta" id="metaPlan">Plan</div>
      </div>
      <div class="chart-empty" id="emptyPlan">No plan data found</div>
      <canvas id="chartPlan"></canvas>
    </div>
    <div class="chart-card">
      <div class="chart-header">
        <div class="chart-title">Users by risk</div>
        <div class="chart-meta" id="metaRisk">Risk</div>
      </div>
      <div class="chart-empty" id="emptyRisk">No risk data found</div>
      <canvas id="chartRisk"></canvas>
    </div>
    <div class="chart-card">
      <div class="chart-header">
        <div class="chart-title">Registration status</div>
        <div class="chart-meta" id="metaReg">Status</div>
      </div>
      <div class="chart-empty" id="emptyReg">No status data found</div>
      <canvas id="chartReg"></canvas>
    </div>
  </div>

  <div class="section-title">Subscriptions breakdown</div>
  <div class="charts-grid">
    <div class="chart-card">
      <div class="chart-header">
        <div class="chart-title">Subscriptions by status</div>
        <div class="chart-meta" id="metaSubStatus">Status</div>
      </div>
      <div class="chart-empty" id="emptySubStatus">No status data found</div>
      <canvas id="chartSubStatus"></canvas>
    </div>
    <div class="chart-card">
      <div class="chart-header">
        <div class="chart-title">Subscriptions by plan</div>
        <div class="chart-meta" id="metaSubPlan">Plan</div>
      </div>
      <div class="chart-empty" id="emptySubPlan">No plan data found</div>
      <canvas id="chartSubPlan"></canvas>
    </div>
    <div class="chart-card">
      <div class="chart-header">
        <div class="chart-title">Billing interval</div>
        <div class="chart-meta" id="metaSubInterval">Interval</div>
      </div>
      <div class="chart-empty" id="emptySubInterval">No interval data found</div>
      <canvas id="chartSubInterval"></canvas>
    </div>
  </div>
</div>

<script>
const palette = ["#0ea5e9", "#f97316", "#10b981", "#e11d48", "#6366f1", "#f59e0b", "#14b8a6", "#8b5cf6"];

let charts = [];

function normalizeKey(key) {
  return String(key).toLowerCase().replace(/[^a-z0-9]/g, "");
}

function findColumn(rows, candidates) {
  if (!rows || rows.length === 0) return null;
  const keys = Object.keys(rows[0]);
  const map = {};
  keys.forEach(k => { map[normalizeKey(k)] = k; });
  for (const c of candidates) {
    const hit = map[normalizeKey(c)];
    if (hit) return hit;
  }
  return null;
}

function uniqueCount(rows, col) {
  if (!col) return rows.length;
  const set = new Set();
  rows.forEach(r => set.add(String(r[col] ?? "")));
  return set.size;
}

function countMatch(rows, col, values) {
  if (!col) return 0;
  const lookup = values.map(v => String(v).toLowerCase());
  return rows.filter(r => lookup.includes(String(r[col] ?? "").toLowerCase())).length;
}

function groupCounts(rows, col) {
  if (!col) return null;
  const counts = {};
  rows.forEach(r => {
    const raw = r[col];
    const key = String(raw ?? "Unknown").trim() || "Unknown";
    counts[key] = (counts[key] || 0) + 1;
  });
  return counts;
}

function pickColors(n) {
  return Array.from({length: n}, (_, i) => palette[i % palette.length]);
}

function renderChart(id, type, counts, emptyId) {
  const canvas = document.getElementById(id);
  const empty = document.getElementById(emptyId);
  if (!counts || Object.keys(counts).length === 0) {
    canvas.style.display = "none";
    empty.style.display = "block";
    return;
  }

  const labels = Object.keys(counts);
  const values = Object.values(counts);
  const colors = pickColors(labels.length);

  canvas.style.display = "block";
  empty.style.display = "none";

  const chart = new Chart(canvas, {
    type,
    data: {
      labels,
      datasets: [{
        data: values,
        label: "Count",
        backgroundColor: type === "line" ? "rgba(14, 165, 233, 0.2)" : colors,
        borderColor: type === "line" ? "#0ea5e9" : colors,
        borderWidth: type === "bar" ? 0 : 1,
        borderRadius: type === "bar" ? 8 : 0
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: type !== "bar" }
      },
      scales: type === "bar" ? {
        x: { ticks: { color: "#475569" } },
        y: { ticks: { color: "#475569", precision: 0 }, beginAtZero: true }
      } : {}
    }
  });

  charts.push(chart);
}

async function reload() {
  const btn = document.getElementById("reloadBtn");
  btn.disabled = true;
  document.getElementById("syncStatus").innerText = "Loading";
  document.getElementById("syncTime").innerText = new Date().toLocaleString();

  charts.forEach(c => c.destroy());
  charts = [];

  const payload = await fetch("/data").then(r => r.json());
  const users = payload.users_master || [];
  const subs = payload.subscriptions || [];

  const userIdCol = findColumn(users, ["TelegramUserID", "UserID", "user_id", "userid"]);
  const regCol = findColumn(users, ["RegistrationStatus", "Status", "registration_status"]);
  const verifiedCol = findColumn(users, ["EmailVerified", "Verified", "email_verified"]);
  const planCol = findColumn(users, ["InvestmentPlanSelected", "Plan", "plan"]);
  const riskCol = findColumn(users, ["RiskOptionSelected", "Risk", "risk"]);

  const subStatusCol = findColumn(subs, ["Status", "SubscriptionStatus", "subscription_status"]);
  const subPlanCol = findColumn(subs, ["Plan", "PlanName", "plan"]);
  const subIntervalCol = findColumn(subs, ["Interval", "BillingInterval", "billing_interval", "Period"]);

  const totalUsers = uniqueCount(users, userIdCol);
  const completed = countMatch(users, regCol, ["completed"]);
  const verified = countMatch(users, verifiedCol, ["yes", "true", "verified"]);
  const conversion = totalUsers ? ((completed / totalUsers) * 100).toFixed(1) + "%" : "--";

  document.getElementById("kpiUsers").innerText = totalUsers || "--";
  document.getElementById("kpiCompleted").innerText = regCol ? completed : "--";
  document.getElementById("kpiVerified").innerText = verifiedCol ? verified : "--";
  document.getElementById("kpiConversion").innerText = regCol ? conversion : "--";
  document.getElementById("kpiSubs").innerText = subs.length || "--";
  document.getElementById("kpiActive").innerText = subStatusCol ? countMatch(subs, subStatusCol, ["active"]) : "--";

  document.getElementById("rowsTotal").innerText = users.length + subs.length;
  document.getElementById("rowsDetail").innerText = users.length + " users + " + subs.length + " subs";
  document.getElementById("syncStatus").innerText = "Fresh";

  renderChart("chartPlan", "bar", groupCounts(users, planCol), "emptyPlan");
  renderChart("chartRisk", "pie", groupCounts(users, riskCol), "emptyRisk");
  renderChart("chartReg", "bar", groupCounts(users, regCol), "emptyReg");

  renderChart("chartSubStatus", "doughnut", groupCounts(subs, subStatusCol), "emptySubStatus");
  renderChart("chartSubPlan", "bar", groupCounts(subs, subPlanCol), "emptySubPlan");
  renderChart("chartSubInterval", "pie", groupCounts(subs, subIntervalCol), "emptySubInterval");

  btn.disabled = false;
}

reload();
</script>

</body>
</html>
"""


USER_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>User Analytics</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>

<style>
body {
  margin: 0;
  font-family: Inter, system-ui, Arial;
  background: #f3f6fb;
}

.header {
  background: linear-gradient(135deg, #2563eb, #1e40af);
  color: white;
  padding: 18px 30px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
}

.header a {
  color: white;
  text-decoration: none;
  background: rgba(255,255,255,0.15);
  padding: 8px 14px;
  border-radius: 8px;
  font-weight: 600;
}

.container {
  max-width: 1400px;
  margin: auto;
  padding: 30px;
}

.controls {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 25px;
}

select {
  padding: 10px 14px;
  border-radius: 8px;
  border: 1px solid #cbd5e1;
  font-size: 14px;
  min-width: 200px;
}

button {
  background: #2563eb;
  color: white;
  border: none;
  padding: 10px 16px;
  border-radius: 8px;
  cursor: pointer;
  font-weight: 600;
}

button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 20px;
}

.card {
  background: white;
  padding: 20px;
  border-radius: 14px;
  box-shadow: 0 10px 30px rgba(0,0,0,.08);
}

.card h4 {
  margin: 0;
  color: #64748b;
  font-size: 13px;
  font-weight: 600;
}

.card p {
  margin-top: 6px;
  font-size: 20px;
  font-weight: 700;
  color: #0f172a;
}

.chart-wrap {
  margin-top: 40px;
  background: white;
  padding: 25px;
  border-radius: 14px;
  box-shadow: 0 10px 30px rgba(0,0,0,.08);
  height: 420px;
}

.chart-title {
  font-weight: 700;
  margin-bottom: 10px;
}

canvas {
  width: 100% !important;
  height: 100% !important;
}
</style>
</head>

<body>

<div class="header">
  <h1>User Analytics</h1>
  <a href="/">← Back to Overview</a>
</div>

<div class="container">

  <!-- CONTROLS -->
  <div class="controls">
    <select id="userSelect"></select>
    <button id="reloadBtn" onclick="loadUser()">Reload User</button>
  </div>

  <!-- USER INFO -->
  <div class="cards">
    <div class="card"><h4>Name</h4><p id="name">—</p></div>
    <div class="card"><h4>Status</h4><p id="status">—</p></div>
    <div class="card"><h4>Plan</h4><p id="plan">—</p></div>
    <div class="card"><h4>Risk</h4><p id="risk">—</p></div>
  </div>

  <!-- TIMELINE -->
  <div class="chart-wrap">
    <div class="chart-title">User Activity Timeline</div>
    <canvas id="timelineChart"></canvas>
  </div>

</div>

<script>
let timelineChart;

async function fetchJSON(url) {
  return fetch(url).then(r => r.json());
}

async function loadUsers() {
  const users = await fetchJSON('/users-list');
  const select = document.getElementById('userSelect');
  select.innerHTML = '';

  users.forEach(u => {
    const opt = document.createElement('option');
    opt.value = u;
    opt.textContent = u;
    select.appendChild(opt);
  });

  if (users.length > 0) {
    select.value = users[0];
    loadUser();
  }
}

async function loadUser() {
  const btn = document.getElementById('reloadBtn');
  btn.disabled = true;

  const username = document.getElementById('userSelect').value;
  if (!username) return;

  const data = await fetchJSON('/user/' + username);

  // FIXED DOM BINDINGS
  document.getElementById('name').innerText   = data.profile.name || '—';
  document.getElementById('status').innerText = data.profile.status || '—';
  document.getElementById('plan').innerText   = data.profile.plan || '—';
  document.getElementById('risk').innerText   = data.profile.risk || '—';

  const labels = Object.keys(data.timeline);
  const values = Object.values(data.timeline);

  if (timelineChart) timelineChart.destroy();

  timelineChart = new Chart(document.getElementById('timelineChart'), {
    type: 'line',
    data: {
      labels: labels,
      datasets: [{
        label: 'User Activity',
        data: values,
        borderColor: '#2563eb',
        backgroundColor: 'rgba(37,99,235,0.2)',
        fill: true,
        tension: 0.4,
        pointRadius: 6,
        pointHoverRadius: 8
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: true }
      },
      scales: {
        y: {
          beginAtZero: true,
          ticks: { precision: 0 }
        }
      }
    }
  });

  btn.disabled = false;
}

// AUTO LOAD ON USER CHANGE
document.getElementById('userSelect').addEventListener('change', loadUser);

// INIT
loadUsers();
</script>

</body>
</html>
"""
