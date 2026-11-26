# -*- coding: utf-8 -*-
"""上线前全量回归：默认打本机 https://127.0.0.1:8614（服务现在默认走 HTTPS、自签证书），
地址可用环境变量 KB_TEST_BASE 改；全部走临时数据，测完物理清理。"""
import io
import json
import os
import sys
import time
import warnings

import requests

sys.path.insert(0, '.')
from auth_gate.gate import issue_token  # noqa: E402

warnings.filterwarnings('ignore', message='Unverified HTTPS request')    # 本机自签证书
BASE = os.environ.get('KB_TEST_BASE', 'https://127.0.0.1:8614')
TAG = '__pretest__'
PASS, FAIL = 0, 0
FAILS = []


def check(name, ok, detail=''):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f'PASS  {name}')
    else:
        FAIL += 1
        FAILS.append(f'{name}: {detail}')
        print(f'FAIL  {name}  {detail}')


class Api:
    def __init__(self, token=None):
        self.s = requests.Session()
        self.s.verify = False                 # 不校验本机自签证书
        if token:
            self.s.cookies.set('kb_gate', token)
            self.s.headers['Authorization'] = 'Bearer ' + token

    def req(self, method, path, **kw):
        return self.s.request(method, BASE + path, timeout=30, **kw)

    def get(self, path, **kw):
        return self.req('GET', path, **kw)

    def post(self, path, body=None, **kw):
        return self.req('POST', path, json=body, **kw)

    def patch(self, path, body=None, **kw):
        return self.req('PATCH', path, json=body, **kw)

    def delete(self, path, **kw):
        return self.req('DELETE', path, **kw)


admin = Api(issue_token(1, 'admin', 'admin'))
STAFF_UID = None
staff = Api()  # 稍后建临时用户再发 token
guest = Api()


def j(r):
    try:
        return r.json()
    except Exception:
        return {'_raw': r.text[:200]}


def cleanup():
    print('--- cleanup ---')
    try:
        d = j(admin.get('/api/meta'))
        tids = [t['id'] for t in d.get('tables', []) if t['name'].startswith(TAG)]
        for tid in tids:
            admin.delete(f'/api/tables/{tid}')
        for p in d.get('projects', []):
            if p['name'].startswith(TAG):
                admin.delete('/api/projects/' + requests.utils.quote(p['name']))
        for db in j(admin.get('/api/dashboards?project=' + TAG)).get('dashboards', []):
            admin.delete(f"/api/dashboards/{db['id']}")
        us = j(admin.get('/__auth/users')).get('users', [])
        for u in us:
            if str(u['username']).startswith(TAG):
                admin.delete(f"/__auth/users/{u['id']}")
        from backend.engine import ex, q
        tids2 = [r['id'] for r in q('SELECT id FROM kb_table WHERE name LIKE %s', (TAG + '%',))]
        if tids2:
            ph = ','.join(['%s'] * len(tids2))
            sync_ids = [r['id'] for r in q(
                f'SELECT id FROM kb_sync WHERE target_tid IN ({ph}) OR source_tid IN ({ph})',
                tuple(tids2 + tids2))]
            if sync_ids:
                sph = ','.join(['%s'] * len(sync_ids))
                ex(f'DELETE FROM kb_sync_link WHERE sync_id IN ({sph})', tuple(sync_ids))
                ex(f'DELETE FROM kb_sync WHERE id IN ({sph})', tuple(sync_ids))
            ex(f'DELETE FROM kb_apikey WHERE table_id IN ({ph})', tuple(tids2))
            ex(f'DELETE FROM kb_comment WHERE table_id IN ({ph})', tuple(tids2))
            ex(f'DELETE FROM kb_relation WHERE from_tid IN ({ph}) OR to_tid IN ({ph})', tuple(tids2 + tids2))
            ex(f'DELETE FROM kb_column WHERE table_id IN ({ph})', tuple(tids2))
            ex(f'DELETE FROM kb_row WHERE table_id IN ({ph})', tuple(tids2))
            ex(f'DELETE FROM kb_audit WHERE table_id IN ({ph})', tuple(tids2))
            ex(f'DELETE FROM kb_image WHERE table_id IN ({ph})', tuple(tids2))
            ex(f'DELETE FROM kb_table WHERE id IN ({ph})', tuple(tids2))
        ex('DELETE FROM kb_project WHERE name LIKE %s', (TAG + '%',))
        ex('DELETE FROM kb_dashboard WHERE name LIKE %s', (TAG + '%',))    # 接口删看板是软删，这里物理清掉
        ex('DELETE FROM kb_user WHERE username LIKE %s', (TAG + '%',))
        ex('DELETE FROM kb_perm WHERE scope_key LIKE %s', (TAG + '%',))
        ex("DELETE FROM kb_perm WHERE user_id NOT IN (SELECT id FROM kb_user)")
        print('cleanup done')
    except Exception as e:
        print('cleanup error:', e)


# ==================== 1. 未登录 / 登录 ====================
r = guest.get('/')
check('未登录 / 返回登录页', r.status_code == 200 and '请登录后使用' in r.text, f'{r.status_code}')
check('登录页安全头 CSP', 'content-security-policy' in {k.lower() for k in r.headers}, str(dict(r.headers))[:200])
check('登录页 XFO DENY', r.headers.get('x-frame-options', '').lower() == 'deny', r.headers.get('x-frame-options'))
r = guest.get('/api/meta')
check('未登录 /api/meta 401', r.status_code == 401, f'{r.status_code}')
r = guest.post('/__auth/login', {'username': '__nosuchuser__', 'password': 'wrongpw'})
check('假账号登录 401', r.status_code in (400, 401, 403), f'{r.status_code} {r.text[:80]}')

# ==================== 2. 项目 CRUD ====================
r = admin.post('/api/projects', {'name': TAG + '项目'})
check('新建项目', r.status_code == 200 and j(r).get('ok'), r.text[:100])
PRJ = TAG + '项目'
r = admin.patch('/api/projects/' + requests.utils.quote(PRJ), {'name': TAG + '项目2'})
check('重命名项目', r.status_code == 200 and j(r).get('name') == TAG + '项目2', r.text[:100])
PRJ = TAG + '项目2'

# ==================== 3. 表 / 列 ====================
cols = [
    {'label': '名称', 'type': 'text', 'required': True},
    {'label': '状态', 'type': 'select', 'options': ['待定', '进行中', '已完成'],
     'option_colors': {'待定': 2, '进行中': 5, '已完成': 8}},
    {'label': '金额', 'type': 'number'},
    {'label': '日期', 'type': 'date'},
    {'label': '多选', 'type': 'multiselect', 'options': ['抖音', '红书']},
    {'label': '开关', 'type': 'checkbox'},
    {'label': '隐藏列', 'type': 'text', 'hidden': True},
]
r = admin.post('/api/tables', {'name': PRJ + '·Sheet1', 'project': PRJ, 'group_name': PRJ,
                               'columns': cols})
check('建表(带列/选项色/隐藏列)', r.status_code == 200 and j(r).get('ok'), r.text[:150])
T1 = j(r)['id']
r = admin.post('/api/tables', {'name': PRJ + '·Sheet2', 'project': PRJ, 'group_name': PRJ,
                               'columns': [{'label': '匹配', 'type': 'text'},
                                           {'label': '提取', 'type': 'text'},
                                           {'label': '查找', 'type': 'formula',
                                            'expr': 'LOOKUP("%s·Sheet1","名称",{匹配},"状态")' % PRJ},
                                           {'label': '汇总', 'type': 'formula',
                                            'expr': 'SUMIF("%s·Sheet1","名称",{匹配},"金额")' % PRJ}]})
check('建表2(跨表公式)', r.status_code == 200 and j(r).get('ok'), r.text[:150])
T2 = j(r)['id']

# HTTP 写入密钥（免登录 POST 插数据）
r = admin.post('/api/tables', {'name': PRJ + '·写入', 'project': PRJ, 'group_name': PRJ,
                               'columns': [{'label': '名称', 'type': 'text', 'required': True},
                                           {'label': '数量', 'type': 'number'}]})
check('建表3(写入密钥测试)', r.status_code == 200 and j(r).get('ok'), r.text[:150])
T3 = j(r)['id']
r = admin.post(f'/api/tables/{T3}/apikeys', {'note': '回归测试'})
check('生成写入密钥', r.status_code == 200 and (j(r).get('key') or '').startswith('kbk_'), r.text[:120])
KEY = j(r)['key']
r = guest.post(f'/api/in/{T3}', {'data': {'名称': '接口A', '数量': '2万'}},
               headers={'X-KB-Token': KEY})
check('免登录单条写入', r.status_code == 200 and j(r).get('inserted') == 1, r.text[:120])
r = guest.post(f'/api/in/{T3}', {'rows': [{'名称': '接口B', '数量': 12.5}, {'名称': '接口C'}]},
               headers={'X-KB-Token': KEY})
check('免登录批量写入', r.status_code == 200 and j(r).get('inserted') == 2, r.text[:120])
t3c = next((t.get('count') for t in j(admin.get('/api/meta'))['tables'] if t['id'] == T3), None)
check('接口写入后行数即时更新（不清全部缓存）', t3c == 3, str(t3c))
r = guest.post(f'/api/in/{T3}', {'data': {'名称': 'X'}}, headers={'X-KB-Token': 'kbk_bad'})
check('错误密钥 401', r.status_code == 401, f'{r.status_code} {r.text[:80]}')
r = guest.post(f'/api/in/{T3}', {'data': {'数量': 1}}, headers={'X-KB-Token': KEY})
check('缺必填 400', r.status_code == 400 and '必填' in r.text, f'{r.status_code} {r.text[:80]}')
rr = j(admin.get(f'/api/tables/{T3}/rows'))
check('写入值按列类型规范化', bool(rr.get('rows')) and rr['rows'][0]['data'].get('数量') == 20000,
      str(rr)[:160])
kid = j(admin.get(f'/api/tables/{T3}/apikeys'))['rows'][0]['id']
admin.delete(f'/api/tables/{T3}/apikeys/{kid}')
r = guest.post(f'/api/in/{T3}', {'data': {'名称': 'D'}}, headers={'X-KB-Token': KEY})
check('作废后写入 401', r.status_code == 401, f'{r.status_code} {r.text[:80]}')

