let DATA = null, CUR = null, CURDIR = null, SHOW_HIDDEN = false;
const $ = s => document.querySelector(s);
const fmtSize = n => {
  const u = ["B","KB","MB","GB","TB"]; let i = 0;
  while (n >= 1024 && i < 4) { n /= 1024; i++; }
  return (i ? n.toFixed(1) : n) + " " + u[i];
};
const fmtTime = t => {
  const d = new Date(t * 1000), now = new Date();
  const days = Math.floor((now - d) / 86400000);
  const dt = `${d.getMonth()+1}/${d.getDate()}`;
  if (days === 0) return "今天";
  if (days === 1) return "昨天";
  if (days < 30) return `${days}天前`;
  return dt;
};
function dlInfo(deadline) {
  if (!deadline) return "";
  const days = Math.ceil((new Date(deadline) - new Date()) / 86400000);
  if (days < 0) return `<span class="chip dl">已逾期 ${-days} 天</span>`;
  if (days <= 7) return `<span class="chip dl">⏰ 还剩 ${days} 天</span>`;
  return `<span class="chip dl-ok">${deadline} 截止</span>`;
}

async function load() {
  const r = await fetch("/api/projects");
  DATA = await r.json();
  render();
}

function pass(p) {
  const q = $("#q").value.trim().toLowerCase();
  const sf = $("#statusFilter").value;
  const tf = $("#typeFilter").value;
  if (p.hidden && !SHOW_HIDDEN) return false;
  if (sf && p.status !== sf) return false;
  if (tf && p.type !== tf) return false;
  if (q && !(p.name.toLowerCase().includes(q) || p.client.toLowerCase().includes(q) ||
             p.notes.toLowerCase().includes(q))) return false;
  return true;
}

function render() {
  const main = $("#main");
  const hiddenCnt = DATA.groups.flatMap(g => g.projects).filter(p => p.hidden).length;

  // 分桶：主视图(客户/开发项目) 按组聚合；其余进折叠区
  const buckets = new Map();
  const aux = [];
  let shown = 0;
  for (const g of DATA.groups) {
    for (const p of g.projects) {
      if (!pass(p)) continue;
      if (p.type === "客户项目" || p.type === "开发项目") {
        shown++;
        if (!buckets.has(g.group)) buckets.set(g.group, []);
        buckets.get(g.group).push(p);
      } else aux.push(p);
    }
  }

  let html = "";
  for (const [gname, ps] of buckets) {
    const dev = ps.every(p => p.type === "开发项目");
    html += `<div class="gtitle">${dev ? "🧑‍💻" : "📁"} ${gname} · ${ps.length}</div><div class="grid">`;
    for (const p of ps) html += cardHtml(p);
    html += "</div>";
  }
  if (aux.length) {
    html += `<div class="gtitle aux-toggle" onclick="toggleAux()">📦 资源与归档 · ${aux.length} <span class="dim">${AUX_OPEN ? "收起 ▲" : "展开 ▼"}</span></div>`;
    if (AUX_OPEN) {
      html += '<div class="grid">';
      for (const p of aux) html += cardHtml(p);
      html += "</div>";
    }
  }
  if (hiddenCnt) {
    html = `<div class="hidbar"><label><input type="checkbox" id="showHid" ${SHOW_HIDDEN ? "checked" : ""}
      onchange="SHOW_HIDDEN=this.checked;render()"> 显示已隐藏项目（${hiddenCnt}）</label></div>` + html;
  }
  main.innerHTML = html || '<div class="empty">没有匹配的项目</div>';
  document.title = `项目台账 (${shown})`;
}
let AUX_OPEN = false;
function toggleAux() { AUX_OPEN = !AUX_OPEN; render(); }

function cardHtml(p) {
  return `<div class="card ${p.hidden ? 'is-hidden' : ''} ${p.type === '开发项目' ? 'is-dev' : ''}" onclick="openModal('${encodeURIComponent(p.key)}')">
    <div class="name">${p.type === '开发项目' ? '🧑‍💻 ' : ''}${p.name}</div>
    <div class="meta">
      ${p.type !== '客户项目' ? `<span class="chip t-dev">${p.type}</span>` : ""}
      <span class="chip s-${p.status}">${p.status}</span>
      ${p.client ? `<span class="chip">${p.client}</span>` : ""}
      ${dlInfo(p.deadline)}
    </div>
    ${p.notes ? `<div class="notes">📝 ${p.notes}</div>` : ""}
    <div class="foot"><span>📂 ${p.files} 个文件 · ${fmtSize(p.size)}</span>
    <span class="ops">
      <a onclick="toggleHide(event,'${encodeURIComponent(p.key)}',${p.hidden ? 0 : 1})">${p.hidden ? "取消隐藏" : "隐藏"}</a>
      <span>活动: ${fmtTime(p.mtime)}</span></span></div>
  </div>`;
}

