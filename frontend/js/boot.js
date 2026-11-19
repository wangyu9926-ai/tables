/* =====================================================================
 * 多维表格（开放版）应用壳：
 *   游客直接进应用（只读）/ 登录弹层 / 页面注册 / 全局 toast / 全局确认弹窗
 * 页面本体在 js/pages/mtables.js（Vue 组件，走 window.mountPageApp）。
 * ===================================================================== */

const PAGES = {};
function registerPage(id, def) { PAGES[id] = def; }

function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

function toast(msg, ms) {
  const el = document.createElement('div');
  el.className = 'toast-item'; el.textContent = msg;
  document.getElementById('toast').appendChild(el);
  setTimeout(() => el.remove(), ms || 2200);
}

/* ============ 全局确认弹窗（自研组件，禁用原生 window.confirm） ============ */
const CONFIRM_CSS = [
  '.cm-mask{position:fixed;inset:0;z-index:300;background:rgba(15,23,40,.40);display:flex;align-items:center;justify-content:center;padding:24px;animation:cmFade .16s ease}',
  '.cm-modal{background:#fff;border-radius:16px;width:400px;max-width:92vw;box-shadow:0 20px 50px rgba(15,23,42,.22);padding:26px 24px 20px;display:flex;flex-direction:column;gap:8px;animation:cmPop .18s ease;border:1px solid var(--border-soft,#E6EAF0)}',
  '.cm-icon{width:42px;height:42px;border-radius:11px;background:#FEF2F2;color:var(--red,#EF4444);display:flex;align-items:center;justify-content:center}',
  '.cm-icon.cm-icon-primary{background:var(--primary-light,#EFF6FF);color:var(--primary,#2563EB)}',
  '.cm-title{font-size:16px;font-weight:700;color:var(--text,#0F172A);margin-top:4px}',
  '.cm-msg{font-size:13px;color:var(--text-3,#64748B);line-height:1.65}',
  '.cm-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:18px}',
  '.cm-btn{border:0;border-radius:9px;padding:9px 20px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;transition:all .14s}',
  '.cm-btn-cancel{background:var(--cb-strong,#EFF2F6);color:var(--text-2,#475569)}',
  '.cm-btn-cancel:hover:not(:disabled){background:#E2E6EC}',
  '.cm-btn-danger{background:var(--red,#EF4444);color:#fff;box-shadow:0 3px 10px rgba(239,68,68,.25)}',
  '.cm-btn-danger:hover:not(:disabled){background:#DC2626;box-shadow:0 4px 14px rgba(239,68,68,.34)}',
  '.cm-btn-primary{background:var(--primary,#2563EB);color:#fff;box-shadow:0 3px 10px rgba(37,99,235,.25)}',
  '.cm-btn-primary:hover:not(:disabled){background:#1D4ED8}',
  '.cm-btn:disabled{opacity:.5;cursor:not-allowed}',
  '@keyframes cmFade{from{opacity:0}to{opacity:1}}',
  '@keyframes cmPop{from{opacity:0;transform:scale(.95) translateY(6px)}to{opacity:1;transform:scale(1) translateY(0)}}'
].join('\n');

function confirmModal(opts) {
  const o = opts || {};
  if (!document.getElementById('cm-style')) {
    const st = document.createElement('style');
    st.id = 'cm-style'; st.textContent = CONFIRM_CSS;
    document.head.appendChild(st);
  }
  const danger = o.danger !== false;
  const iconSvg = o.icon || '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>';

  const mask = document.createElement('div');
  mask.className = 'cm-mask';
  mask.innerHTML =
    '<div class="cm-modal">' +
      '<div class="cm-icon' + (danger ? '' : ' cm-icon-primary') + '">' + iconSvg + '</div>' +
      '<div class="cm-title">' + esc(o.title || '确认操作') + '</div>' +
      '<div class="cm-msg">' + esc(o.msg || '') + '</div>' +
      '<div class="cm-actions">' +
        '<button type="button" class="cm-btn cm-btn-cancel" data-act="cancel">' + esc(o.cancelText || '取消') + '</button>' +
        '<button type="button" class="cm-btn ' + (danger ? 'cm-btn-danger' : 'cm-btn-primary') + '" data-act="ok">' + esc(o.okText || '确定') + '</button>' +
      '</div>' +
    '</div>';
  document.body.appendChild(mask);

  const okBtn = mask.querySelector('[data-act="ok"]');
  const cancelBtn = mask.querySelector('[data-act="cancel"]');
  let done = false;

  function close() {
    if (done) return; done = true;
    mask.remove();
    document.removeEventListener('keydown', onKey);
  }
  function onKey(e) { if (e.key === 'Escape') close(); }

  okBtn.addEventListener('click', function () {
    okBtn.disabled = true; cancelBtn.disabled = true;
    let r = null;
    try { r = o.onOk ? o.onOk() : null; }
    catch (err) { okBtn.disabled = false; cancelBtn.disabled = false; return; }
    if (r && typeof r.then === 'function') {
      r.then(function () { close(); })
       .catch(function () { okBtn.disabled = false; cancelBtn.disabled = false; });
    } else { close(); }
  });
  cancelBtn.addEventListener('click', close);
  mask.addEventListener('click', function (e) { if (e.target === mask) close(); });
  document.addEventListener('keydown', onKey);
  return { close: close, el: mask };
}
window.confirmModal = confirmModal;

/* ============ 身份：游客直接进 / 登录弹层（不刷新页面） ============ */

const GUEST_USER = { username: '', real_name: '游客', role: 'guest' };
let _loginClosing = false;
let _loginAnimGuard = null;

