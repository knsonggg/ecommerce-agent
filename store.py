"""SQLite persistence. Connections and transactions never span a model request."""
import hashlib
import hmac
import json
import math
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager, closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class Problem(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def text(value, label, maximum=200, optional=False):
    if not isinstance(value, str) or len(value.strip()) > maximum or (not optional and not value.strip()):
        raise Problem(400, f'{label}格式错误或超过 {maximum} 字')
    return value.strip()


def number(value, label, integer=False, maximum=100000000):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= maximum or (integer and int(value) != value):
        raise Problem(400, f'{label}必须是范围内的非负数')
    return int(value) if integer else round(value, 2)


def strings(value, label):
    if not isinstance(value, list) or len(value) > 30:
        raise Problem(400, f'{label}必须是最多 30 条的文本列表')
    return [text(v, label, 300) for v in value]


def validate(kind, value):
    if not isinstance(value, dict):
        raise Problem(400, '记录必须是对象')
    v = value
    if kind not in ('products', 'orders', 'policies'):
        raise Problem(404, '未知数据类型')
    identifier = text(v.get('id'), '编号', 60)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', identifier):
        raise Problem(400, '编号只能使用字母、数字、下划线和短横线')
    out = {'id': identifier}
    if kind == 'products':
        for key, label in [('name', '商品名称'), ('category', '分类')]:
            out[key] = text(v.get(key), label, 100)
        for key in ('price', 'cost', 'stock', 'sales_30d', 'returns_30d'):
            out[key] = number(v.get(key), key, key in ('stock', 'sales_30d', 'returns_30d'))
        if out['price'] <= 0 or out['returns_30d'] > out['sales_30d']:
            raise Problem(400, '售价须大于零，退货数不可高于销量')
        out['tags'] = strings(v.get('tags', []), '标签')
        out['features'] = strings(v.get('features', []), '卖点')
        out['active'] = v.get('active', True)
        if not isinstance(out['active'], bool):
            raise Problem(400, '上架状态须为布尔值')
    elif kind == 'orders':
        out['status'] = text(v.get('status'), '订单状态', 30)
        if out['status'] not in ('待付款', '待发货', '运输中', '已完成', '已取消', '售后中'):
            raise Problem(400, '不支持的订单状态')
        out['items'] = strings(v.get('items'), '订单商品')
        if not out['items']:
            raise Problem(400, '订单至少包含一件商品')
        out['amount'] = number(v.get('amount'), '订单金额')
        out['carrier'] = text(v.get('carrier') or '', '承运方', 80, True)
        out['tracking'] = text(v.get('tracking') or '', '物流编号', 100, True)
        out['timeline'] = strings(v.get('timeline', []), '物流节点')
    else:
        out['topic'] = text(v.get('topic'), '知识标题', 100)
        out['answer'] = text(v.get('answer'), '知识正文', 5000)
        out['keywords'] = strings(v.get('keywords'), '检索关键词')
        if not out['keywords']:
            raise Problem(400, '至少需要一个检索关键词')
        out['published'] = v.get('published', False)
        if not isinstance(out['published'], bool):
            raise Problem(400, '发布状态须为布尔值')
    return out


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 600000).hex()
    return f'{salt}${digest}'


def credentials(body):
    username = text(body.get('username'), '账号', 40).lower()
    password = text(body.get('password'), '密码', 128)
    if not re.fullmatch(r'[a-z0-9_.-]{3,40}', username) or len(password) < 12:
        raise Problem(400, '账号限 3–40 位字母数字；密码至少 12 位')
    return username, password


