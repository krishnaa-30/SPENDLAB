
const $ = id => document.getElementById(id);
const categories = window.EXPENSE_CATEGORIES;
let categoryChart = null;
let trendChart = null;
let toastTimer;

const money = n => Number(n || 0).toLocaleString("en-IN", {
  style: "currency", currency: "INR", maximumFractionDigits: 2
});
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[c]));

function showToast(message) {
  $("toast").textContent = message;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("toast").textContent = "", 3500);
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      ...(options.body && !(options.body instanceof FormData)
        ? { "Content-Type": "application/json" } : {}),
      ...options.headers
    }
  });
  const type = response.headers.get("content-type") || "";
  if (!type.includes("application/json")) {
    throw new Error("Unexpected server response. Check the Flask console.");
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed.");
  return data;
}

const json = (method, body) => ({
  method, body: JSON.stringify(body)
});
const month = () => $("monthPicker").value;
const queryMonth = () => `?month=${encodeURIComponent(month())}`;

function populateCategories() {
  $("transactionCategory").innerHTML = "";
  $("categoryFilter").innerHTML = '<option value="">All categories</option>';
  for (const [type, list] of Object.entries(categories)) {
    const group = document.createElement("optgroup");
    group.label = type === "income" ? "Income" : "Expense";
    list.forEach(category => {
      const option = new Option(category, category);
      group.append(option);
      $("categoryFilter").add(new Option(category, category));
    });
    $("transactionCategory").append(group);
  }
  categories.expense.forEach(c => $("budgetCategory").add(new Option(c, c)));
}

function updateCategoryChoices(type, selected = "") {
  const select = $("transactionCategory");
  select.innerHTML = "";
  categories[type].forEach(c => select.add(new Option(c, c)));
  if (selected && categories[type].includes(selected)) select.value = selected;
}

function openModal(transaction = null) {
  const form = $("transactionForm");
  form.reset();
  form.elements.id.value = transaction?.id || "";
  $("transactionModalTitle").textContent = transaction ? "Edit transaction" : "New transaction";
  $("transactionType").value = transaction?.type || "expense";
  updateCategoryChoices($("transactionType").value, transaction?.category || "");
  form.elements.amount.value = transaction?.amount ?? "";
  form.elements.date.value = transaction?.date || new Date().toISOString().slice(0, 10);
  form.elements.description.value = transaction?.description || "";
  $("transactionModal").hidden = false;
  form.elements.amount.focus();
}

function closeModal() {
  $("transactionModal").hidden = true;
}

async function loadDashboard() {
  const d = await api("/api/dashboard" + queryMonth());
  $("incomeValue").textContent = money(d.income);
  $("expenseValue").textContent = money(d.expenses);
  $("balanceValue").textContent = money(d.balance);
  $("savingsValue").textContent = d.income > 0
    ? `${(d.balance / d.income * 100).toFixed(1)}%` : "—";

  $("budgetValue").textContent = d.budget ? money(d.budget) : "Not set";
  $("budgetSpent").textContent = `${money(d.expenses)} spent`;
  const usage = d.budget_usage;
  $("budgetUsage").textContent = d.budget
    ? `${usage.toFixed(1)}% used · ${money(d.budget_remaining)} remaining`
    : "Set a budget to track usage";
  $("budgetProgress").style.width = `${Math.min(usage, 100)}%`;
  $("budgetProgress").classList.toggle("over", usage > 100);

  $("categoryBudgetHealth").innerHTML = d.category_budgets.length
    ? d.category_budgets.map(b => `
      <div class="health-item">
        <div class="health-row"><span>${esc(b.category)}</span>
          <strong>${money(b.spent)} / ${money(b.budget)}</strong></div>
        <div class="progress-track"><div class="progress-fill ${b.usage > 100 ? "over" : ""}"
          style="width:${Math.min(b.usage, 100)}%"></div></div>
      </div>`).join("")
    : '<p class="muted">No category budgets set yet.</p>';

  renderCharts(d);
  renderGoalPreview(d.goals);
  $("year").textContent = new Date().getFullYear();
}