function _loginEls() {
  return {
    page: document.getElementById('login-page'),
    box: document.querySelector('#login-page .login-box')
  };
}

/* 动画兜底：个别环境（后台标签页节流）动画会停在第一帧，
 * 300ms 后强制清掉 animation 让元素回到基础态（完整可见），杜绝「看不见但挡点击」。 */
function _armLoginAnimGuard() {
  if (_loginAnimGuard) clearTimeout(_loginAnimGuard);
  _loginAnimGuard = setTimeout(function () {
    const { page, box } = _loginEls();
    if (page && !page.classList.contains('hidden')) {
      if (box) box.style.animation = 'none';
    }
  }, 300);
}

function showLoginOverlay() {
  const { page, box } = _loginEls();
  page.classList.remove('closing', 'hidden');
  if (box) box.style.animation = '';   /* 重新挂回入场动画 */
  /* 打开时清掉上次输入的密码 */
  document.getElementById('login-password').value = '';
  _armLoginAnimGuard();
  setTimeout(function () { document.getElementById('login-username').focus(); }, 60);
}

function hideLoginOverlay() {
  /* 带退场动画：150ms 后真正隐藏（.hidden=display:none 兜底，绝不会卡住） */
  if (_loginClosing) return;
  const { page, box } = _loginEls();
  if (page.classList.contains('hidden')) return;
  _loginClosing = true;
  page.classList.add('closing');
  setTimeout(function () {
    page.classList.add('hidden');
    page.classList.remove('closing');
    if (box) box.style.animation = 'none';
    _loginClosing = false;
  }, 150);
}
window.__showLogin = showLoginOverlay;

function applyIdentity(user) {
  const logged = !!(user && user.username);
  document.getElementById('login-entry-btn').hidden = logged;
  document.getElementById('user-box').hidden = !logged;
  document.getElementById('guest-hint').hidden = logged;
  if (logged) {
    const name = (user && (user.real_name || user.username)) || '用户';
    document.getElementById('account-user').textContent = name + '（' + (user.role === 'admin' ? '管理员' : '可编辑') + '）';
    document.getElementById('avatar').textContent = name.trim().charAt(0) || '用';
  }
}

function renderPage() {
  const container = document.getElementById('page-container');
  const def = PAGES['mtables'];
  if (def && typeof def.render === 'function') def.render(container);
  else container.innerHTML = '<div class="empty is-err">页面未注册</div>';
}

/* 登录/退出后不刷新整页：卸掉当前组件重挂，meta/权限即时生效 */
function remountPage() {
  window.unmountPageApp && window.unmountPageApp();
  renderPage();
}

/* 会话过期（api.js 检测到 401 且本地有 token）：静默切回游客，不整页刷新 */
let _switchingSession = false;
window.__sessionExpired = function () {
  if (_switchingSession) return;
  _switchingSession = true;
  localStorage.removeItem(API_CONFIG.TOKEN_KEY);
  applyIdentity(GUEST_USER);
  remountPage();
  toast('登录已过期，已切回游客模式');
  setTimeout(function () { _switchingSession = false; }, 600);
};

async function tryRestoreSession() {
  if (localStorage.getItem(API_CONFIG.TOKEN_KEY)) {
    try {
      const me = await API.authMe();
      applyIdentity(me);
      renderPage();
      return;
    } catch (e) { /* token 失效：按游客进入 */ }
  }
  applyIdentity(GUEST_USER);
  renderPage();
}

function bindLogin() {
  const btn = document.getElementById('login-btn');
  const doLogin = async () => {
    const username = document.getElementById('login-username').value.trim();
    const password = document.getElementById('login-password').value;
    if (!username || !password) { toast('填用户名和密码'); return; }
    btn.disabled = true;
    try {
      const r = await API.authLogin({ username, password });
      localStorage.setItem(API_CONFIG.TOKEN_KEY, r.token);
      hideLoginOverlay();
      applyIdentity(r.user);
      remountPage();
      toast('登录成功，现在可以编辑了');
    } catch (e) {
      toast(e.message || '登录失败');
    } finally { btn.disabled = false; }
  };
  btn.addEventListener('click', doLogin);
  document.getElementById('login-password').addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });
  document.getElementById('login-username').addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });

  /* 游客不登录，继续看；Esc 关闭弹层 */
  document.getElementById('login-entry-btn').addEventListener('click', showLoginOverlay);
  document.getElementById('login-cancel-btn').addEventListener('click', hideLoginOverlay);
  document.addEventListener('keydown', e => {
    /* 只响应真实按键：嵌入式 webview 失焦时可能合成 Escape，误关弹层 */
    if (e.key === 'Escape' && e.isTrusted) hideLoginOverlay();
  });

  const avatar = document.getElementById('avatar');
  const menu = document.getElementById('account-menu');
  avatar.addEventListener('click', () => { menu.hidden = !menu.hidden; });
  document.addEventListener('click', e => {
    if (!menu.hidden && !menu.contains(e.target) && e.target !== avatar) menu.hidden = true;
  });
  document.getElementById('logout-btn').addEventListener('click', async () => {
    try { await API.authLogout(); } catch (e) { /* 会话已失效也照常退出 */ }
    localStorage.removeItem(API_CONFIG.TOKEN_KEY);
    menu.hidden = true;
    applyIdentity(GUEST_USER);
    remountPage();
    toast('已退出，当前为游客（只读）');
  });
}

document.addEventListener('DOMContentLoaded', () => {
  bindLogin();
  tryRestoreSession();
});
