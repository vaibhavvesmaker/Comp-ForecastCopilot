const money = (n) => `$${Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

async function loadCommission() {
  const res = await fetch("/api/commission");
  const data = await res.json();
  document.getElementById("commission-total").textContent = money(data.total_payout_amount ?? 0);
  const list = document.getElementById("commission-detail");
  list.innerHTML = "";
  for (const payout of data.payouts ?? []) {
    const li = document.createElement("li");
    li.textContent = `${payout.deal_id} — ${money(payout.payout_amount)} (${(payout.tier_rate * 100).toFixed(0)}% tier)`;
    list.appendChild(li);
  }
  if ((data.skipped ?? []).length) {
    const li = document.createElement("li");
    li.textContent = `${data.skipped.length} deal(s) skipped — see /api/commission for reasons`;
    list.appendChild(li);
  }
}

async function loadForecast() {
  const res = await fetch("/api/forecast");
  const data = await res.json();
  document.getElementById("forecast-total").textContent = money(data.blended_forecast ?? 0);
  const list = document.getElementById("forecast-detail");
  list.innerHTML = "";
  const stageLi = document.createElement("li");
  stageLi.textContent = `Stage-weighted: ${money(data.stage_weighted_total)}`;
  list.appendChild(stageLi);
  if (data.historical_win_rate != null) {
    const trendLi = document.createElement("li");
    trendLi.textContent = `Historical win rate: ${(data.historical_win_rate * 100).toFixed(1)}%`;
    list.appendChild(trendLi);
  }
}

async function loadHealth() {
  const res = await fetch("/api/health");
  const data = await res.json();
  const atRisk = data.at_risk_count ?? 0;
  const statEl = document.getElementById("health-at-risk");
  statEl.textContent = atRisk;
  statEl.classList.toggle("stat--good", atRisk === 0);
  statEl.classList.toggle("stat--warn", atRisk > 0);

  const list = document.getElementById("health-detail");
  list.innerHTML = "";
  for (const score of data.scores ?? []) {
    const li = document.createElement("li");
    li.textContent = `${score.deal_id} — ${score.score}/100${score.is_at_risk ? " (at risk)" : ""}`;
    if (score.is_at_risk) li.classList.add("detail-list__item--warn");
    list.appendChild(li);
  }
}

async function loadScenarios() {
  const res = await fetch("/api/scenarios");
  const data = await res.json();
  document.getElementById("scenario-worst").textContent = money(data.worst.blended_forecast);
  document.getElementById("scenario-commit").textContent = money(data.commit.blended_forecast);
  document.getElementById("scenario-best").textContent = money(data.best.blended_forecast);
}

async function generateNarrative() {
  const button = document.getElementById("generate-narrative-btn");
  const output = document.getElementById("narrative-output");
  button.disabled = true;
  button.textContent = "Generating…";
  output.classList.remove("error");
  output.innerHTML = '<p class="narrative-placeholder">Generating narrative…</p>';

  try {
    const res = await fetch("/api/narrative", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Narrative generation failed.");
    }
    output.innerHTML = marked.parse(data.narrative);
  } catch (err) {
    output.classList.add("error");
    output.innerHTML = `<p>Could not generate narrative: ${err.message}</p>`;
  } finally {
    button.disabled = false;
    button.textContent = "Generate Narrative";
  }
}

document.getElementById("generate-narrative-btn").addEventListener("click", generateNarrative);

loadCommission();
loadForecast();
loadHealth();
loadScenarios();