function renderCharts(d) {
  if (categoryChart) categoryChart.destroy();
  if (trendChart) trendChart.destroy();

  $("categoryEmpty").hidden = d.categories.length > 0;
  $("trendEmpty").hidden = d.trends.length > 0;

  const gridColor = "#e2dfd4";
  const common = {
    responsive: true, maintainAspectRatio: false,
    plugins: {
      legend: { labels: { color: "#171a17", font: { family: "Space Grotesk" } } }
    }
  };

  if (d.categories.length) {
    categoryChart = new Chart($("categoryChart"), {
      type: "doughnut",
      data: {
        labels: d.categories.map(x => x.category),
        datasets: [{
          data: d.categories.map(x => x.total),
          backgroundColor: [
            "#a6ff72", "#ffb7cb", "#ffe66d", "#b8d9ff",
            "#c9b5ff", "#ffb878", "#80e4d2", "#ed8f9d",
            "#d6e69a", "#b6b6b6"
          ],
          borderColor: "#171a17", borderWidth: 2
        }]
      },
      options: {
        ...common, cutout: "60%",
        plugins: {
          ...common.plugins,
          legend: { position: "bottom", labels: { color: "#171a17", boxWidth: 10 } }
        }
      }
    });
  }

  if (d.trends.length) {
    trendChart = new Chart($("trendChart"), {
      type: "bar",
      data: {
        labels: d.trends.map(x => x.month),
        datasets: [
          { label: "Income", data: d.trends.map(x => x.income),
            backgroundColor: "#a6ff72", borderColor: "#171a17", borderWidth: 1 },
          { label: "Expenses", data: d.trends.map(x => x.expenses),
            backgroundColor: "#ffb7cb", borderColor: "#171a17", borderWidth: 1 }
        ]
      },
      options: {
        ...common,
        scales: {
          x: { ticks: { color: "#555" }, grid: { display: false } },
          y: { beginAtZero: true, ticks: { color: "#555" },
            grid: { color: gridColor } }
        }
      }
    });
  }
}

async function loadTransactions() {
  const params = new URLSearchParams({
    month: month(),
    search: $("searchInput").value.trim(),
    type: $("typeFilter").value,
    category: $("categoryFilter").value
  });
  const rows = await api("/api/transactions?" + params);
  $("transactionRows").innerHTML = rows.map(t => `
    <tr>
      <td>${esc(t.date)}</td>
      <td>${esc(t.description || t.category)}</td>
      <td>${esc(t.category)}</td>
      <td><span class="type-pill ${t.type}">${esc(t.type)}</span></td>
      <td class="amount-${t.type}">${t.type === "expense" ? "−" : "+"}${money(t.amount)}</td>
      <td><div class="row-actions">
        <button class="mini-btn" data-edit="${t.id}">Edit</button>
        <button class="mini-btn" data-delete="${t.id}">Delete</button>
      </div></td>
    </tr>`).join("");
  $("transactionCount").textContent = `${rows.length} transaction(s) shown · Maximum 500`;
  $("transactionRows").querySelectorAll("[data-edit]").forEach(button => {
    button.addEventListener("click", async () => {
      try {
        const all = await api("/api/transactions?" + new URLSearchParams({ month: month() }));
        const t = all.find(x => x.id === Number(button.dataset.edit));
        if (t) openModal(t);
        else showToast("Entry is outside the current month.");
      } catch (e) { showToast(e.message); }
    });
  });
  $("transactionRows").querySelectorAll("[data-delete]").forEach(button => {
    button.addEventListener("click", async () => {
      if (!confirm("Delete this transaction?")) return;
      try {
        await api(`/api/transactions/${button.dataset.delete}`, { method: "DELETE" });
        showToast("Transaction deleted.");
        await reloadData();
      } catch (e) { showToast(e.message); }
    });
  });
}