# 能力下发：字段管理 / 账号管理（都不能超过自身权限）
CAPMGR, CAPSUB = TAG + 'capmgr', TAG + 'capsub'
CAPOTHER = TAG + 'capother'
r = admin.post('/__auth/users', {'username': CAPOTHER, 'password': 'test123456', 'role': 'staff'})
check('建非本人新增的对照账号', r.status_code == 200, r.text[:100])
other_row = next(u for u in j(admin.get('/__auth/users'))['users'] if u['username'] == CAPOTHER)
r = admin.post('/__auth/users', {'username': CAPMGR, 'password': 'test123456',
                                 'real_name': '能力管理员', 'role': 'staff'})
check('建能力管理员账号', r.status_code == 200, r.text[:100])
mgr_row = next(u for u in j(admin.get('/__auth/users'))['users'] if u['username'] == CAPMGR)
mgr_uid = mgr_row['id']
r = admin.post('/__auth/grants', {'uid': mgr_uid, 'scope_type': 'project', 'scope_key': PRJ, 'role': 'editor'})
check('给能力管理员项目编辑权', r.status_code == 200, r.text[:100])
mgr = Api(issue_token(mgr_uid, CAPMGR, 'staff'))
r = mgr.post(f'/api/tables/{T1}/columns', {'label': '未下发列', 'type': 'text'})
check('未下发字段能力 403', r.status_code == 403, f'{r.status_code} {r.text[:80]}')
r = admin.patch(f'/__auth/users/{mgr_uid}', {'can_fields': True, 'can_users': True})
check('管理员下发字段/账号能力', r.status_code == 200, r.text[:100])
r = mgr.post(f'/api/tables/{T1}/columns', {'label': '能力列', 'type': 'text'})
check('下发后可在编辑表加列', r.status_code == 200, r.text[:100])
capcol = j(r).get('id')
r = mgr.delete(f'/api/tables/{T1}/columns/{capcol}')
check('下发后可在编辑表删列', r.status_code == 200, r.text[:100])
ul = j(mgr.get('/__auth/users'))['users']
check('能力管理员未建下级时只看到自己', [u['username'] for u in ul] == [CAPMGR], str(ul)[:160])
r = mgr.post('/__auth/users', {'username': CAPSUB, 'password': 'test123456', 'role': 'admin'})
check('能力管理员可建下级账号', r.status_code == 200, r.text[:100])
sub_row = next(u for u in j(admin.get('/__auth/users'))['users'] if u['username'] == CAPSUB)
check('下级账号强制 staff 且无能力', sub_row['role'] == 'staff' and not sub_row['can_fields'], str(sub_row)[:160])
ul = j(mgr.get('/__auth/users'))['users']
check('能力管理员只看到自己和自己新增的', {u['username'] for u in ul} == {CAPMGR, CAPSUB}, str(ul)[:160])
r = mgr.patch(f"/__auth/users/{other_row['id']}", {'status': 0})
check('不能操作非自己新增的账号 403', r.status_code == 403, f'{r.status_code} {r.text[:80]}')
sub = Api(issue_token(sub_row['id'], CAPSUB, 'staff'))
submeta = j(sub.get('/api/meta'))
check('新建下级默认无项目权限', not any(p['name'] == PRJ for p in submeta.get('projects', [])), str(submeta.get('projects'))[:160])
r = mgr.post('/__auth/grants', {'uid': sub_row['id'], 'scope_type': 'project', 'scope_key': PRJ, 'role': 'viewer'})
check('创建者可给下级授项目只读', r.status_code == 200, f'{r.status_code} {r.text[:100]}')
submeta = j(sub.get('/api/meta'))
check('下级获得项目权限', any(p['name'] == PRJ for p in submeta.get('projects', [])), str(submeta.get('projects'))[:160])
sub_t1 = next((t for t in submeta.get('tables', []) if t.get('project') == PRJ), None)
check('创建者授予的是只读', bool(sub_t1) and sub_t1.get('my_role') == 'viewer', str(sub_t1)[:160])
r = mgr.post('/__auth/grants', {'uid': sub_row['id'], 'scope_type': 'project', 'scope_key': '__capview__项目', 'role': 'editor'})
check('不能授超过自己的角色 403', r.status_code == 403, f'{r.status_code} {r.text[:80]}')
r = mgr.post('/__auth/grants', {'uid': other_row['id'], 'scope_type': 'project', 'scope_key': PRJ, 'role': 'viewer'})
check('不能给非自己新增账号授权 403', r.status_code == 403, f'{r.status_code} {r.text[:80]}')
r = mgr.patch('/__auth/users/1', {'status': 0})
check('不能操作管理员账号 403', r.status_code == 403, f'{r.status_code} {r.text[:80]}')
r = mgr.patch(f"/__auth/users/{sub_row['id']}", {'role': 'staff'})
check('不能改角色/下发能力 403', r.status_code == 403, f'{r.status_code} {r.text[:80]}')
r = admin.patch(f'/__auth/users/{mgr_uid}', {'can_fields': False})
check('管理员收回字段能力', r.status_code == 200, r.text[:100])
r = mgr.post(f'/api/tables/{T1}/columns', {'label': '收回后列', 'type': 'text'})
check('收回后字段接口 403', r.status_code == 403, f'{r.status_code} {r.text[:80]}')

# 非管理员写公式：跨表函数只能引用自己有权读的表；试算接口只对有字段能力的人开放
XTM = TAG + 'xtmgr'
r = admin.post('/__auth/users', {'username': XTM, 'password': 'test123456', 'role': 'staff'})
check('建字段能力账号', r.status_code == 200, r.text[:100])
xt_row = next(u for u in j(admin.get('/__auth/users'))['users'] if u['username'] == XTM)
admin.post('/__auth/grants', {'uid': xt_row['id'], 'scope_type': 'table', 'scope_key': str(T1), 'role': 'editor'})
r = admin.patch(f"/__auth/users/{xt_row['id']}", {'can_fields': True})
check('下发字段能力', r.status_code == 200, r.text[:100])
xtm = Api(issue_token(xt_row['id'], XTM, 'staff'))
r = xtm.post('/api/formula/eval', {'expr': '1+1', 'row': {}, 'tid': T1})
check('有字段能力可试算本表公式', r.status_code == 200 and j(r).get('ok'), f'{r.status_code} {r.text[:80]}')
XT_EXPR = f'LOOKUP("{PRJ}·Sheet2","匹配",{{名称}},"提取")'
r = xtm.post(f'/api/tables/{T1}/columns', {'label': '越权引用', 'type': 'formula', 'expr': XT_EXPR})
check('公式引用无权的表 400', r.status_code == 400 and '没有权限引用表' in r.text, f'{r.status_code} {r.text[:80]}')
r = xtm.post('/api/formula/eval', {'expr': XT_EXPR, 'row': {'名称': '张三'}, 'tid': T1})
check('试算引用无权的表被拒', r.status_code == 200 and j(r).get('ok') is False and '没有权限引用表' in r.text,
      f'{r.status_code} {r.text[:80]}')
r = xtm.post(f'/api/tables/{T1}/columns', {'label': '同表公式', 'type': 'formula', 'expr': '{金额}+1'})
check('有字段能力可建本表公式列', r.status_code == 200, f'{r.status_code} {r.text[:80]}')
admin.delete(f"/api/tables/{T1}/columns/{j(r).get('id')}")

meta = j(admin.get('/api/meta'))
t1 = next(t for t in meta['tables'] if t['id'] == T1)
names = [c['label'] for c in t1['columns']]
check('meta 列齐全', all(x in names for x in ['名称', '状态', '金额', '日期', '多选', '开关', '隐藏列']), str(names))
st = next(c for c in t1['columns'] if c['label'] == '状态')
check('选项色落库', (st.get('option_colors') or {}).get('已完成') == 8, str(st.get('option_colors')))
check('隐藏列标记', next(c for c in t1['columns'] if c['label'] == '隐藏列')['hidden'] is True, '')
check('my_role editor', t1.get('my_role') == 'editor', str(t1.get('my_role')))

r = admin.patch(f'/api/tables/{T1}/rename', {'short_name': '改名'})
check('Sheet 重命名', r.status_code == 200 and j(r).get('name') == PRJ + '·改名', r.text[:100])
r = admin.patch(f'/api/tables/{T1}', {'note': '备注一下'})
check('表备注更新', r.status_code == 200, r.text[:80])
r = admin.patch('/api/groups/rename', {'project': PRJ, 'old_name': PRJ, 'new_name': TAG + '组'})
check('分组重命名', r.status_code == 200, r.text[:100])
meta = j(admin.get('/api/meta'))
t1 = next(t for t in meta['tables'] if t['id'] == T1)
check('组名同步', t1['group_name'] == TAG + '组' and t1['name'] == TAG + '组·改名', str(t1['name']))
t2 = next(t for t in meta['tables'] if t['id'] == T2)
fexprs = [c['expr'] for c in t2['columns'] if c['type'] == 'formula']
check('公式引用同步改名', fexprs and all(TAG + '组' in (e or '') for e in fexprs), str(fexprs))

# 列 CRUD
r = admin.post(f'/api/tables/{T1}/columns', {'label': '新增列', 'type': 'text'})
check('新增列', r.status_code == 200, r.text[:80])
NEWCOL = j(r).get('id')
r = admin.patch(f'/api/tables/{T1}/columns/{NEWCOL}', {'label': '改名列', 'type': 'number'})
check('改列类型/名', r.status_code == 200, r.text[:80])
admin.patch(f'/api/tables/{T1}/columns/{NEWCOL}', {'label': '必填隐藏列', 'required': True, 'hidden': True, 'width': 160})
r = admin.patch(f'/api/tables/{T1}/columns/{NEWCOL}', {'label': '只改列名'})
nc = next(c for t in j(admin.get('/api/meta'))['tables'] if t['id'] == T1 for c in t['columns'] if c['id'] == NEWCOL)
check('只改列名不清掉必填/隐藏/列宽',
      r.status_code == 200 and nc['label'] == '只改列名' and nc['required'] and nc['hidden'] and nc.get('width') == 160,
      str({k: nc.get(k) for k in ('label', 'required', 'hidden', 'width')}))
admin.patch(f'/api/tables/{T1}/columns/{NEWCOL}', {'required': False})
r = admin.post(f'/api/tables/{T1}/columns/sort',
               {'order': [c['id'] for c in t1['columns']] + [NEWCOL], 'hidden': []})
