/* =====================================================================
 * 多维表格（开放版）API 服务层
 * 统一 request：可选 Bearer JWT（游客无 token 也可读）。
 * 401 处理：有 token=会话过期（清掉刷新回游客）；无 token=游客触发写操作（弹登录层）。
 * ===================================================================== */

const API_CONFIG = {
  /* 跟随访问来源；file:// 直开时回退本地 8002 */
  BASE_URL: (location.protocol === 'http:' || location.protocol === 'https:')
    ? location.origin
    : 'http://localhost:8002',
  TIMEOUT: 30000,
  LONG_TIMEOUT: 600000,   // Excel 导入万行级
  TOKEN_KEY: 'mt_token'
};

const LONG_PATHS = ['/api/mtables/tables/import'];

async function request(path, { method = 'GET', params = {}, body = null, options = {} } = {}) {
  const url = new URL(API_CONFIG.BASE_URL + path, location.href);
  if (method === 'GET' && params) Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .forEach(([k, v]) => url.searchParams.set(k, v));
  const token = localStorage.getItem(API_CONFIG.TOKEN_KEY);

  const doFetch = async () => {
    const ctrl = new AbortController();
    const pathTimeout = LONG_PATHS.some(p => path.startsWith(p)) ? API_CONFIG.LONG_TIMEOUT : API_CONFIG.TIMEOUT;
    const timer = setTimeout(() => ctrl.abort(), pathTimeout);
    try {
      const res = await fetch(url, {
        method,
        signal: ctrl.signal,
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { 'Authorization': 'Bearer ' + token } : {})
        },
        ...(method !== 'GET' && body ? { body: JSON.stringify(body) } : {})
      });
      if (res.status === 401 && !path.startsWith('/api/auth/login')) {
        if (token) {
          /* 会话过期：清 token，静默切回游客（不整页刷新） */
          if (window.__sessionExpired) window.__sessionExpired();
          else location.reload();
          throw new Error('登录已过期');
        }
        /* 游客触发登录才能用的操作：弹登录层 */
        if (window.__showLogin) window.__showLogin();
        throw new Error('请先登录后再操作');
      }
      if (!res.ok) {
        const text = await res.text().catch(() => '');
        let detail = text;
        try { detail = JSON.parse(text).detail || text; } catch (e) { /* 原样 */ }
        throw new Error(detail || ('HTTP ' + res.status));
      }
      return await res.json();
    } finally {
      clearTimeout(timer);
    }
  };

  try {
    return await doFetch();
  } catch (err) {
    if (err.name === 'AbortError' || (err instanceof TypeError && err.message.includes('Failed to fetch'))) {
      await new Promise(r => setTimeout(r, 500));
      return await doFetch();
    }
    throw err;
  }
}

const API = {
  get: (path, params, options) => request(path, { params, options }),
  post: (path, body, options) => request(path, { method: 'POST', body, options }),
  patch: (path, body, options) => request(path, { method: 'PATCH', body, options }),
  delete: (path, params) => request(path, { method: 'DELETE', params }),

  /* ============ 认证 ============ */
  authLogin:  (body) => API.post('/api/auth/login', body),
  authMe:     ()     => API.get('/api/auth/me'),
  authLogout: ()     => API.post('/api/auth/logout', {}),

  /* ============ 多维表格 ============ */
  mtMeta:        ()          => API.get('/api/mtables/meta'),
  mtTables:      ()          => API.get('/api/mtables/tables'),
  mtTable:       (tid)       => API.get('/api/mtables/tables/' + tid),
  mtTableCreate: (body)      => API.post('/api/mtables/tables', body),
  mtImport:      (body)      => API.post('/api/mtables/tables/import', body),   // body: {name, note, file_b64}（.xlsx 的 base64）
  mtTableDelete: (tid)       => API.delete('/api/mtables/tables/' + tid),
  mtColCreate:   (tid, body) => API.post('/api/mtables/tables/' + tid + '/columns', body),
  mtColUpdate:   (tid, cid, body) => API.patch('/api/mtables/tables/' + tid + '/columns/' + cid, body),
  mtColDelete:   (tid, cid)  => API.delete('/api/mtables/tables/' + tid + '/columns/' + cid),
  mtRows:        (tid, p)    => API.get('/api/mtables/tables/' + tid + '/rows', p),
  mtRowCreate:   (tid, body) => API.post('/api/mtables/tables/' + tid + '/rows', body),
  mtRowUpdate:   (tid, rid, body) => API.patch('/api/mtables/tables/' + tid + '/rows/' + rid, body),
  mtRowDelete:   (tid, rid)  => API.delete('/api/mtables/tables/' + tid + '/rows/' + rid),
  mtAudit:       (tid, p)    => API.get('/api/mtables/tables/' + tid + '/audit', p),
  mtDbView:      (tid)       => API.get('/api/mtables/tables/' + tid + '/dbview'),
  mtDbViewCreate:(tid)       => API.post('/api/mtables/tables/' + tid + '/dbview', {}),
  mtDbViewDrop:  (tid)       => API.delete('/api/mtables/tables/' + tid + '/dbview'),

  /* ============ 用户管理（仅 admin） ============ */
  mtUsers:       ()          => API.get('/api/mtables/users'),
  mtUserCreate:  (body)      => API.post('/api/mtables/users', body),
  mtUserPatch:   (uid, body) => API.patch('/api/mtables/users/' + uid, body),
  mtUserDelete:  (uid)       => API.delete('/api/mtables/users/' + uid),

  /* ============ HTTP 写入密钥（仅 admin） ============ */
  mtApiKeys:     (tid)       => API.get('/api/mtables/tables/' + tid + '/apikeys'),
  mtApiKeyCreate:(tid, body) => API.post('/api/mtables/tables/' + tid + '/apikeys', body),   // → {key: 明文，只显示一次}
  mtApiKeyRevoke:(tid, kid)  => API.delete('/api/mtables/tables/' + tid + '/apikeys/' + kid)
};

window.API = API;