async function loadBudgets() {
  const rows = await api("/api/budgets" + queryMonth());
  $("budgetList").innerHTML = rows.length ? rows.map(b => `
    <div class="manage-item"><div class="manage-row">
      <div class="detail"><strong>${b.category === "*" ? "Overall budget" : esc(b.category)}</strong>
      <small>${esc(b.month)} · ${money(b.amount)}</small></div>
      <div class="manage-actions"><button class="mini-btn" data-budget-delete="${b.id}">Delete</button></div>
    </div></div>`).join("") : '<p class="muted">No budgets configured for this month.</p>';

  $("budgetList").querySelectorAll("[data-budget-delete]").forEach(btn => {
    btn.addEventListener("click", async () => {
      if (!confirm("Delete this budget?")) return;
      try {
        await api(`/api/budgets/${btn.dataset.budgetDelete}`, { method: "DELETE" });
        showToast("Budget deleted.");
        await reloadData();
      } catch (e) { showToast(e.message); }
    });
  });
}

function renderGoalPreview(goals) {
  $("goalPreview").innerHTML = goals.length ? goals.slice(0, 4).map(g => {
    const pct = Math.min(g.saved / g.target * 100, 100);
    return `<div class="goal-item"><div class="goal-row">
      <strong>${esc(g.name)}</strong><span>${pct.toFixed(0)}%</span></div>
      <div class="progress-track"><div class="progress-fill" style="width:${pct}%"></div></div>
      <p class="hint">${money(g.saved)} of ${money(g.target)}</p></div>`;
  }).join("") : '<p class="muted">No goals yet. Create your first savings goal.</p>';
}

async function loadGoals() {
  const goals = await api("/api/goals");
  $("goalList").innerHTML = goals.length ? goals.map(g => `
    <div class="manage-item"><div class="manage-row">
      <div class="detail"><strong>${esc(g.name)}</strong>
        <small>${money(g.saved)} / ${money(g.target)}${g.deadline ? " · Due " + esc(g.deadline) : ""}</small>
        <small>${Math.min(100, g.saved / g.target * 100).toFixed(1)}% saved</small></div>
      <div class="manage-actions">
        <button class="mini-btn" data-goal-edit="${g.id}">Update</button>
        <button class="mini-btn" data-goal-delete="${g.id}">Delete</button>
      </div></div></div>`).join("") : '<p class="muted">No savings goals created.</p>';

  $("goalList").querySelectorAll("[data-goal-edit]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const goal = goals.find(g => g.id === Number(btn.dataset.goalEdit));
      if (!goal) return;
      const name = prompt("Goal name:", goal.name);
      if (name === null) return;
      const target = prompt("Target amount (₹):", goal.target);
      if (target === null) return;
      const saved = prompt("Amount already saved (₹):", goal.saved);
      if (saved === null) return;
      const deadline = prompt("Deadline (YYYY-MM-DD, optional):", goal.deadline || "");
      if (deadline === null) return;
      try {
        await api(`/api/goals/${goal.id}`, json("PUT", {
          name, target: Number(target), saved: Number(saved), deadline
        }));
        showToast("Goal updated.");
        await reloadData();
      } catch (e) { showToast(e.message); }
    });
  });

  $("goalList").querySelectorAll("[data-goal-delete]").forEach(btn => {
    btn.addEventListener("click", async () => {
      if (!confirm("Delete this savings goal?")) return;
      try {
        await api(`/api/goals/${btn.dataset.goalDelete}`, { method: "DELETE" });
        showToast("Goal deleted.");
        await reloadData();
      } catch (e) { showToast(e.message); }
    });
  });
}

const KIND_TITLES = {
  over: "Over budget",
  pace: "Heading over budget",
  new: "Set a budget",
  lower: "Room to lower this budget"
};