check('列排序/显隐接口', r.status_code == 200, r.text[:80])
r = admin.delete(f'/api/tables/{T1}/columns/{NEWCOL}')
check('删列', r.status_code == 200, r.text[:80])
r = admin.post(f'/api/tables/{T1}/columns', {'label': '回环列', 'type': 'text'})
RC = j(r).get('id')
admin.delete(f'/api/tables/{T1}/columns/{RC}')
r = admin.post(f'/api/tables/{T1}/columns', {'label': '回环列', 'type': 'text'})
check('删列后重建同名列 200（原来 500）', r.status_code == 200, f'{r.status_code} {r.text[:80]}')
RC_KEY = j(r).get('key')      # 软删的同名列还占着原标识，新列的标识是「回环列_2」

# ==================== 4. 行 CRUD / 公式 ====================
def mkrow(name, status, amount, date, tags, flag):
    return {'名称': name, '状态': status, '金额': amount, '日期': date,
            '多选': tags, '开关': flag}


rows_in = [mkrow('张三', '已完成', 100, '2026-01-02', ['抖音'], True),
           mkrow('李四', '进行中', '2万', '2026-02-03', ['红书', '抖音'], False),
           mkrow('王五', '待定', None, None, None, None)]
rids = []
for d in rows_in:
    r = admin.post(f'/api/tables/{T1}/rows', {'data': d})
    check('新增行 ' + str(d['名称']), r.status_code == 200 and j(r).get('ok'), r.text[:80])
    rids.append(j(r)['id'])
r = admin.get(f'/api/tables/{T1}/rows?size=0')
d = j(r)
check('行列表 total=3', d.get('total') == 3, str(d.get('total')))
row1 = next(x for x in d['rows'] if x['id'] == rids[0])
check('金额数字落库', row1['data'].get('金额') == 100, str(row1['data'].get('金额')))
check('多选数组', row1['data'].get('多选') == ['抖音'], str(row1['data'].get('多选')))
check('勾选布尔', row1['data'].get('开关') is True, str(row1['data'].get('开关')))
check('隐藏列不出现在行视图', '隐藏列' not in row1['data'], '')
r = admin.patch(f"/api/tables/{T1}/rows/{rids[0]}", {'data': {'隐藏列': 'x' * 20001}, 'ver': row1['ver']})
check('单元格超过 2 万字 400', r.status_code == 400 and '不能超过' in r.text, f'{r.status_code} {r.text[:80]}')

# 行号间插入：before_id / after_id 必须精确插到视觉位置
r = admin.post(f'/api/tables/{T1}/rows', {'data': {'名称': '插队前'}, 'before_id': rids[1]})
check('before_id 插入成功', r.status_code == 200 and j(r).get('pos') == 2, r.text[:100])
rid_before = j(r)['id']
names = [x['data'].get('名称') for x in j(admin.get(f'/api/tables/{T1}/rows?size=0'))['rows']]
check('before_id 插到目标行之前', names[:3] == ['张三', '插队前', '李四'], str(names))
r = admin.post(f'/api/tables/{T1}/rows', {'data': {'名称': '插队后'}, 'after_id': rids[0]})
check('after_id 插入成功', r.status_code == 200 and j(r).get('pos') == 2, r.text[:100])
rid_after = j(r)['id']
names = [x['data'].get('名称') for x in j(admin.get(f'/api/tables/{T1}/rows?size=0'))['rows']]
check('after_id 插到目标行之后', names[:4] == ['张三', '插队后', '插队前', '李四'], str(names))
r = admin.post(f'/api/tables/{T1}/rows', {'data': {}, 'before_id': 999999999})
check('不存在的锚点行 404', r.status_code == 404, f'{r.status_code}')
admin.delete(f'/api/tables/{T1}/rows/{rid_before}')
admin.delete(f'/api/tables/{T1}/rows/{rid_after}')
check('删掉插入行后恢复 3 行', j(admin.get(f'/api/tables/{T1}/rows?size=0'))['total'] == 3, '')

# 拖动行调整顺序：移走再移回、offset、参数校验（sort 要始终保持连续）
order0 = [x['id'] for x in j(admin.get(f'/api/tables/{T1}/rows?size=0'))['rows']]
r = admin.post(f"/api/tables/{T1}/rows/{order0[0]}/move", {'after_id': order0[2]})
check('拖动行：移到第 3 行之后', r.status_code == 200 and j(r).get('moved') and j(r).get('pos') == 3, r.text[:100])
order1 = [x['id'] for x in j(admin.get(f'/api/tables/{T1}/rows?size=0'))['rows']]
check('拖动行：顺序正确', order1 == [order0[1], order0[2], order0[0]], str(order1))
r = admin.post(f"/api/tables/{T1}/rows/{order0[0]}/move", {'before_id': order0[1]})
check('拖动行：移回最前', r.status_code == 200 and [x['id'] for x in j(admin.get(f'/api/tables/{T1}/rows?size=0'))['rows']] == order0, r.text[:100])
r = admin.post(f"/api/tables/{T1}/rows/{order0[0]}/move", {'offset': -1})
check('拖动行：第一行再上移是空操作', r.status_code == 200 and j(r).get('moved') is False, r.text[:100])
r = admin.post(f"/api/tables/{T1}/rows/{order0[0]}/move", {'offset': 0})
check('拖动行：offset 非法 400', r.status_code == 400, f'{r.status_code}')
r = admin.post(f"/api/tables/{T1}/rows/{order0[0]}/move", {'before_id': 999999999})
check('拖动行：目标行不存在 404', r.status_code == 404, f'{r.status_code}')

ver = row1['ver']
r = admin.patch(f"/api/tables/{T1}/rows/{rids[0]}", {'data': {'状态': '进行中'}, 'ver': ver})
check('改行(乐观锁通过)', r.status_code == 200, r.text[:80])
r = admin.patch(f"/api/tables/{T1}/rows/{rids[0]}", {'data': {'状态': '已完成'}, 'ver': ver})
check('旧 ver 冲突 409', r.status_code == 409, f'{r.status_code}')
r = admin.delete(f'/api/tables/{T1}/rows/{rids[2]}')
check('删行', r.status_code == 200, r.text[:60])
check('行数变 2', j(admin.get(f'/api/tables/{T1}/rows?size=0'))['total'] == 2, '')

# 跨表公式
r = admin.post(f'/api/tables/{T2}/rows', {'data': {'匹配': '张三', '提取': 'x'}})
rid2 = j(r)['id']
d = j(admin.get(f'/api/tables/{T2}/rows?size=0'))
r2 = next(x for x in d['rows'] if x['id'] == rid2)
check('LOOKUP 跨表取值', r2['data'].get('查找') == '进行中', str(r2['data'].get('查找')))
check('SUMIF 跨表汇总', r2['data'].get('汇总') == 100, str(r2['data'].get('汇总')))
# 保存接口返回的那一行：公式值要和列表接口一致。原来算返回值时漏传项目，跨表函数用不了，
# 返回里的跨表公式是空的 —— 用户刚改一格就看到同行的跨表列变成“—”，刷新才回来（真浏览器里发现）。
r = admin.patch(f"/api/tables/{T2}/rows/{rid2}", {'data': {'提取': 'y'}, 'ver': r2['ver']})
back = j(r).get('row') or {}
check('保存接口返回的行里跨表公式有值', back.get('查找') == '进行中' and back.get('汇总') == 100, str(back)[:150])
listed = next(x['data'] for x in j(admin.get(f'/api/tables/{T2}/rows?size=0'))['rows'] if x['id'] == rid2)
check('保存接口返回的行与列表接口一致', back == listed, f'{back} / {listed}')
# 公式引用本表（COUNTIF 本表）：改的正好是被匹配的那一格，返回值也要按改后的数据算，不能用改前的索引
r = admin.post('/api/tables', {'name': PRJ + '·自引', 'project': PRJ, 'group_name': PRJ,
                               'columns': [{'label': '名称', 'type': 'text'},
                                           {'label': '同名计数', 'type': 'formula',
                                            'expr': 'COUNTIF("%s·自引","名称",{名称})' % PRJ}]})
T3 = j(r)['id']
ra = j(admin.post(f'/api/tables/{T3}/rows', {'data': {'名称': 'a'}}))['id']
rb = j(admin.post(f'/api/tables/{T3}/rows', {'data': {'名称': 'a'}}))['id']
rows3 = {x['id']: x for x in j(admin.get(f'/api/tables/{T3}/rows?size=0'))['rows']}     # 这一步把跨表索引建起来
check('引用本表的公式：同名两行各计 2', rows3[ra]['data'].get('同名计数') == 2 and rows3[rb]['data'].get('同名计数') == 2, '')
r = admin.patch(f"/api/tables/{T3}/rows/{rb}", {'data': {'名称': 'b'}, 'ver': rows3[rb]['ver']})
back = j(r).get('row') or {}
check('改了被匹配的那一格后，返回值按改后的数据算', back.get('同名计数') == 1, str(back)[:120])
after = {x['id']: x['data'] for x in j(admin.get(f'/api/tables/{T3}/rows?size=0'))['rows']}
check('改完两行各计 1', after[ra].get('同名计数') == 1 and after[rb].get('同名计数') == 1, str(after)[:160])

# 筛选 / 搜索 / 排序 / 分页
f = json.dumps({'状态': ['进行中']})
d = j(admin.get(f'/api/tables/{T1}/rows?size=0&filters=' + requests.utils.quote(f)))
check('筛选 单选=进行中', d['total'] == 2, str(d['total']))
f = json.dumps({'金额': {'op': 'gte', 'value': 100}})
d = j(admin.get(f'/api/tables/{T1}/rows?size=0&filters=' + requests.utils.quote(f)))
check('筛选 数字≥100', d['total'] == 2, str(d['total']))
f = json.dumps({'日期': {'from': '2026-01-01', 'to': '2026-01-31'}})
d = j(admin.get(f'/api/tables/{T1}/rows?size=0&filters=' + requests.utils.quote(f)))
check('筛选 日期区间', d['total'] == 1, str(d['total']))
# 高级筛选：多列条件 + 且/或（前端「高级筛选」弹窗拼出来的就是这种结构）
f = json.dumps({'logic': 'and', 'conds': [{'key': '状态', 'op': 'eq', 'value': '进行中'},
                                          {'key': '金额', 'op': 'gte', 'value': 1000}]})