async function toggleHide(ev, key, hidden) {
  ev.stopPropagation();
  await fetch("/api/meta", { method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ key: decodeURIComponent(key), hidden }) });
  await fetch("/api/projects?force=1");
  load();
}

/* ---------- 详情弹窗 ---------- */
function openModal(encKey) {
  CUR = decodeURIComponent(encKey);
  const p = DATA.groups.flatMap(g => g.projects).find(x => x.key === CUR);
  if (!p) return;
  $("#mTitle").textContent = (p.type === '开发项目' ? '🧑‍💻 ' : '') + p.name;
  $("#mType").value = ["客户项目","开发项目","内部事务","归档"].includes(p.type) ? p.type : "客户项目";
  $("#mStatus").value = ["未标记","进行中","已完成","暂停","已交付"].includes(p.status) ? p.status : "未标记";
  $("#mClient").value = p.client || "";
  $("#mDeadline").value = p.deadline || "";
  $("#mNotes").value = p.notes || "";
  $("#mStat").textContent = `${p.files} 个文件 · ${fmtSize(p.size)}`;
  $("#mask").classList.remove("hidden");
  listDir(CUR);
}
function closeModal() { $("#mask").classList.add("hidden"); }

async function saveMeta() {
  await fetch("/api/meta", { method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ key: CUR, status: $("#mStatus").value, client: $("#mClient").value,
      deadline: $("#mDeadline").value, notes: $("#mNotes").value, type: $("#mType").value }) });
  closeModal();
  load();
}
function openFolder() {
  fetch("/api/open", { method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ path: CUR }) });
}

/* ---------- 文件浏览 ---------- */
async function listDir(path) {
  CURDIR = path;
  const r = await fetch("/api/files?path=" + encodeURIComponent(path));
  const d = await r.json();
  if (!d.ok) { $("#fileList").innerHTML = `<div class="fitem">读取失败: ${d.msg}</div>`; return; }
  $("#crumbs").innerHTML =
    `<a onclick="listDir(CUR)">📁 项目根目录</a> / ` +
    `<span>${d.path}</span> <a style="margin-left:8px" onclick="openDir()">在资源管理器打开</a>`;
  $("#fileList").innerHTML = d.items.map(it => {
    const enc = encodeURIComponent(path + "/" + it.name);
    const icon = it.dir ? "📁" : iconOf(it.name);
    return `<div class="fitem" onclick="clickItem('${enc}',${it.dir})">
      <span>${icon}</span><span>${it.name}</span>
      <span class="sz">${it.dir ? "" : fmtSize(it.size)} · ${fmtTime(it.mtime)}</span></div>`;
  }).join("") || '<div class="fitem">（空目录）</div>';
}
function iconOf(n) {
  const e = n.split(".").pop().toLowerCase();
  return {md:"📄",txt:"📄",doc:"📘",docx:"📘",pdf:"📕",xls:"📗",xlsx:"📗",
          ppt:"📙",pptx:"📙",png:"🖼️",jpg:"🖼️",jpeg:"🖼️",zip:"🗜️",rar:"🗜️",
          ovpn:"🔒",bat:"⚙️",exe:"⚙️",msi:"⚙️"}[e] || "📄";
}
function openDir() {
  fetch("/api/open", { method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ path: CURDIR }) });
}
function clickItem(enc, isDir) {
  const full = decodeURIComponent(enc);
  if (isDir) listDir(full);
  else if (isPreviewable(full)) preview(full);
  else openSelect(full);
}
let CUR_PREVIEW = "";
function openNative() {
  if (!CUR_PREVIEW) return;
  fetch("/api/open", { method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ path: CUR_PREVIEW }) });
}
function openSelect(path) {
  fetch("/api/open", { method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ path, mode: "select" }) });
}
const PREVIEW_EXT = new Set(["md","txt","log","json","py","toml","yaml","yml","ini","conf",
  "cfg","sh","bat","ps1","csv","sql","js","html","css","png","jpg","jpeg","gif","bmp",
  "webp","svg","ico","pdf","docx","pptx","xlsx"]);