async function loadInsights() {
  const rows = await api("/api/suggestions" + queryMonth());
  $("insightList").innerHTML = rows.length ? rows.map(s => `
    <article class="insight-card sev-${esc(s.severity)}">
      <div class="panel-heading"><span class="tag">${esc(s.category)}</span>
        <span class="insight-status">${s.status === "pending" ? esc(s.severity) + " priority" : esc(s.status)}</span></div>
      <h3>${s.status === "pending" ? esc(KIND_TITLES[s.kind] || "Budget recommendation") : "Recommendation reviewed"}</h3>
      <p>${esc(s.reason)}</p>
      <div class="insight-amount">${money(s.suggested_budget)} <span class="muted">suggested</span></div>
      <p>Current category budget: ${s.old_budget ? money(s.old_budget) : "Not set"}</p>
      <p class="insight-meta">Last month ${money(s.last_month)} · Projected ${money(s.projected)}</p>
      <div class="confidence" title="How much data supports this suggestion">
        <span>Confidence ${s.confidence}%</span>
        <div class="progress-track"><div class="progress-fill" style="width:${s.confidence}%"></div></div>
      </div>
      ${s.status === "pending" ? `<div class="insight-actions">
        <button class="btn btn-green" data-accept="${s.id}">Accept suggestion</button>
        <button class="btn btn-danger" data-reject="${s.id}">Reject</button>
      </div>` : `<p class="muted">Decision: ${esc(s.status)}.</p>`}
    </article>`).join("") :
    '<article class="insight-card"><h3>No new recommendations</h3><p>Add spending for this month and last month, plus a few budgets, to get adaptive suggestions. Last month is used as the baseline.</p></article>';

  $("insightList").querySelectorAll("[data-accept]").forEach(btn => {
    btn.addEventListener("click", () => decideSuggestion(btn.dataset.accept, "accepted"));
  });
  $("insightList").querySelectorAll("[data-reject]").forEach(btn => {
    btn.addEventListener("click", () => decideSuggestion(btn.dataset.reject, "rejected"));
  });
}

async function loadAdvisor() {
  const d = await api("/api/advisor" + queryMonth());
  $("advisorScore").textContent = d.score ?? "–";
  $("advisorLabel").textContent = d.label.toUpperCase();
  $("advisorHeadline").textContent = d.headline;
  $("scoreBadge").className = "score-badge " + (
    d.score === null ? "" : d.score >= 80 ? "good" : d.score >= 60 ? "ok" : "bad");
  $("advisorInsights").innerHTML = d.insights.map(i =>
    `<li class="tone-${esc(i.tone)}">${esc(i.text)}</li>`).join("");
  $("askAnswer").hidden = true;
}

async function askAdvisor(question) {
  question = question.trim();
  if (!question) return;
  const box = $("askAnswer");
  box.hidden = false;
  box.textContent = "Thinking…";
  try {
    const r = await api("/api/advisor/ask", json("POST", { question, month: month() }));
    box.textContent = r.answer;
  } catch (e) {
    box.textContent = e.message;
  }
}

async function decideSuggestion(id, status) {
  try {
    await api(`/api/suggestions/${id}/decision`, json("POST", { status }));
    showToast(`Suggestion ${status}.`);
    await reloadData();
  } catch (e) { showToast(e.message); }
}

async function reloadData() {
  try {
    await Promise.all([
      loadDashboard(), loadTransactions(), loadBudgets(),
      loadGoals(), loadInsights(), loadAdvisor()
    ]);
  } catch (e) { showToast(e.message); }
}

$("transactionType").addEventListener("change", () =>
  updateCategoryChoices($("transactionType").value));

$("transactionForm").addEventListener("submit", async event => {
  event.preventDefault();
  const f = event.currentTarget;
  const data = Object.fromEntries(new FormData(f));
  data.amount = Number(data.amount);
  const id = data.id;
  delete data.id;
  try {
    await api(id ? `/api/transactions/${id}` : "/api/transactions",
      json(id ? "PUT" : "POST", data));
    closeModal();
    showToast(id ? "Transaction updated." : "Transaction added.");
    await reloadData();
  } catch (e) { showToast(e.message); }
});

$("budgetForm").addEventListener("submit", async event => {
  event.preventDefault();
  const f = event.currentTarget;
  try {
    await api("/api/budgets", json("POST", {
      month: f.elements.month.value,
      category: f.elements.category.value,
      amount: Number(f.elements.amount.value)
    }));
    f.elements.amount.value = "";
    showToast("Budget saved.");
    await reloadData();
  } catch (e) { showToast(e.message); }
});

$("goalForm").addEventListener("submit", async event => {
  event.preventDefault();
  const f = event.currentTarget;
  const data = Object.fromEntries(new FormData(f));
  data.target = Number(data.target);
  data.saved = Number(data.saved);
  try {
    await api("/api/goals", json("POST", data));
    f.reset();
    f.elements.saved.value = 0;
    showToast("Savings goal created.");
    await reloadData();
  } catch (e) { showToast(e.message); }
});