d = j(admin.get(f'/api/tables/{T1}/rows?size=0&filters=' + requests.utils.quote(f)))
check('高级筛选 且：状态=进行中 且 金额≥1000', d['total'] == 1, str(d['total']))
f = json.dumps({'logic': 'or', 'conds': [{'key': '名称', 'op': 'contains', 'value': '张'},
                                         {'key': '金额', 'op': 'gte', 'value': 5000}]})
d = j(admin.get(f'/api/tables/{T1}/rows?size=0&filters=' + requests.utils.quote(f)))
check('高级筛选 或：名称含张 或 金额≥5000', d['total'] == 2, str(d['total']))
f = json.dumps({'logic': 'and', 'conds': [{'key': '隐藏列', 'op': 'empty'}]})
d = j(admin.get(f'/api/tables/{T1}/rows?size=0&filters=' + requests.utils.quote(f)))
check('高级筛选 为空（隐藏列谁都没填）', d['total'] == 2, str(d['total']))
f = json.dumps({'logic': 'and', 'conds': [{'key': '开关', 'op': 'eq', 'value': '是'}]})
d = j(admin.get(f'/api/tables/{T1}/rows?size=0&filters=' + requests.utils.quote(f)))
check('高级筛选 复选框=是', d['total'] == 1, str(d['total']))
f = json.dumps({'logic': 'and', 'conds': [{'key': '日期', 'op': 'daterange',
                                           'value': '2026-02-01', 'value2': '2026-02-28'}]})
d = j(admin.get(f'/api/tables/{T1}/rows?size=0&filters=' + requests.utils.quote(f)))
check('高级筛选 日期区间', d['total'] == 1, str(d['total']))
f = json.dumps({'logic': 'and', 'conds': [{'key': '早就删掉的列', 'op': 'contains', 'value': 'x'}]})
d = j(admin.get(f'/api/tables/{T1}/rows?size=0&filters=' + requests.utils.quote(f)))
check('高级筛选 引用了不存在的列：忽略这条，不是筛成空', d['total'] == 2, str(d['total']))

# 畸形输入只能得到 4xx，不能 500（上线前最后一轮全接口扫描修掉的那批，逐类钉住）
_q = requests.utils.quote
for raw in ('[[[]]]', '5', '"s"', 'true', 'null', '[' * 200 + ']' * 200):
    r = admin.get(f'/api/tables/{T1}/rows?size=1&filters=' + _q(raw))
    check(f'筛选参数不是对象 {_q(raw)[:16]}：不 500', r.status_code == 200, f'{r.status_code}')
for spec in (5, True, {'from': 123, 'to': {'a': 1}}, {'from': [1], 'to': ''}, ['（空）', 5]):
    r = admin.get(f'/api/tables/{T1}/rows?size=0&filters=' + _q(json.dumps({'日期': spec})))
    check(f'筛选条件类型不对 {str(spec)[:22]}：不 500', r.status_code == 200, f'{r.status_code} {r.text[:80]}')
f = json.dumps({'金额': 5})
r = admin.get(f'/api/tables/{T1}/rows?size=0&filters=' + _q(f))
check('筛选条件是数字：按不筛处理，仍返回全部行', r.status_code == 200 and j(r).get('total') == 2,
      f'{r.status_code} {r.text[:80]}')
f = json.dumps({'logic': 'and', 'conds': [{'key': ['金额'], 'op': 'eq', 'value': 1},
                                          {'key': '金额', 'op': ['gte'], 'value': 'x'},
                                          {'key': {'a': 1}, 'op': 'eq', 'value': 1}]})
r = admin.get(f'/api/tables/{T1}/rows?size=0&filters=' + _q(f))
check('高级筛选 key / op 不是文本：忽略该条件，不 500', r.status_code == 200 and j(r)['total'] == 2, f'{r.status_code}')
f = json.dumps({'logic': 'and', 'conds': [{'key': '金额', 'op': 'between', 'value': [1], 'value2': {'a': 1}}]})
r = admin.get(f'/api/tables/{T1}/rows?size=0&filters=' + _q(f))
check('高级筛选 区间值是数组 / 对象：不 500', r.status_code == 200, f'{r.status_code}')
for lim in ('-1', '99999999999999999999'):
    r = admin.get(f'/api/tables/{T1}/audit?limit={lim}')
    check(f'变更历史 limit={lim[:12]}：不 500', r.status_code == 200, f'{r.status_code}')
r = admin.get(f'/api/tables/{T1}/rows/by-ids?ids=' + _q('²,1,abc,99999999999999999999'))
check('按 ID 取行 含 ² / 超长数字：不 500', r.status_code == 200, f'{r.status_code}')
_meta = j(admin.get('/api/meta'))['tables']
_t1 = next(t for t in _meta if t['id'] == T1)
_t2 = next(t for t in _meta if t['id'] == T2)
_col1 = next(c for c in _t1['columns'] if c.get('id'))
_order0 = [c['id'] for c in sorted(_t1['columns'], key=lambda c: c.get('sort') or 0)]
_bad_tables = [(TAG + '坏列定义', [{'label': 'ok'}, None, 1]), (TAG + '超长列名', [{'label': 'x' * 300}]),
               (TAG + '非法公式', [{'label': 'f', 'type': 'formula', 'expr': 5}]),
               (TAG + '公式引用不存在的列', [{'label': 'f', 'type': 'formula', 'expr': '{不存在}*2'}]),
               (TAG + '公式引用不存在的表', [{'label': '名称', 'type': 'text'},
                                          {'label': 'f', 'type': 'formula', 'expr': 'COUNTIF("没有这张表","名称",{名称})'}])]
for _n, _c in _bad_tables:
    r = admin.post('/api/tables', {'name': _n, 'project': PRJ, 'columns': _c})
    check(f'建表列定义不合法（{_n[len(TAG):]}）→ 400', r.status_code == 400, f'{r.status_code} {r.text[:80]}')
    check(f'……没有留下空壳表（{_n[len(TAG):]}）', not any(t['name'] == _n for t in j(admin.get('/api/meta'))['tables']), _n)
r = admin.post('/api/tables', {'name': 'x' * 300})
check('表名超长 → 400', r.status_code == 400, f'{r.status_code}')
r = admin.post('/api/projects', {'name': 'x' * 300})
check('项目名超长 → 400', r.status_code == 400, f'{r.status_code}')
r = admin.post('/api/projects', {'name': TAG + '图标超长', 'icon': '📄' * 20})
check('项目图标超长 → 400', r.status_code == 400, f'{r.status_code}')
r = admin.patch(f'/api/tables/{T2}', {'name': _t1['name']})
check('把表名改成已有名字 → 409', r.status_code == 409, f'{r.status_code} {r.text[:80]}')
r = admin.patch(f'/api/tables/{T2}', {'name': 'y' * 300})
check('表改名超长 → 400', r.status_code == 400, f'{r.status_code}')
r = admin.post(f'/api/tables/{T1}/columns/sort', {'order': ['a', None, {}, 99999999999999999999], 'hidden': ['x', None]})
_order1 = [c['id'] for c in sorted(next(t for t in j(admin.get('/api/meta'))['tables'] if t['id'] == T1)['columns'],
                                   key=lambda c: c.get('sort') or 0)]
check('列排序含非整数 → 200 且列顺序不变', r.status_code == 200 and _order0 == _order1, f'{r.status_code} {_order1}')
r = admin.post('/api/groups/sort', {'project': PRJ, 'groups': [{'a': 1}, 1, 'x']})
check('分组排序含非文本 → 200', r.status_code == 200, f'{r.status_code} {r.text[:80]}')
r = admin.post(f'/api/groups/{_q("x")}/sort', {'tids': ['a', None, {}, T1]})
check('Sheet 排序含非整数 → 200', r.status_code == 200, f'{r.status_code} {r.text[:80]}')
for w in (99999999999999999999, -40000):
    r = admin.patch(f'/api/tables/{T1}/columns/{_col1["id"]}', {'label': _col1['label'], 'width': w})
    check(f'列宽越界 {str(w)[:12]} → 不 500', r.status_code == 200, f'{r.status_code}')
_H = {'Content-Type': 'application/json'}
for raw in ('{"data":Infinity}', 'NaN', 'Infinity', '[]', 'null', '"s"'):
    r = admin.req('POST', f'/api/tables/{T1}/rows', data=raw, headers=_H)
    check(f'行接口请求体不合法 {_q(raw)[:20]} → 4xx', 400 <= r.status_code < 500, f'{r.status_code} {r.text[:60]}')
for raw in ('{"data":{"金额":NaN}}', '{"data":{"金额":Infinity}}'):
    r = admin.req('POST', f'/api/tables/{T1}/rows', data=raw, headers=_H)
    check(f'数字单元格是 NaN / Infinity {_q(raw)[9:20]} → 400（不悄悄存成空行）', r.status_code == 400, f'{r.status_code} {r.text[:60]}')
r = admin.req('POST', f'/api/tables/{T1}/columns', data='{"label":"c","type":"select","options":[Infinity]}', headers=_H)
check('列选项含 Infinity → 4xx', 400 <= r.status_code < 500, f'{r.status_code}')
r = admin.req('PATCH', f'/api/tables/{T1}/rows/1', data='{"data":', headers=_H)
check('请求体 JSON 截断 → 4xx', 400 <= r.status_code < 500, f'{r.status_code}')
_XFF = {'X-Forwarded-For': '203.0.113.250'}        # 失败登录记在测试 IP 上，不占本机 IP 的失败次数
for body in ({'username': {'a': 1}, 'password': 'x'}, {'username': ['x'], 'password': 'x'},
             {'username': 5, 'password': 'x'}, {'username': '__nosuchuser__', 'password': {'a': 1}},
             {'username': '__nosuchuser__', 'password': 5}, {'username': '__nosuchuser__', 'password': [1]}):
    r = guest.post('/__auth/login', body, headers=_XFF)
    check(f'登录接口账号 / 密码不是文本 {str(body)[:26]}：不 500', r.status_code in (400, 401, 429), f'{r.status_code}')
