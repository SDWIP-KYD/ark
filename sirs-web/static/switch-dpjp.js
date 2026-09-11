/* ============================================================
   switch-dpjp.js — Widget "Switch DPJP" bersama untuk semua halaman.
   Cara pakai: sertakan <script src="/static/switch-dpjp.js"></script>
   setelah DOM. Widget otomatis menempel ke elemen #loginBadge:
   - isi teks badge dari /api/me
   - klik badge -> dropdown daftar akun DPJP -> klik -> /api/switch
   - sukses -> reload halaman (sesi baru langsung aktif)
   ============================================================ */
(function () {
  let accList = [];
  let currentUser = "";
  let menuEl = null;
  let badgeEl = null;
  let switching = false;

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function ensureMenu() {
    if (menuEl) return menuEl;
    menuEl = document.createElement("div");
    menuEl.id = "switchDpjpMenu";
    menuEl.style.cssText =
      "display:none;position:fixed;z-index:9999;background:#1e293b;border:1px solid #334155;" +
      "border-radius:10px;box-shadow:0 8px 30px rgba(0,0,0,.45);max-height:320px;overflow-y:auto;" +
      "min-width:250px;font-family:-apple-system,Roboto,Arial,sans-serif;";
    document.body.appendChild(menuEl);
    return menuEl;
  }

  function positionMenu() {
    const m = ensureMenu();
    const r = badgeEl.getBoundingClientRect();
    m.style.top = (r.bottom + window.scrollY + 6) + "px";
    // rata kanan terhadap badge, tapi jangan keluar layar
    let left = r.right + window.scrollX - 250;
    if (left < 8) left = 8;
    m.style.left = left + "px";
  }

  function renderMenu() {
    const m = ensureMenu();
    let html = '<div style="padding:8px 14px;font-size:11px;color:#94a3b8;border-bottom:1px solid #334155;">Beralih akun DPJP</div>';
    if (!accList.length) {
      html += '<div style="padding:12px 14px;font-size:13px;color:#94a3b8;">Memuat...</div>';
    } else {
      accList.forEach(a => {
        const cur = a.label === currentUser;
        html += '<div data-idx="' + a.i + '" style="padding:10px 14px;font-size:13px;cursor:pointer;' +
          "border-bottom:1px solid #0f172a;" +
          (cur ? "color:#38bdf8;font-weight:600;" : "color:#cbd5e1;") + '">' +
          (cur ? "✓ " : "") + esc(a.label) + "</div>";
      });
    }
    m.innerHTML = html;
    m.querySelectorAll("div[data-idx]").forEach(d => {
      d.onmouseenter = () => { d.style.background = "#334155"; };
      d.onmouseleave = () => { d.style.background = ""; };
      d.onclick = () => doSwitch(parseInt(d.dataset.idx, 10), d);
    });
  }

  async function openMenu() {
    const m = ensureMenu();
    positionMenu();
    renderMenu();
    m.style.display = "block";
    if (!accList.length) {
      try {
        const r = await fetch("/api/accounts");
        const j = await r.json();
        if (j.ok) { accList = j.accounts; renderMenu(); positionMenu(); }
      } catch (e) { /* biarkan "Memuat..." */ }
    }
  }

  function closeMenu() {
    if (menuEl) menuEl.style.display = "none";
  }

  function isOpen() {
    return menuEl && menuEl.style.display === "block";
  }

  async function doSwitch(idx, itemEl) {
    if (switching) return;
    switching = true;
    itemEl.textContent = "⏳ Beralih...";
    try {
      const r = await fetch("/api/switch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account_index: idx }),
      });
      const j = await r.json();
      if (j.ok) {
        // update kredensial utk auto-relogin client-side
        try { localStorage.setItem("sirsLastLogin", JSON.stringify({ mode: "select", account_index: idx })); } catch (e) {}
        window.location.reload();
        return; // reload; jangan reset state
      }
      itemEl.textContent = "❌ " + (j.error || "Gagal");
      itemEl.style.color = "#f87171";
    } catch (e) {
      itemEl.textContent = "❌ Network error";
      itemEl.style.color = "#f87171";
    }
    setTimeout(() => { renderMenu(); positionMenu(); switching = false; }, 2000);
  }

  function init() {
    badgeEl = document.getElementById("loginBadge");
    if (!badgeEl) return;
    badgeEl.style.cursor = "pointer";
    badgeEl.title = "Klik untuk ganti akun DPJP";
    badgeEl.addEventListener("click", (e) => {
      e.stopPropagation();
      if (isOpen()) { closeMenu(); } else { openMenu(); }
    });
    document.addEventListener("click", (e) => {
      if (isOpen() && menuEl && !menuEl.contains(e.target)) closeMenu();
    });
    // isi nama user dari /api/me (kalau halaman belum isi sendiri)
    fetch("/api/me").then(r => r.json()).then(j => {
      if (j.ok) {
        currentUser = j.user;
        if (/👤\s*(\.\.\.|…)/.test(badgeEl.textContent.trim())) {
          badgeEl.textContent = "👤 " + j.user;
        }
      }
    }).catch(() => {});
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