$("addTopBtn").addEventListener("click", () => openModal());
$("addTransactionBtn").addEventListener("click", () => openModal());
$("closeModal").addEventListener("click", closeModal);
$("transactionModal").addEventListener("click", e => {
  if (e.target === $("transactionModal")) closeModal();
});
document.addEventListener("keydown", e => {
  if (e.key === "Escape") { closeModal(); closeImport(); }
});

$("monthPicker").addEventListener("change", async () => {
  $("budgetMonth").value = month();
  await reloadData();
});
$("searchInput").addEventListener("input", () => loadTransactions().catch(e => showToast(e.message)));
$("typeFilter").addEventListener("change", () => loadTransactions().catch(e => showToast(e.message)));
$("categoryFilter").addEventListener("change", () => loadTransactions().catch(e => showToast(e.message)));

$("exportBtn").addEventListener("click", () => {
  window.location.href = "/api/export" + queryMonth();
});

// ---- Advisor Q&A -------------------------------------------------------
$("askForm").addEventListener("submit", event => {
  event.preventDefault();
  askAdvisor($("askInput").value);
});
document.querySelectorAll(".chip").forEach(chip => {
  chip.addEventListener("click", () => {
    $("askInput").value = chip.dataset.q;
    askAdvisor(chip.dataset.q);
  });
});

// ---- CSV import --------------------------------------------------------
function openImport() {
  $("importForm").reset();
  $("importResult").hidden = true;
  $("importModal").hidden = false;
  $("importFile").focus();
}
function closeImport() { $("importModal").hidden = true; }

$("importTopBtn").addEventListener("click", openImport);
$("importBtn").addEventListener("click", openImport);
$("closeImport").addEventListener("click", closeImport);
$("importModal").addEventListener("click", e => {
  if (e.target === $("importModal")) closeImport();
});

$("importForm").addEventListener("submit", async event => {
  event.preventDefault();
  const file = $("importFile").files[0];
  if (!file) return showToast("Choose a CSV file first.");
  const body = new FormData();
  body.append("file", file);
  body.append("mode", $("importMode").value);

  const result = $("importResult");
  const submit = $("importSubmit");
  submit.disabled = true;
  submit.textContent = "Importing…";
  result.hidden = false;
  result.className = "import-result";
  result.textContent = "Reading file…";
  try {
    const r = await api("/api/import", { method: "POST", body });
    const lines = [
      `<strong>${r.imported}</strong> imported`,
      `${r.duplicates} duplicate(s) skipped`,
      `${r.auto_categorized} auto-categorised`,
      `${r.error_count} row(s) with problems`
    ];
    result.innerHTML = `<p>${lines.join(" · ")}</p>` + (r.errors.length
      ? `<ul>${r.errors.map(x => `<li>Row ${x.row}: ${esc(x.message)}</li>`).join("")}</ul>` : "");
    if (r.error_count > r.errors.length) {
      result.insertAdjacentHTML("beforeend",
        `<p class="muted">…and ${r.error_count - r.errors.length} more.</p>`);
    }
    // Jump to the newest imported month if the current view would show nothing new.
    if (r.months.length && !r.months.includes(month())) {
      $("monthPicker").value = r.months[r.months.length - 1];
      $("budgetMonth").value = month();
      result.insertAdjacentHTML("beforeend",
        `<p class="muted">Switched to ${esc(month())} to show your imported data.</p>`);
    }
    showToast(r.message);
    await reloadData();
  } catch (e) {
    result.className = "import-result error";
    result.textContent = e.message;
  } finally {
    submit.disabled = false;
    submit.textContent = "Import transactions ↑";
  }
});

document.querySelectorAll(".nav-link").forEach(link => {
  link.addEventListener("click", () => {
    document.querySelectorAll(".nav-link").forEach(a => a.classList.remove("active"));
    link.classList.add("active");
  });
});

async function start() {
  const today = new Date();
  const current = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}`;
  $("monthPicker").value = current;
  $("budgetMonth").value = current;
  populateCategories();
  await reloadData();
}

start().catch(e => showToast(e.message));