COMBO = TAG + 'combo'
admin.post('/__auth/users', {'username': COMBO, 'password': 'test123456', 'role': 'staff'})
combo_id = next((u['id'] for u in j(admin.get('/__auth/users'))['users'] if u['username'] == COMBO), None)
check('建临时账号（组合更新用）', combo_id is not None, str(combo_id))
r = admin.patch(f'/__auth/users/{combo_id}', {'password': 'test654321', 'status': 0})
check('重置密码 + 停用 同一个请求 → 200', r.status_code == 200, f'{r.status_code} {r.text[:80]}')
r = admin.patch(f'/__auth/users/{combo_id}', {'password': 'test654321', 'status': 1, 'role': 'staff'})
check('重置密码 + 改角色 + 启用 同一个请求 → 200', r.status_code == 200, f'{r.status_code} {r.text[:80]}')
for body in ({'status': 'x'}, {'status': [0]}, {'status': 1.5}, {'status': 2}, {'password': ['12345678']},
             {'password': {'a': 1}}, {'password': 1234567890}, {'real_name': ['r']}):
    r = admin.patch(f'/__auth/users/{combo_id}', body)
    check(f'改账号字段类型不对 {str(body)[:24]} → 400', r.status_code == 400, f'{r.status_code} {r.text[:60]}')
for body in ({'username': ['x'], 'password': '12345678'}, {'username': {'a': 1}, 'password': '12345678'},
             {'username': '__pretest__pw', 'password': ['12345678']}, {'username': '__pretest__pw', 'password': 12345678},
             {'username': '__pretest__pw', 'password': '12345678', 'real_name': ['r']}):
    r = admin.post('/__auth/users', body)
    check(f'建账号字段类型不对 {str(body)[:28]} → 400', r.status_code == 400, f'{r.status_code} {r.text[:60]}')
r = admin.get('/__auth/grants?uid=' + _q('²'))
check('授权查询 uid=² → 400', r.status_code == 400, f'{r.status_code}')
check('……畸形探测没有留下多余账号', not any(u['username'] == '__pretest__pw' for u in j(admin.get('/__auth/users'))['users']), '')
_buf = io.BytesIO()
try:
    import openpyxl
    _wb = openpyxl.Workbook()
    _wb.active.append(['名称'])
    _wb.active.append(['甲'])
    _wb.save(_buf)
    for cfg in (json.dumps({'target': 'new', 'project': PRJ, 'name': TAG + '导入坏配置', 'header_row': 'abc'}),
                json.dumps({'target': 'table', 'table_id': 'abc'}),
                json.dumps({'target': 'new', 'project': PRJ, 'name': 'x' * 300}),
                json.dumps({'target': 'new', 'project': PRJ, 'name': TAG + '导入坏列',
                            'columns': [{'source': 0, 'label': 'x' * 300}]})):
        r = admin.req('POST', '/api/import/commit',
                      files={'file': ('x.xlsx', _buf.getvalue(), 'application/octet-stream')}, data={'config': cfg})
        check(f'导入配置不合法 {cfg[10:30]} → 400', r.status_code == 400, f'{r.status_code} {r.text[:60]}')
    check('……导入没有留下空壳表',
          not any(t['name'].startswith(TAG + '导入坏') for t in j(admin.get('/api/meta'))['tables']), '')
except ImportError:
    print('SKIP  导入畸形配置（环境里没有 openpyxl）')
d = j(admin.get(f'/api/tables/{T1}/rows?size=0&q=李四'))
check('搜索关键词', d['total'] == 1, str(d['total']))
d = j(admin.get(f'/api/tables/{T1}/rows?size=1&page=2'))
check('分页 size=1 page=2 有数据', len(d['rows']) == 1, str(len(d['rows'])))
d = j(admin.get(f'/api/tables/{T1}/rows?size=0&sort=金额&dir=desc'))
check('排序 金额 desc', (d['rows'][0]['data'].get('金额') or 0) >= (d['rows'][-1]['data'].get('金额') or 0), '')

# 公式试算
r = admin.post('/api/formula/eval', {'expr': 'ROUND({金额}/2,1)', 'row': {'金额': 3}, 'tid': T1})
check('公式试算', r.status_code == 200 and j(r).get('value') == 1.5, r.text[:100])
# 会卡死整个服务的正则：试算不卡、建列被拒
r = admin.post('/api/formula/eval', {'expr': 'REGEXEXTRACT("' + 'a' * 40 + 'b","(a+)+$")', 'row': {}, 'tid': T1})
check('灾难性回溯的正则：试算不卡并提示', r.status_code == 200 and j(r).get('ok') is False and '正则有风险' in r.text,
      f'{r.status_code} {r.text[:80]}')
r = admin.post(f'/api/tables/{T1}/columns', {'label': '坏正则', 'type': 'formula', 'expr': 'REGEXEXTRACT({名称},"(a|aa)+$")'})
check('灾难性回溯的正则不能建成公式列 400', r.status_code == 400 and '正则有风险' in r.text, f'{r.status_code} {r.text[:80]}')
r = admin.post('/api/formula/eval', {'expr': 'REPT("ab",100000000000)', 'row': {}, 'tid': T1})
check('REPT 结果有上限', r.status_code == 200 and isinstance(j(r).get('value'), str) and len(j(r)['value']) <= 20000,
      r.text[:60])
r = admin.post(f'/api/tables/{T1}/columns', {'label': '正常正则', 'type': 'formula',
                                             'expr': 'IFERROR(REGEXEXTRACT({名称},"[0-9]{2,}"),"#N/A")'})
check('现网用到的正常正则不受影响', r.status_code == 200, f'{r.status_code} {r.text[:80]}')
REGCOL = j(r).get('key')
admin.delete(f"/api/tables/{T1}/columns/{j(r).get('id')}")

# ==================== 5. 导出 / 导入 / 图片 ====================
r = admin.get(f'/api/tables/{T1}/export')
csv_text = r.text
check('导出 CSV 含表头', '名称' in csv_text and '状态' in csv_text, csv_text[:80])
check('导出 CSV 含数据', '张三' in csv_text, '')
# 导出 CSV 防公式注入：以 = 开头的文本前面加单引号，Excel 打开不会当公式执行
cv0 = next(x for x in j(admin.get(f'/api/tables/{T1}/rows?size=0'))['rows'] if x['id'] == rids[0])['ver']
admin.patch(f"/api/tables/{T1}/rows/{rids[0]}", {'data': {RC_KEY: '=HYPERLINK("http://x","点我")'}, 'ver': cv0})
csv_inj = admin.get(f'/api/tables/{T1}/export').text
check('导出 CSV 给公式开头的值加单引号', "'=HYPERLINK" in csv_inj, csv_inj[:160])

csv_body = '名称,金额,状态\n赵六,300,已完成\n钱七,1.2w,待定\n'.encode('utf-8-sig')
r = admin.post('/api/import/preview', files={'file': ('t.csv', csv_body, 'text/csv')}, data={'sheet': '', 'header_row': 1})
check('导入预览不带目标 400（原来只凭登录就能调）', r.status_code == 400, f'{r.status_code} {r.text[:80]}')
r = admin.post('/api/import/preview', files={'file': ('t.csv', csv_body, 'text/csv')},
               data={'sheet': '', 'header_row': 1, 'table_id': str(T1)})
pv = j(r)
check('导入预览', r.status_code == 200 and pv.get('total_rows') == 2, r.text[:150])
r = admin.post('/api/import/commit',
               files={'file': ('t.csv', csv_body, 'text/csv')},
               data={'config': json.dumps({'target': 'new', 'project': PRJ, 'name': TAG + '导入表',
                                           'group_name': TAG + '导入组', 'sheet': '', 'header_row': 1})})
check('导入新建表', r.status_code == 200 and j(r).get('ok'), r.text[:150])
IMP_TID = j(r).get('table_id')
d = j(admin.get(f'/api/tables/{IMP_TID}/rows?size=0'))
check('导入行数=2', d.get('total') == 2, str(d.get('total')))
qian = next((x for x in d['rows'] if x['data'].get('名称') == '钱七'), None)
check('导入金额 1.2w→12000', qian and qian['data'].get('金额') == 12000, str(qian and qian['data'].get('金额')))
imp_keys = [c['key'] for c in next(t for t in j(admin.get('/api/meta'))['tables'] if t['id'] == IMP_TID)['columns']]
r = admin.post(f'/api/import/commit',
               files={'file': ('t.csv', csv_body, 'text/csv')},
               data={'config': json.dumps({'target': 'table', 'table_id': IMP_TID, 'sheet': '',
                                           'header_row': 1, 'mode': 'append', 'mapping': imp_keys})})
check('导入追加', r.status_code == 200 and j(r).get('ok'), r.text[:120])
check('追加后 4 行', j(admin.get(f'/api/tables/{IMP_TID}/rows?size=0'))['total'] == 4, '')
r = admin.post('/api/import/commit',
               files={'file': ('t.csv', ('名称\n' + 'x' * 20001 + '\n').encode('utf-8-sig'), 'text/csv')},
               data={'config': json.dumps({'target': 'table', 'table_id': IMP_TID, 'sheet': '',
                                           'header_row': 1, 'mode': 'append', 'mapping': ['名称']})})
check('导入的单元格超过 2 万字 400', r.status_code == 400 and '超过' in r.text, f'{r.status_code} {r.text[:80]}')
check('超限导入没有写入任何行', j(admin.get(f'/api/tables/{IMP_TID}/rows?size=0'))['total'] == 4, '')

import base64
png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PzWvWQAAAABJRU5ErkJggg==')
r = admin.post('/api/upload/image', files={'file': ('p.png', png, 'image/png')}, data={'tid': str(T1)})
check('上传图片', r.status_code == 200 and j(r).get('ok'), r.text[:120])
IMG_ID = j(r).get('id')
r = admin.get(f'/api/tables/{T1}/images/{IMG_ID}')
check('读取图片', r.status_code == 200 and r.headers.get('content-type', '').startswith('image/'), f'{r.status_code}')

# ==================== 6. 审计 / 关联 ====================
d = j(admin.get(f'/api/tables/{T1}/audit?limit=200'))
acts = [a['action'] for a in d.get('items', d.get('audit', d.get('rows', [])))]
check('审计含 create_row/update_row/delete_row',
      all(a in acts for a in ('create_row', 'update_row', 'delete_row')), str(set(acts))[:150])
# 关联接口：对方表也要有读权限；行必须属于当前表（原来两边都不查，可借它读无权的表）
from backend.engine import ex as _ex  # noqa: E402
rel_id = _ex("INSERT INTO kb_relation(from_tid,from_field,to_tid,to_field) VALUES(%s,%s,%s,%s)",
             (T1, '名称', T2, '匹配'))