function isPreviewable(name) {
  const e = name.split(".").pop().toLowerCase();
  return PREVIEW_EXT.has(e);
}
async function preview(path, kw) {
  CUR_PREVIEW = path;
  const r = await fetch("/api/file?path=" + encodeURIComponent(path));
  const ct = r.headers.get("Content-Type") || "";
  $("#pText").classList.add("hidden"); $("#pImg").classList.add("hidden");
  $("#pPdf").classList.add("hidden"); $("#pFallback").classList.add("hidden");
  if (ct.includes("application/json")) {
    const d = await r.json();
    $("#pTitle").textContent = d.name;
    if (kw) {
      const re = new RegExp("(" + kw.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "gi");
      $("#pText").innerHTML = esc(d.content || "").replace(re, "<mark>$1</mark>");
    } else {
      $("#pText").textContent = d.content || "(空文件)";
    }
    $("#pText").classList.remove("hidden");
  } else if (ct.startsWith("image/")) {
    $("#pTitle").textContent = decodeURIComponent(path.split("/").pop());
    const blob = await r.blob();
    $("#pImg").src = URL.createObjectURL(blob);
    $("#pImg").classList.remove("hidden");
  } else if (ct === "application/pdf") {
    $("#pTitle").textContent = decodeURIComponent(path.split("/").pop());
    $("#pPdf").src = "/api/file?path=" + encodeURIComponent(path);
    $("#pPdf").classList.remove("hidden");
  } else {
    // 无法在线预览 → 直接在资源管理器中打开所在目录并定位文件
    $("#preview").classList.add("hidden");
    openSelect(path);
    return;
  }
  $("#preview").classList.remove("hidden");
}
function closePreview() { $("#preview").classList.add("hidden"); }

/* ---------- 事件 ---------- */
const esc = s => s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
function hi(snips, kw) {
  if (!snips) return [];
  const re = new RegExp("(" + kw.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "gi");
  return snips.map(s => esc(s).replace(re, "<mark>$1</mark>"));
}
let searchTimer = null, CUR_Q = "";
$("#q").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(doSearch, 300);
});
$("#statusFilter").addEventListener("change", render);
$("#typeFilter").addEventListener("change", render);
$("#refresh").addEventListener("click", async () => {
  await fetch("/api/projects?force=1"); load();
});

async function doSearch() {
  const q = $("#q").value.trim();
  CUR_Q = q;
  if (!q) { render(); return; }
  const main = $("#main");
  main.innerHTML = '<div class="empty">搜索中…</div>';
  const r = await fetch("/api/search?q=" + encodeURIComponent(q));
  const d = await r.json();
  let html = `<div class="shead">🔍 「${esc(q)}」 — <b>${d.files.length}</b> 个文件 · <b>${d.total}</b> 处内容匹配 · ${d.hits.length} 个项目匹配
    <a class="dimlink" onclick="reindex()">重建索引</a></div>`;
  if (d.hits.length) {
    html += `<div class="gtitle">📌 项目匹配</div><div class="grid">`;
    for (const p of d.hits) {
      html += `<div class="card" onclick="openModal('${encodeURIComponent(p.key)}')">
        <div class="name">${p.name}</div>
        <div class="meta"><span class="chip s-${p.status}">${p.status}</span>
        ${p.client ? `<span class="chip">${p.client}</span>` : ""}</div>
        ${p.notes ? `<div class="notes">📝 ${esc(p.notes)}</div>` : ""}</div>`;
    }
    html += "</div>";
  }
  if (d.files.length) {
    html += `<div class="gtitle">📄 文档内容</div>`;
    for (const f of d.files) {
      html += `<div class="fresult" onclick="previewFull('${encodeURIComponent(f.full)}')">
        <div class="fname">📄 ${esc(f.name)} <span class="dim fpath">${esc(f.path)}</span>
        ${f.hits ? `<span class="chip">${f.hits} 处</span>` : ""}</div>
        ${hi(f.snips, q).map(s => `<div class="snip">${s}</div>`).join("")}</div>`;
    }
  } else if (!d.hits.length) {
    html += '<div class="empty">没有找到相关内容。新加入的文件需要 <a class="dimlink" onclick="reindex()">重建索引</a></div>';
  }
  main.innerHTML = html;
  document.title = `搜索: ${q}`;
}
async function reindex() {
  const main = $("#main");
  main.innerHTML = '<div class="empty">重建索引中，约 10 秒…</div>';
  await fetch("/api/reindex", { method: "POST" });
  doSearch();
}
async function previewFull(fullPath) {
  const full = decodeURIComponent(fullPath);
  if (isPreviewable(full)) preview(full, CUR_Q);
  else openSelect(full);
}

document.addEventListener("keydown", e => {
  if (e.key === "Escape") { closePreview(); closeModal(); }
});

load();