class Store:
    def __init__(self, path=None, seed=True):
        self.path = Path(path or ROOT / 'data/private/commerce.sqlite3')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL,role TEXT NOT NULL,created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),csrf TEXT NOT NULL,expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL,id TEXT NOT NULL,data TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,updated REAL NOT NULL,PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),title TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,busy_until REAL NOT NULL DEFAULT 0,lease TEXT);
                CREATE TABLE IF NOT EXISTS messages (seq INTEGER PRIMARY KEY AUTOINCREMENT,conversation_id TEXT NOT NULL REFERENCES conversations(id),role TEXT NOT NULL,content TEXT NOT NULL,trace TEXT NOT NULL,created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS turns (conversation_id TEXT NOT NULL REFERENCES conversations(id),request_id TEXT NOT NULL,message TEXT NOT NULL,response TEXT NOT NULL,PRIMARY KEY(conversation_id,request_id));
                CREATE TABLE IF NOT EXISTS tickets (id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL REFERENCES conversations(id),created_by TEXT NOT NULL REFERENCES users(id),subject TEXT NOT NULL,note TEXT NOT NULL,status TEXT NOT NULL,assignee TEXT,created REAL NOT NULL,updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS audits (seq INTEGER PRIMARY KEY AUTOINCREMENT,actor TEXT NOT NULL,action TEXT NOT NULL,target TEXT NOT NULL,created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id,seq);
                CREATE INDEX IF NOT EXISTS conversations_owner ON conversations(user_id,updated);
            ''')
            columns = {r[1] for r in db.execute('PRAGMA table_info(tickets)')}
            if 'resolution' not in columns:
                db.execute("ALTER TABLE tickets ADD COLUMN resolution TEXT NOT NULL DEFAULT ''")
            user_columns = {r[1] for r in db.execute('PRAGMA table_info(users)')}
            if 'active' not in user_columns:
                db.execute('ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
            db.execute("INSERT INTO meta(key,value) VALUES ('schema_version','2') ON CONFLICT(key) DO UPDATE SET value='2'")
            if not db.execute("SELECT 1 FROM meta WHERE key='initialized'").fetchone():
                if seed:
                    demo = json.loads((ROOT / 'data/demo.json').read_text(encoding='utf-8'))
                    for kind, rows in demo.items():
                        for i, row in enumerate(rows):
                            if kind == 'policies':
                                row = {**row, 'id': f'KB{i+1:03}', 'published': True}
                            self._put(db, kind, validate(kind, row))
                db.execute("INSERT INTO meta VALUES ('initialized','1')")

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def audit(self, db, actor, action, target):
        db.execute('INSERT INTO audits(actor,action,target,created) VALUES (?,?,?,?)', (actor, action, target, time.time()))

    def bootstrap_needed(self):
        with self.connection() as db:
            return not db.execute('SELECT 1 FROM users LIMIT 1').fetchone()

    def create_user(self, body, actor=None):
        username, password = credentials(body)
        role = body.get('role', 'support') if actor else 'admin'
        if role not in ('admin', 'support'):
            raise Problem(400, '无效角色')
        hashed = password_hash(password)
        with self.connection(True) as db:
            if actor is None and db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                raise Problem(409, '管理员已创建，请登录')
            uid = uuid.uuid4().hex
            try:
                db.execute('INSERT INTO users(id,username,password,role,created) VALUES (?,?,?,?,?)', (uid, username, hashed, role, time.time()))
            except sqlite3.IntegrityError:
                raise Problem(409, '账号已存在') from None
            self.audit(db, actor or uid, 'user.create', uid)
        return {'id': uid, 'username': username, 'role': role}

    def login(self, body):
        username = text(body.get('username'), '账号', 40).lower()
        password = text(body.get('password'), '密码', 128)
        with self.connection(True) as db:
            row = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            stored = row['password'] if row else '0' * 32 + '$' + '0' * 64
            hashed = password_hash(password, stored.split('$')[0])
            if not hmac.compare_digest(hashed, stored) or not row or not row['active']:
                raise Problem(401, '账号或密码错误')
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO sessions VALUES (?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), row['id'], csrf, time.time() + 8 * 3600))
            self.audit(db, row['id'], 'session.login', row['id'])
        return token, csrf

    def session(self, token):
        with self.connection() as db:
            row = db.execute('SELECT u.id,u.username,u.role,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>? AND u.active=1', (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            return dict(row) if row else None

    def logout(self, token):
        with self.connection(True) as db:
            db.execute('DELETE FROM sessions WHERE token=?', (hashlib.sha256(token.encode()).hexdigest(),))

    def users(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute('SELECT id,username,role,created,active FROM users ORDER BY created')]

    def toggle_user(self, actor, body):
        uid = text(body.get('id'), '成员编号', 60)
        active = body.get('active')
        if not isinstance(active, bool):
            raise Problem(400, '账号状态须为布尔值')
        if actor == uid:
            raise Problem(400, '不能在此禁用当前登录账号')
        with self.connection(True) as db:
            row = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
            if not row:
                raise Problem(404, '成员不存在')
            if not active and row['role'] == 'admin' and db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0] <= 1:
                raise Problem(409, '至少需要一个启用的管理员')
            db.execute('UPDATE users SET active=? WHERE id=?', (int(active), uid))
            db.execute('DELETE FROM sessions WHERE user_id=?', (uid,))
            self.audit(db, actor, 'user.enable' if active else 'user.disable', uid)
        return {'id': uid, 'active': active}

    def records(self, kind):
        with self.connection() as db:
            return [{**json.loads(r['data']), '_version': r['version'], '_updated': r['updated']} for r in db.execute('SELECT * FROM records WHERE kind=? ORDER BY id LIMIT 2000', (kind,))]

    def snapshot(self):
        with self.connection() as db:
            rows = db.execute('SELECT kind,data FROM records ORDER BY id').fetchall()
        result = {k: [] for k in ('products', 'orders', 'policies')}
        for r in rows:
            value = json.loads(r['data'])
            if r['kind'] == 'products' and not value.get('active', True):
                continue
            if r['kind'] == 'policies' and not value.get('published'):
                continue
            result[r['kind']].append(value)
        return result

    def _put(self, db, kind, record):
        db.execute('INSERT INTO records(kind,id,data,version,updated) VALUES (?,?,?,1,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data,version=records.version+1,updated=excluded.updated', (kind, record['id'], json.dumps(record, ensure_ascii=False), time.time()))

    def save_record(self, kind, body, actor):
        value = validate(kind, body)
        version = body.get('_version')
        with self.connection(True) as db:
            current = db.execute('SELECT version FROM records WHERE kind=? AND id=?', (kind, value['id'])).fetchone()
            if current and version != current['version']:
                raise Problem(409, '记录已存在或已被更新，请刷新后编辑，避免覆盖')
            if not current and version is not None:
                raise Problem(409, '记录不存在，请刷新后重试')
            if not current and db.execute('SELECT COUNT(*) FROM records WHERE kind=?', (kind,)).fetchone()[0] >= 2000:
                raise Problem(400, '单类数据最多 2000 条，请对接分页数据服务')
            self._put(db, kind, value)
            self.audit(db, actor, f'{kind}.save', value['id'])
        return value

    def import_records(self, kind, rows, actor):
        if not isinstance(rows, list) or not 1 <= len(rows) <= 500:
            raise Problem(400, '每批导入须包含 1–500 条记录')
        clean = [validate(kind, row) for row in rows]
        ids = [r['id'] for r in clean]
        if len(set(ids)) != len(ids):
            raise Problem(400, '导入文件包含重复编号')
        with self.connection(True) as db:
            existing = {r[0] for r in db.execute('SELECT id FROM records WHERE kind=?', (kind,))}
            if existing.intersection(ids):
                raise Problem(409, '导入包含已有编号；导入仅新增，不覆盖，请在列表中编辑已有数据')
            if len(existing) + len(ids) > 2000:
                raise Problem(400, '单类数据最多 2000 条')
            for row in clean:
                self._put(db, kind, row)
            self.audit(db, actor, f'{kind}.import', str(len(clean)))
        return {'imported': len(clean)}

    def new_conversation(self, uid):
        cid, now = uuid.uuid4().hex, time.time()
        with self.connection(True) as db:
            db.execute('INSERT INTO conversations(id,user_id,title,created,updated) VALUES (?,?,?,?,?)', (cid, uid, '新咨询', now, now))
        return {'id': cid, 'title': '新咨询', 'updated': now}

    def conversations(self, uid):
        with self.connection() as db:
            return [dict(r) for r in db.execute('SELECT id,title,updated FROM conversations WHERE user_id=? ORDER BY updated DESC LIMIT 100', (uid,))]

    def owned(self, db, cid, uid):
        row = db.execute('SELECT * FROM conversations WHERE id=? AND user_id=?', (cid, uid)).fetchone()
        if not row:
            raise Problem(404, '咨询不存在或无权访问')
        return row

    def messages(self, cid, uid):
        with self.connection() as db:
            self.owned(db, cid, uid)
            return [{**dict(r), 'trace': json.loads(r['trace'])} for r in db.execute('SELECT role,content,trace,created FROM messages WHERE conversation_id=? ORDER BY seq', (cid,))]

    def begin_turn(self, cid, uid, request_id, message):
        with self.connection(True) as db:
            row = self.owned(db, cid, uid)
            prior = db.execute('SELECT message,response FROM turns WHERE conversation_id=? AND request_id=?', (cid, request_id)).fetchone()
            if prior:
                if prior['message'] != message:
                    raise Problem(409, '相同请求编号不能用于不同消息')
                return None, json.loads(prior['response'])
            if row['busy_until'] > time.time():
                raise Problem(409, '当前咨询正在生成回复，请稍后重试')
            if db.execute('SELECT COUNT(*) FROM messages WHERE conversation_id=?', (cid,)).fetchone()[0] >= 400:
                raise Problem(400, '咨询已达 200 轮，请新建咨询')
            lease = uuid.uuid4().hex
            db.execute('UPDATE conversations SET lease=?,busy_until=? WHERE id=?', (lease, time.time()+180, cid))
            history = [dict(r) for r in db.execute('SELECT role,content FROM messages WHERE conversation_id=? ORDER BY seq DESC LIMIT 20', (cid,))][::-1]
        return lease, history

    def finish_turn(self, cid, uid, lease, request_id, message, result):
        now = time.time()
        with self.connection(True) as db:
            row = self.owned(db, cid, uid)
            if row['lease'] != lease:
                raise Problem(409, '回复已过期，请刷新咨询')
            for role, content, trace in [('user', message, []), ('assistant', result['reply'], result['trace'])]:
                db.execute('INSERT INTO messages(conversation_id,role,content,trace,created) VALUES (?,?,?,?,?)', (cid, role, content, json.dumps(trace, ensure_ascii=False), now))
            db.execute('INSERT INTO turns VALUES (?,?,?,?)', (cid, request_id, message, json.dumps(result, ensure_ascii=False)))
            db.execute('UPDATE conversations SET title=CASE WHEN title=? THEN ? ELSE title END,updated=?,lease=NULL,busy_until=0 WHERE id=?', ('新咨询', message[:36], now, cid))
            self.audit(db, uid, 'chat.complete', cid)

    def release_turn(self, cid, lease):
        with self.connection(True) as db:
            db.execute('UPDATE conversations SET lease=NULL,busy_until=0 WHERE id=? AND lease=?', (cid, lease))

    def create_ticket(self, uid, body):
        cid = text(body.get('conversation_id'), '咨询编号', 60)
        subject = text(body.get('subject'), '工单主题', 120)
        note = text(body.get('note', ''), '转接说明', 2000, True)
        with self.connection(True) as db:
            self.owned(db, cid, uid)
            current = db.execute("SELECT id FROM tickets WHERE conversation_id=? AND status!='resolved'", (cid,)).fetchone()
            if current:
                raise Problem(409, '此咨询已有待处理工单')
            tid, now = 'TK-' + uuid.uuid4().hex[:10].upper(), time.time()
            db.execute('INSERT INTO tickets(id,conversation_id,created_by,subject,note,status,assignee,created,updated) VALUES (?,?,?,?,?,?,?,?,?)', (tid, cid, uid, subject, note, 'open', None, now, now))
            self.audit(db, uid, 'ticket.create', tid)
        return {'id': tid}

    def tickets(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute('SELECT t.*,u.username AS creator,a.username AS assigned_name FROM tickets t JOIN users u ON u.id=t.created_by LEFT JOIN users a ON a.id=t.assignee ORDER BY updated DESC LIMIT 500')]

    def ticket_detail(self, tid):
        with self.connection() as db:
            row = db.execute('SELECT * FROM tickets WHERE id=?', (tid,)).fetchone()
            if not row:
                raise Problem(404, '工单不存在')
            return {'ticket': dict(row), 'messages': [{**dict(r), 'trace': json.loads(r['trace'])} for r in db.execute('SELECT role,content,trace,created FROM messages WHERE conversation_id=? ORDER BY seq', (row['conversation_id'],))]}

    def update_ticket(self, uid, body):
        tid = text(body.get('id'), '工单编号', 40)
        target = body.get('status')
        with self.connection(True) as db:
            row = db.execute('SELECT * FROM tickets WHERE id=?', (tid,)).fetchone()
            if not row:
                raise Problem(404, '工单不存在')
            valid = (row['status'] == 'open' and target == 'in_progress') or (row['status'] == 'in_progress' and target == 'resolved' and row['assignee'] == uid)
            if not valid:
                raise Problem(409, '请先领取待处理工单，仅领取人可以标记完成')
            resolution = text(body.get('resolution'), '处理结果', 2000) if target == 'resolved' else ''
            db.execute('UPDATE tickets SET status=?,assignee=?,updated=?,resolution=? WHERE id=?', (target, uid, time.time(), resolution, tid))
            self.audit(db, uid, 'ticket.' + target, tid)
        return {'id': tid, 'status': target}

    def audits(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute('SELECT a.seq,a.action,a.target,a.created,u.username AS actor FROM audits a LEFT JOIN users u ON u.id=a.actor ORDER BY seq DESC LIMIT 100')]

    def metrics(self):
        with self.connection() as db:
            totals = {name: db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0] for name in ('conversations', 'messages', 'tickets')}
            totals['open_tickets'] = db.execute("SELECT COUNT(*) FROM tickets WHERE status!='resolved'").fetchone()[0]
            totals['recent_activity'] = [dict(r) for r in db.execute('SELECT action,COUNT(*) AS count FROM audits GROUP BY action ORDER BY count DESC LIMIT 6')]
        return totals

    def backup(self, destination):
        destination = Path(destination)
        if destination.exists():
            raise Problem(409, '备份文件已存在，请使用新文件名')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as source:
            with closing(sqlite3.connect(destination)) as target:
                source.backup(target)

    def reset_password(self, username, password):
        username, password = credentials({'username': username, 'password': password})
        hashed = password_hash(password)
        with self.connection(True) as db:
            row = db.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
            if not row:
                raise Problem(404, '账号不存在')
            db.execute('UPDATE users SET password=? WHERE id=?', (hashed, row['id']))
            db.execute('DELETE FROM sessions WHERE user_id=?', (row['id'],))
            self.audit(db, row['id'], 'user.password_reset_cli', row['id'])