try:
    g = j(admin.get(f'/api/related?tid={T1}&row_id={rids[0]}')).get('groups', [])
    check('关联接口返回对方表的行', any(x['table_id'] == T2 for x in g), str(g)[:160])
    rel_rows = next(x for x in g if x['table_id'] == T2)['rows']      # 对方表里的跨表公式同样要有值
    check('关联数据里对方表的跨表公式有值', bool(rel_rows) and all(
        x['data'].get('查找') == '进行中' and x['data'].get('汇总') == 100 for x in rel_rows), str(rel_rows[:1])[:200])
    g = j(xtm.get(f'/api/related?tid={T1}&row_id={rids[0]}')).get('groups', [])
    check('没有对方表权限就看不到关联数据', not any(x['table_id'] == T2 for x in g), str(g)[:160])
    r = xtm.get(f'/api/related?tid={T1}&row_id={rid2}')
    check('不能用别的表的行来查关联 404', r.status_code == 404, f'{r.status_code}')
finally:
    _ex("DELETE FROM kb_relation WHERE id=%s", (rel_id,))

# ==================== 7. 数据分析看板 ====================
cfg = {'charts': [{'id': 'c1', 'type': 'bar', 'table_id': T1, 'dimension': '状态',
                   'metrics': [{'agg': 'count', 'field': '名称'}], 'filters': [], 'title': '状态分布'}]}
r = admin.post('/api/dashboards', {'project': PRJ, 'name': TAG + '看板', 'config': cfg})
check('新建看板', r.status_code == 200 and (j(r).get('dashboard') or {}).get('id'), r.text[:120])
DID = (j(r).get('dashboard') or {}).get('id')
r = admin.patch(f'/api/dashboards/{DID}', {'name': TAG + '看板2', 'config': cfg, 'is_page': True})
check('保存为分析页面', r.status_code == 200 and (j(r).get('dashboard') or {}).get('name') == TAG + '看板2', r.text[:120])
meta = j(admin.get('/api/meta'))
pages = [p for p in meta.get('analysis_pages', []) if p['project'] == PRJ]
check('meta 出现分析页面', len(pages) == 1 and pages[0]['name'] == TAG + '看板2', str(pages))
# 项目改名：看板和分析页按项目名归属，必须跟着走
P3 = TAG + '项目3'
r = admin.patch('/api/projects/' + requests.utils.quote(PRJ), {'name': P3})
check('带看板的项目改名', r.status_code == 200 and j(r).get('name') == P3, r.text[:100])
check('改名后看板还在',
      any(d['id'] == DID for d in j(admin.get('/api/dashboards?project=' + requests.utils.quote(P3)))['dashboards']), '')
check('改名后侧栏分析页还在', any(p['project'] == P3 for p in j(admin.get('/api/meta')).get('analysis_pages', [])),
      str(j(admin.get('/api/meta')).get('analysis_pages'))[:160])
r = admin.patch('/api/projects/' + requests.utils.quote(P3), {'name': PRJ})
check('项目名改回', r.status_code == 200 and j(r).get('name') == PRJ, r.text[:100])
check('改回后看板还在',
      any(d['id'] == DID for d in j(admin.get('/api/dashboards?project=' + requests.utils.quote(PRJ)))['dashboards']), '')
r = admin.delete(f'/api/dashboards/{DID}')
check('删除看板', r.status_code == 200, r.text[:80])
check('meta 分析页面移除',
      not [p for p in j(admin.get('/api/meta')).get('analysis_pages', []) if p['project'] == PRJ], '')

# ==================== 8. 用户 / 授权 / 权限 ====================
r = admin.post('/__auth/users', {'username': TAG + 'u1', 'real_name': '临时', 'password': 'tmp123456', 'role': 'staff'})
check('新建账号', r.status_code == 200 and j(r).get('ok'), r.text[:100])
us = j(admin.get('/__auth/users'))['users']
U1 = next(u for u in us if u['username'] == TAG + 'u1')
STAFF_UID = U1['id']
staff = Api(issue_token(STAFF_UID, TAG + 'u1', 'staff'))
check('临时账号登录态', j(staff.get('/__auth/me')).get('user', {}).get('username') == TAG + 'u1', '')
r = staff.post('/api/formula/eval', {'expr': '1+1', 'row': {}, 'tid': T1})
check('无字段能力的账号不能试算公式 403', r.status_code == 403, f'{r.status_code} {r.text[:80]}')
r = staff.post('/api/formula/eval', {'expr': '1+1', 'row': {}})
check('试算不带表 ID 400', r.status_code == 400, f'{r.status_code} {r.text[:80]}')

check('未授权看不到项目', not any(p['name'] == PRJ for p in j(staff.get('/api/meta')).get('projects', [])), '')
r = admin.post('/__auth/grants', {'uid': STAFF_UID, 'scope_type': 'project', 'scope_key': PRJ, 'role': 'viewer'})
check('项目 viewer 授权', r.status_code == 200, r.text[:80])
m = j(staff.get('/api/meta'))
t1v = next((t for t in m['tables'] if t['id'] == T1), None)
check('viewer 能看到表且只读', t1v and t1v['my_role'] == 'viewer', str(t1v and t1v['my_role']))
check('viewer 分析只读', next((p for p in m['projects'] if p['name'] == PRJ), {}).get('analysis_role') == 'viewer', '')
r = staff.patch(f"/api/tables/{T1}/rows/{rids[0]}", {'data': {'状态': '待定'}, 'ver': 99})
check('viewer 改行 403', r.status_code == 403, f'{r.status_code}')
r = staff.post('/api/tables', {'name': TAG + 'hack', 'project': PRJ})
check('viewer 建表 403', r.status_code == 403, f'{r.status_code}')
r = staff.post(f'/api/tables/{T1}/columns', {'label': 'hack', 'type': 'text'})
check('viewer 建列 403', r.status_code == 403, f'{r.status_code}')
r = staff.delete('/api/projects/' + requests.utils.quote(PRJ))
check('viewer 删项目 403', r.status_code == 403, f'{r.status_code}')
r = staff.post('/api/groups/sort', {'project': PRJ, 'groups': [TAG + '组']})
check('viewer 组排序 403', r.status_code == 403, f'{r.status_code}')
r = staff.post(f'/api/groups/{requests.utils.quote(TAG + "组")}/sort', {'tids': [T1, T2]})
check('viewer 页签排序 403', r.status_code == 403, f'{r.status_code}')
r = staff.patch(f'/api/tables/{T1}/rename', {'short_name': 'x'})
check('viewer 改页签 403', r.status_code == 403, f'{r.status_code}')

# 单元格批注：不算改数据 —— viewer 也能写，但不能删别人的；列/行不存在要挡住
r = admin.post(f'/api/tables/{T1}/comments', {'row_id': rids[0], 'col_key': '名称', 'body': '这条要复核一下'})
check('添加批注', r.status_code == 200 and j(r).get('id'), r.text[:100])
CMT_ID = j(r).get('id')
d = j(admin.get(f'/api/tables/{T1}/comments?row_id={rids[0]}&col_key=' + requests.utils.quote('名称')))
check('读取批注（带作者）', any(c['body'] == '这条要复核一下' and c['author'] for c in d.get('comments', [])), str(d)[:160])
d = j(admin.get(f'/api/tables/{T1}/comments/summary'))
check('批注汇总按格子计数', any(x['row_id'] == rids[0] and x['col_key'] == '名称' and x['n'] >= 1 for x in d.get('items', [])), str(d)[:160])
r = staff.post(f'/api/tables/{T1}/comments', {'row_id': rids[0], 'col_key': '名称', 'body': 'viewer 也能批注'})
check('只读账号可以批注', r.status_code == 200, f'{r.status_code} {r.text[:80]}')
r = staff.delete(f'/api/comments/{CMT_ID}')
check('不能删别人的批注 403', r.status_code == 403, f'{r.status_code}')
r = admin.post(f'/api/tables/{T1}/comments', {'row_id': rids[0], 'col_key': '不存在的列', 'body': 'x'})
check('批注到不存在的列 404', r.status_code == 404, f'{r.status_code}')
r = admin.post(f'/api/tables/{T1}/comments', {'row_id': rids[0], 'col_key': '名称', 'body': '   '})
check('空批注 400', r.status_code == 400, f'{r.status_code}')
r = admin.delete(f'/api/comments/{CMT_ID}')
check('删除自己的批注', r.status_code == 200, r.text[:80])
d = j(admin.get(f'/api/tables/{T1}/comments?row_id={rids[0]}&col_key=' + requests.utils.quote('名称')))
check('删除后列表里不再有它', all(c['id'] != CMT_ID for c in d.get('comments', [])), str(d)[:160])
r = admin.post('/__auth/grants', {'uid': STAFF_UID, 'scope_type': 'project', 'scope_key': PRJ, 'role': 'editor'})
check('升为 editor', r.status_code == 200, r.text[:80])
r = staff.patch(f"/api/tables/{T1}/rows/{rids[0]}", {'data': {'状态': '待定'}, 'ver': 100})
check('editor 改行 旧ver 409', r.status_code == 409, f'{r.status_code}')
cur_ver = next(x for x in j(admin.get(f'/api/tables/{T1}/rows?size=0'))['rows'] if x['id'] == rids[0])['ver']
r = staff.patch(f"/api/tables/{T1}/rows/{rids[0]}", {'data': {'状态': '待定'}, 'ver': cur_ver})
check('editor 改行成功', r.status_code == 200, r.text[:80])
check('editor 分析可写', next((p for p in j(staff.get('/api/meta'))['projects'] if p['name'] == PRJ), {})
      .get('analysis_role') == 'editor', '')
r = staff.post(f'/api/groups/{requests.utils.quote(TAG + "组")}/sort', {'tids': [T1, T2]})
check('editor 页签排序 200', r.status_code == 200, f'{r.status_code} {r.text[:60]}')
r = staff.post('/api/groups/sort', {'project': PRJ, 'groups': [TAG + '组']})
check('editor 组排序 403(仅 admin)', r.status_code == 403, f'{r.status_code}')

# ---- 第二批：跨站写请求 / 导入权限 / 限流 / 首页缓存 / 脚本策略 / 请求体类型 ----
csv_t1 = {'file': ('t.csv', csv_body, 'text/csv')}
r = staff.patch(f"/api/tables/{T1}/rows/{rids[0]}", {'data': {'状态': '待定'}, 'ver': 1},
                headers={'Sec-Fetch-Site': 'same-site', 'Origin': 'http://127.0.0.1:1'})
