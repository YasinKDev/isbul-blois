// İş Bul — liste des offres du jour, en turc.
const DATA_URL = "data/offers.json";
const EMOJI = { paketleme: "📦", depo: "🏭", ayiklama: "♻️", uretim: "⚙️", temizlik: "🧽", tarim: "🌽", diger: "💼" };
const DAYS = ["Pazar", "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi"];
const MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];

let offers = [];
let tab = "new";

// Préférences locales (favoris, offres masquées) — rien d'important n'y vit.
const store = {
  get(key) { try { return JSON.parse(localStorage.getItem(key)) || []; } catch { return []; } },
  set(key, val) { try { localStorage.setItem(key, JSON.stringify(val)); } catch {} },
};
const saved = new Set(store.get("saved"));
const hidden = new Set(store.get("hidden"));

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const todayIso = () => new Date().toLocaleDateString("sv-SE");

function dayLabel(iso) {
  const today = todayIso();
  const y = new Date(Date.now() - 864e5).toLocaleDateString("sv-SE");
  if (iso === today) return "Bugün";
  if (iso === y) return "Dün";
  const d = new Date(iso + "T12:00:00");
  return `${DAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), 2200);
}

async function load(force) {
  $("#updated").textContent = "Yükleniyor…";
  try {
    const r = await fetch(DATA_URL + (force ? `?t=${Date.now()}` : ""), { cache: force ? "reload" : "default" });
    const data = await r.json();
    offers = data.offers.filter((o) => o.ai && o.ai.suitable);
    const up = new Date(data.updated);
    $("#updated").textContent = `Son güncelleme: ${dayLabel(up.toLocaleDateString("sv-SE"))} ${up.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" })}`;
  } catch {
    $("#updated").textContent = "İnternet yok — eski liste gösteriliyor";
  }
  render();
}

function visible() {
  const today = todayIso();
  const list = offers.filter((o) => !hidden.has(o.uid));
  if (tab === "saved") return list.filter((o) => saved.has(o.uid));
  if (tab === "new") return list.filter((o) => o.first_seen === today);
  return list;
}

function render() {
  const today = todayIso();
  const notHidden = offers.filter((o) => !hidden.has(o.uid));
  $("#count-new").textContent = notHidden.filter((o) => o.first_seen === today).length || "";
  $("#count-all").textContent = notHidden.length || "";
  $("#count-saved").textContent = notHidden.filter((o) => saved.has(o.uid)).length || "";

  const list = visible().sort((a, b) =>
    (b.first_seen || "").localeCompare(a.first_seen || "") || b.ai.score - a.ai.score);
  const main = $("#list");
  main.innerHTML = "";

  if (!list.length) {
    const msg = { new: "Bugün yeni iş yok.<br>Yarın sabah tekrar bak 🙂", all: "Şu an iş ilanı yok.", saved: "Kayıtlı iş yok.<br>Bir işi açıp ⭐ Kaydet'e bas." }[tab];
    main.innerHTML = `<div class="empty"><div class="big">${tab === "saved" ? "⭐" : "☕"}</div><p>${msg}</p></div>`;
    return;
  }

  let lastDay = null;
  const tpl = $("#card-tpl");
  for (const o of list) {
    if (tab !== "new" && o.first_seen !== lastDay) {
      lastDay = o.first_seen;
      const h = document.createElement("div");
      h.className = "day";
      h.textContent = dayLabel(lastDay);
      main.append(h);
    }
    const card = tpl.content.firstElementChild.cloneNode(true);
    card.querySelector(".emoji").textContent = EMOJI[o.ai.category] || "💼";
    card.querySelector("h2").textContent = o.ai.title_tr || o.title;
    card.querySelector(".meta").textContent = [o.city, o.distance_km != null ? `${Math.round(o.distance_km)} km` : ""].filter(Boolean).join(" · ");
    if (o.first_seen !== today) card.querySelector(".badge-new").remove();
    const chips = card.querySelector(".chips");
    for (const c of [o.ai.duration_tr && `⏳ ${o.ai.duration_tr}`, o.ai.schedule_tr && `🕐 ${o.ai.schedule_tr}`, o.ai.salary_tr && `💶 ${o.ai.salary_tr}`, saved.has(o.uid) && "⭐"]) {
      if (!c) continue;
      const s = document.createElement("span");
      s.className = "chip";
      s.textContent = c;
      chips.append(s);
    }
    card.addEventListener("click", () => open(o));
    main.append(card);
  }
}