check('别的站点发起的写请求 403', r.status_code == 403, f'{r.status_code} {r.text[:80]}')
r = staff.post('/api/import/preview', files=csv_t1,
               data={'sheet': '', 'header_row': '1', 'table_id': str(T1)}, headers={'Sec-Fetch-Site': 'cross-site'})
check('表单提交的跨站请求同样 403', r.status_code == 403, f'{r.status_code} {r.text[:80]}')
r = staff.post('/api/import/preview', files=csv_t1,
               data={'sheet': '', 'header_row': '1', 'table_id': str(T1)}, headers={'Sec-Fetch-Site': 'same-origin'})
check('本站页面发起的表单提交正常', r.status_code == 200, f'{r.status_code} {r.text[:80]}')
r = staff.post('/__auth/grants', {'uid': STAFF_UID, 'scope_type': 'table', 'scope_key': '0', 'role': 'viewer'},
               headers={'Origin': 'http://neighbor.example'})
check('老浏览器没有 Sec-Fetch-Site 时按 Origin 拦 403', r.status_code == 403 and '不是本站' in r.text,
      f'{r.status_code} {r.text[:80]}')
r = admin.post('/__auth/users', {'username': TAG + 'xsite', 'password': 'abcdef1', 'role': 'admin'},
               headers={'Sec-Fetch-Site': 'same-site'})
check('别的站点冒充管理员建账号 403', r.status_code == 403 and '不是本站' in r.text, f'{r.status_code} {r.text[:80]}')
check('跨站请求没有建出账号', not any(u['username'] == TAG + 'xsite' for u in j(admin.get('/__auth/users'))['users']), '')

r = staff.post('/api/import/preview', files=csv_t1, data={'sheet': '', 'header_row': '1'})
check('编辑者导入预览不带目标 400', r.status_code == 400, f'{r.status_code} {r.text[:80]}')
r = staff.post('/api/import/preview', files=csv_t1, data={'sheet': '', 'header_row': '1', 'table_id': str(T1)})
check('编辑者可预览导入到自己能编辑的表', r.status_code == 200, f'{r.status_code} {r.text[:80]}')
r = staff.post('/api/import/preview', files=csv_t1, data={'sheet': '', 'header_row': '1', 'table_id': '999999999'})
check('导入预览的目标不存在 404', r.status_code == 404, f'{r.status_code}')
r = staff.post('/api/import/commit',
               files={'file': ('t.csv', '名称,越权新列\n导入甲,1\n'.encode('utf-8-sig'), 'text/csv')},
               data={'config': json.dumps({'target': 'table', 'table_id': T1, 'sheet': '', 'header_row': 1,
                                           'mode': 'append', 'mapping': ['名称', '__new__']})})
check('编辑者靠导入自动建字段 403', r.status_code == 403, f'{r.status_code} {r.text[:80]}')

r = None
for i in range(16):
    r = guest.post('/__auth/login', {'username': TAG + 'nobody', 'password': 'wrongpw'},
                   headers={'X-Forwarded-For': f'10.9.{i // 250}.{i + 1}'})
check('换着 IP 猜同一个账号也会被锁 429', r.status_code == 429, f'第 {i + 1} 次 {r.status_code}')

r = admin.get('/', headers={'Accept-Encoding': 'gzip'})
et = r.headers.get('etag')
check('首页带 ETag', bool(et), str(dict(r.headers))[:160])
r = admin.get('/', headers={'If-None-Match': et or '', 'Accept-Encoding': 'gzip'})
check('首页没变时回 304（浏览器不用重下）', r.status_code == 304 and not r.content, f'{r.status_code} {len(r.content)}')
import base64 as _b64
import hashlib as _hl
import re as _re


def csp_page(resp, name):
    """内容安全策略是“只放行带哈希的脚本”。三件事同时成立，页面才既安全又能用：
    ① 页面里每段内联脚本的哈希都在策略里（这里自己重算，不复用被测的代码）；
    ② 脚本这一项没有 unsafe-inline；
    ③ 标签上没有内联事件。③ 只靠接口测试查不出来 —— 真浏览器里那种写法会被策略拦掉，
       表现是按钮点了没反应、页面不报错（上线前实测就是在登录按钮上发现的）。"""
    html = resp.text
    csp = resp.headers.get('content-security-policy', '')
    want = set()
    for s in _re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, _re.S | _re.I):
        code = s.replace('\r\n', '\n').replace('\r', '\n').encode('utf-8')     # 浏览器先把换行统一成 LF 再算哈希
        want.add("'sha256-" + _b64.b64encode(_hl.sha256(code).digest()).decode() + "'")
    sdir = csp.split('script-src', 1)[1].split(';', 1)[0] if 'script-src' in csp else ''
    have = set(_re.findall(r"'sha256-[A-Za-z0-9+/=]+'", sdir))
    check(f'{name}：每段内联脚本的哈希都在策略里', bool(want) and want <= have,
          f'页面 {len(want)} 段脚本 / 策略 {len(have)} 个哈希: {sorted(want - have)[:1]}')
    check(f'{name}：脚本策略不再放行任意内联脚本', bool(sdir) and "'unsafe-inline'" not in sdir, sdir[:120])
    body = _re.sub(r'<script.*?</script>', '', html, flags=_re.S | _re.I)
    bad = _re.findall(r'<[a-z][^>]*\son[a-z]+\s*=|\shref\s*=\s*["\']?\s*javascript\s*:', body, _re.I)
    check(f'{name}：标签上没有内联事件（会被策略拦掉）', not bad, str(bad[:2]))
    js_bad = _re.findall(r'<[a-z][^<>]{0,400}\son[a-z]+\s*=\s*["\']', html, _re.I)
    check(f'{name}：脚本里没有拼出带内联事件的标签', not js_bad, str(js_bad[:2]))


csp_page(guest.get('/login'), '登录页')
csp_page(admin.get('/__auth/admin'), '用户管理页')
csp_page(admin.get('/'), '主页面')

# 免登录的开发入口必须拒绝启动：run.py 是明文 + 免登录，谁双击旧脚本都能直接读写数据（2026-11-26 踩过）
import subprocess as _sp
import socket as _socket
_env = dict(os.environ)
_env.pop('KB_ALLOW_NOAUTH', None)          # 显式不带放行变量，测的就是“默认拒绝”
_env['PYTHONIOENCODING'] = 'utf-8'
_p = _sp.run([sys.executable, '-B', 'run.py', '8699'], cwd='.', env=_env, capture_output=True,
             text=True, encoding='utf-8', errors='replace', timeout=30)
_out = (_p.stdout or '') + (_p.stderr or '')
check('免登录入口 run.py 拒绝启动（明文/免登录）', _p.returncode != 0 and '拒绝启动' in _out,
      f'{_p.returncode} {_out[:80]}')
_s = _socket.socket()
_s.settimeout(1)
check('免登录入口没有留下监听端口', _s.connect_ex(('127.0.0.1', 8699)) != 0, '8699 在监听')
_s.close()
r = admin.post('/__auth/users', data=json.dumps({'username': TAG + 'plain', 'password': 'abcdef1', 'role': 'staff'}),
               headers={'Content-Type': 'text/plain'})
check('非 JSON 类型的请求体不解析 400', r.status_code == 400, f'{r.status_code} {r.text[:80]}')
check('非 JSON 请求没有建出账号', not any(u['username'] == TAG + 'plain' for u in j(admin.get('/__auth/users'))['users']), '')
from backend.engine import setting_get as _sg  # noqa: E402
check('默认授权的一次性初始化标记已写入', bool(_sg('perm_seed_done')), str(_sg('perm_seed_done')))
# 改密 / 被重置后，已签发的旧登录凭证必须失效
r = admin.patch(f'/__auth/users/{STAFF_UID}', {'password': 'resetpw123456'})
check('管理员重置密码', r.status_code == 200, r.text[:80])
check('重置密码后旧登录凭证失效', j(staff.get('/__auth/me')).get('user') is None, str(j(staff.get('/__auth/me')))[:80])
staff = Api(issue_token(STAFF_UID, TAG + 'u1', 'staff', tv=1))
check('新版本凭证可用', j(staff.get('/__auth/me')).get('user', {}).get('username') == TAG + 'u1', '')
r = staff.post('/__auth/me/password', {'old_password': 'resetpw123456', 'new_password': 'ownpw123456'})
check('本人改密', r.status_code == 200, r.text[:80])
check('本人改密后旧登录凭证失效', j(staff.get('/__auth/me')).get('user') is None, str(j(staff.get('/__auth/me')))[:80])
r = admin.delete(f'/__auth/users/{STAFF_UID}')
check('删除账号', r.status_code == 200 and j(r).get('ok'), r.text[:80])
check('被删账号 me 失效', j(staff.get('/__auth/me')).get('user') is None, str(j(staff.get('/__auth/me')))[:80])

# 表/组排序（admin）
r = admin.post('/api/groups/sort', {'project': PRJ, 'groups': [TAG + '组']})
check('admin 组排序', r.status_code == 200, r.text[:60])
r = admin.post(f'/api/groups/{requests.utils.quote(TAG + "组")}/sort', {'tids': [T2, T1]})
check('admin 页签排序', r.status_code == 200, r.text[:60])

# ==================== 9. 汇总同步（子表 → 总表） ====================
ST1, ST2, TT = TAG + '同步子1', TAG + '同步子2', TAG + '同步总'
r = admin.post('/api/tables', {'name': PRJ + '·同步子1', 'project': PRJ, 'group_name': TAG + '同步组',
                               'columns': [{'label': '姓名', 'type': 'text'},
                                           {'label': '电话', 'type': 'text'},
                                           {'label': '备注', 'type': 'text'}]})
check('建汇总子表1', r.status_code == 200, r.text[:120])
ST1 = j(r)['id']
r = admin.post('/api/tables', {'name': PRJ + '·同步子2', 'project': PRJ, 'group_name': TAG + '同步组',
                               'columns': [{'label': '姓名', 'type': 'text'},
                                           {'label': '电话', 'type': 'text'},
                                           {'label': '备注', 'type': 'text'}]})
check('建汇总子表2', r.status_code == 200, r.text[:120])
ST2 = j(r)['id']
r = admin.post('/api/tables', {'name': PRJ + '·同步总', 'project': PRJ, 'group_name': TAG + '同步组',
                               'columns': [{'label': '名称', 'type': 'text'},
                                           {'label': '电话', 'type': 'text'},
                                           {'label': '来源', 'type': 'text'},
                                           {'label': '备注', 'type': 'text'}]})