function open(o) {
  const a = o.ai;
  const phone = (o.phone || "").replace(/[^\d+]/g, "");
  const maps = `https://maps.apple.com/?daddr=${encodeURIComponent([o.city, o.postal_code].filter(Boolean).join(" "))}`;
  const company = o.company || (o.extra && o.extra.agency) || "";
  const isSaved = saved.has(o.uid);
  $("#detail").innerHTML = `
    <div class="d-emoji">${EMOJI[a.category] || "💼"}</div>
    <h2 class="d-title">${esc(a.title_tr || o.title)}</h2>
    <p class="d-fr">🇫🇷 ${esc(o.title)}</p>
    <p class="d-meta">🏢 ${esc(company)}<br>📍 ${esc(o.city)}${o.distance_km != null ? ` · ${Math.round(o.distance_km)} km` : ""}</p>

    <div class="actions">
      <a class="btn primary" href="${esc(o.url)}" target="_blank" rel="noopener">✅ Başvur</a>
      ${phone ? `<a class="btn call" href="tel:${esc(phone)}">📞 Ara · ${esc(o.phone)}</a>` : ""}
    </div>

    <div class="box"><h3>İş ne?</h3><p>${esc(a.summary_tr)}</p>
      ${a.tasks_tr && a.tasks_tr.length ? `<ul>${a.tasks_tr.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}
    </div>

    <div class="box facts">
      ${a.duration_tr ? `<div class="fact"><span>⏳</span><span>${esc(a.duration_tr)}</span></div>` : ""}
      ${a.schedule_tr ? `<div class="fact"><span>🕐</span><span>${esc(a.schedule_tr)}</span></div>` : ""}
      ${a.salary_tr ? `<div class="fact"><span>💶</span><span>${esc(a.salary_tr)}</span></div>` : ""}
      <div class="fact"><span>🗣️</span><span>Fransızca: ${{ az: "az yeter", orta: "orta", cok: "çok lazım" }[a.french_level] || "-"}</span></div>
    </div>

    <div class="box"><h3>Fransızca mesaj</h3>
      <p class="msg-fr">${esc(a.apply_message_fr)}</p>
      <button class="btn soft" style="width:100%" id="copy">📋 Mesajı kopyala</button>
    </div>

    <div class="row2">
      <button class="btn soft" id="speak">🔊 Dinle</button>
      <a class="btn soft" href="${maps}" target="_blank" rel="noopener">🗺️ Yol</a>
      <button class="btn soft" id="save">${isSaved ? "⭐ Kayıtlı" : "☆ Kaydet"}</button>
      <button class="btn soft" id="hide">🙈 Gizle</button>
    </div>`;

  $("#copy").onclick = async () => {
    try { await navigator.clipboard.writeText(a.apply_message_fr); toast("Mesaj kopyalandı ✅"); }
    catch { toast("Kopyalanamadı"); }
  };
  $("#speak").onclick = () => speak([a.title_tr, a.summary_tr, ...(a.tasks_tr || []), a.duration_tr, a.schedule_tr, a.salary_tr].filter(Boolean).join(". "));
  $("#save").onclick = () => {
    saved.has(o.uid) ? saved.delete(o.uid) : saved.add(o.uid);
    store.set("saved", [...saved]);
    toast(saved.has(o.uid) ? "Kaydedildi ⭐" : "Kayıttan çıkarıldı");
    $("#save").textContent = saved.has(o.uid) ? "⭐ Kayıtlı" : "☆ Kaydet";
    render();
  };
  $("#hide").onclick = () => {
    hidden.add(o.uid);
    store.set("hidden", [...hidden]);
    close();
    toast("İlan gizlendi");
    render();
  };
  $("#sheet").showModal();
  $("#sheet").scrollTop = 0;
}

function close() {
  speechSynthesis.cancel();
  $("#sheet").close();
}

function speak(text) {
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "tr-TR";
  u.rate = 0.9;
  const voice = speechSynthesis.getVoices().find((v) => v.lang.startsWith("tr"));
  if (voice) u.voice = voice;
  speechSynthesis.speak(u);
}

document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("on", x === b));
  tab = b.dataset.tab;
  render();
  window.scrollTo({ top: 0 });
}));
$("#close").onclick = close;
$("#refresh").onclick = () => load(true);
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(true); });

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
load(true);