check('建汇总总表', r.status_code == 200, r.text[:120])
TT = j(r)['id']
SROWS = []
for n, tel in (('同步甲', '101'), ('同步乙', '102')):
    r = admin.post(f'/api/tables/{ST1}/rows', {'data': {'姓名': n, '电话': tel, '备注': 'x'}})
    SROWS.append(j(r)['id'])
MAP = {'名称': '姓名', '电话': '电话', '来源': '$source_table', '备注': '备注'}
r = admin.post('/api/sync', {'target_tid': TT, 'source_tid': ST1, 'mapping': MAP})
check('创建汇总规则', r.status_code == 200 and j(r).get('ok'), r.text[:160])
SID1 = j(r).get('id')


def wait_sync():
    for _ in range(50):
        d = j(admin.get('/api/sync?target_tid=' + str(TT)))
        rs = d.get('rules') or []
        if rs and all(not x.get('job') or x['job'].get('status') not in ('running', 'queued')
                      for x in rs):
            return rs
        time.sleep(0.2)
    return []


def target_rows():
    return j(admin.get(f'/api/tables/{TT}/rows?size=0')).get('rows', [])


rs = wait_sync()
tr = target_rows()
check('全量汇总到总表', len(rs) == 1 and len(tr) == 2, f'rules={rs} rows={tr}')
check('汇总值原样复制', any(x['data'].get('名称') == '同步甲' and x['data'].get('电话') == '101' for x in tr),
      str(tr)[:200])
check('自动带来源表名', all(x['data'].get('来源') == PRJ + '·同步子1' for x in tr), str(tr)[:200])
r = admin.post(f'/api/tables/{ST1}/rows', {'data': {'姓名': '同步丙', '电话': '103'}})
check('子表新增行', r.status_code == 200, r.text[:100])
tr = target_rows()
check('总表自动追加', len(tr) == 3, str(tr)[:200])
src_now = j(admin.get(f'/api/tables/{ST1}/rows?size=0'))['rows']
row0 = next(x for x in src_now if x['id'] == SROWS[0])
admin.patch(f'/api/tables/{ST1}/rows/{SROWS[0]}', {'ver': row0['ver'], 'data': {'姓名': '同步甲改'}})
tr = target_rows()
check('子表修改自动更新', any(x['data'].get('名称') == '同步甲改' for x in tr), str(tr)[:200])
admin.delete(f'/api/tables/{ST1}/rows/{SROWS[1]}')
tr = target_rows()
check('子表删除自动移除', len(tr) == 2 and not any(x['data'].get('名称') == '同步乙' for x in tr), str(tr)[:200])
admin.post(f'/api/sync/{SID1}/run', {})
wait_sync()
admin.post(f'/api/sync/{SID1}/run', {})
wait_sync()
check('重复全量同步不重复', len(target_rows()) == 2, str(target_rows())[:200])
r = admin.post(f'/api/tables/{ST2}/rows', {'data': {'姓名': '同步丁', '电话': '104'}})
r = admin.post('/api/sync', {'target_tid': TT, 'source_tid': ST2, 'mapping': MAP})
check('第二张子表也能汇总', r.status_code == 200, r.text[:120])
SID2 = j(r)['id']
wait_sync()
tr = target_rows()
check('多子表汇总到同一总表', len(tr) == 3 and any(x['data'].get('来源') == PRJ + '·同步子2' for x in tr),
      str(tr)[:240])
admin.delete(f'/api/sync/{SID1}')
tr = target_rows()
check('删除规则清掉该子表副本', len(tr) == 1 and tr[0]['data'].get('来源') == PRJ + '·同步子2', str(tr)[:200])
r = admin.post('/api/sync', {'target_tid': TT, 'source_tid': TT, 'mapping': {'名称': '名称'}})
check('同一张表不能汇总', r.status_code == 400, f'{r.status_code} {r.text[:120]}')
nonadmin = Api(issue_token(other_row['id'], CAPOTHER, 'staff'))
r = nonadmin.post('/api/sync', {'target_tid': TT, 'source_tid': ST2, 'mapping': MAP})
check('非 admin 不能配置汇总', r.status_code == 403, f'{r.status_code} {r.text[:100]}')

# ---- 双向：总表改"镜像行"要回写子表；总表自己新增的行不动子表；总表删镜像行子表一起删 ----
mrows = target_rows()
mrow = next(x for x in mrows if x['data'].get('来源') == PRJ + '·同步子2')
r = admin.patch(f'/api/tables/{TT}/rows/{mrow["id"]}',
                {'ver': mrow['ver'], 'data': {'名称': '总表改回子表', '电话': '999'}})
check('总表改镜像行 200 并回写子表', r.status_code == 200 and j(r).get('synced_back') == 1, r.text[:160])
src_rows = j(admin.get(f'/api/tables/{ST2}/rows?size=0'))['rows']
check('子表那行跟着总表改了', any(x['data'].get('姓名') == '总表改回子表' and x['data'].get('电话') == '999'
                              for x in src_rows), str(src_rows)[:200])
n_child = len(src_rows)
r = admin.post(f'/api/tables/{TT}/rows', {'data': {'名称': '只在总表', '电话': '1'}})
check('总表直接新增行 200', r.status_code == 200, r.text[:100])
check('总表新增的行不同步子表', len(j(admin.get(f'/api/tables/{ST2}/rows?size=0'))['rows']) == n_child, '')
mrows = target_rows()
mrow = next(x for x in mrows if x['data'].get('来源') == PRJ + '·同步子2')
r = admin.delete(f'/api/tables/{TT}/rows/{mrow["id"]}')
check('总表删镜像行 200', r.status_code == 200, r.text[:80])
left = j(admin.get(f'/api/tables/{ST2}/rows?size=0'))['rows']
check('总表删镜像行，子表那行一起软删', not any(x['data'].get('姓名') == '总表改回子表' for x in left), str(left)[:160])
from backend.engine import q as _q  # noqa: E402
admin.get(f'/api/tables/{ST2}/audit?limit=5')      # 审计是服务端后台批量写的：先让它把队列落盘再查库（本机库快，不刷会读到空）
check('回写子表记了审计 sync_back', bool(_q('SELECT id FROM kb_audit WHERE table_id=%s AND action=%s LIMIT 1',
                                          (ST2, 'sync_back'))), '')

# ---- 变更通知：轮询接口 + SSE 握手 ----
r = admin.get(f'/api/tables/{TT}/changes?since=0')
_d = j(r)
check('变更轮询接口返回 seq', r.status_code == 200 and isinstance(_d.get('seq'), int)
      and isinstance(_d.get('events'), list), r.text[:120])
import subprocess as _sp2  # noqa: E402
_bearer = (admin.s.headers.get('Authorization') or '').replace('Bearer ', '')
_p = _sp2.run(['curl', '-sN', '--max-time', '3', '-k', '-H', 'Authorization: Bearer ' + _bearer,
               BASE + f'/api/tables/{TT}/events'], capture_output=True, text=True, timeout=12)
check('SSE 握手返回 hello', 'event: hello' in (_p.stdout or ''), (_p.stdout or _p.stderr)[:120])
# 最小化刷新用的"按 ID 取行"：只返回存在的行，结构跟列表接口一致（公式算好、隐藏列不出现）
r = admin.get(f'/api/tables/{T1}/rows/by-ids?ids={rids[0]},{rids[1]},999999999')
_d = j(r)
check('按 ID 取行只返回存在的行', r.status_code == 200 and len(_d.get('rows', [])) == 2
      and {x['id'] for x in _d['rows']} == {rids[0], rids[1]}
      and all('ver' in x and 'data' in x for x in _d['rows']), r.text[:160])

# ==================== 10. 删表 / 删项目 ====================
r = admin.delete(f'/api/tables/{T2}')
check('删表', r.status_code == 200, r.text[:60])
check('meta 少一张表', not any(t['id'] == T2 for t in j(admin.get('/api/meta'))['tables']), '')
r = admin.delete('/api/projects/' + requests.utils.quote(PRJ))
check('删项目', r.status_code == 200 and j(r).get('ok'), r.text[:80])
meta = j(admin.get('/api/meta'))
check('项目消失', not any(p['name'] == PRJ for p in meta['projects']), '')
check('项目下表全部隐藏', not any(t['id'] == T1 for t in meta['tables']), '')

# 空项目也要能出现在管理员授权目录；改名后授权跟着走，删项目清授权
PP = TAG + '授权项目'
r = admin.post('/api/projects', {'name': PP})
check('新建空项目', r.status_code == 200, r.text[:80])
r = admin.get('/__auth/catalog')
check('空项目出现在授权目录', PP in j(r).get('projects', []), str(j(r).get('projects'))[:160])
r = admin.post('/__auth/grants', {'uid': mgr_uid, 'scope_type': 'project', 'scope_key': PP, 'role': 'editor'})
check('给空项目授项目权限', r.status_code == 200, r.text[:80])
r = admin.patch('/api/projects/' + requests.utils.quote(PP), {'name': PP + '2'})
check('空项目改名', r.status_code == 200, r.text[:80])
gs = j(admin.get(f'/__auth/grants?uid={mgr_uid}')).get('grants', [])
check('改名后项目授权跟随',
      any(g['scope_type'] == 'project' and g['scope_key'] == PP + '2' and g['role'] == 'editor' for g in gs),
      str(gs)[:160])
r = admin.delete('/api/projects/' + requests.utils.quote(PP + '2'))
check('删除空项目', r.status_code == 200, r.text[:80])
gs = j(admin.get(f'/__auth/grants?uid={mgr_uid}')).get('grants', [])
check('删项目后项目授权清除', not any(
    g['scope_type'] == 'project' and g['scope_key'] in (PP, PP + '2') for g in gs), str(gs)[:160])

# ==================== 10. 员工侧越权（admin 自身不受限） ====================
r = admin.post('/api/tables', {'name': TAG + 'tmp', 'project': PRJ})
check('admin 对已删项目建表 404/403', r.status_code in (400, 403, 404), f'{r.status_code}')

print('\n========== 结果 ==========')
print(f'PASS {PASS}  FAIL {FAIL}')
for f_ in FAILS:
    print('  -', f_)
cleanup()
sys.exit(1 if FAIL else 0)
