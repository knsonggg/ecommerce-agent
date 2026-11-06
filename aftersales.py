"""Local simulation only. No payment or marketplace client is implemented."""
import json
import time
import uuid
from decimal import Decimal, InvalidOperation
from store import Problem, text


def cents(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal('.01')):
            raise ValueError()
        return int(amount * 100)
    except (InvalidOperation, ValueError, TypeError):
        raise Problem(400, '金额格式错误') from None


class AfterSales:
    AUTO_LIMIT_CENTS = 10000

    def __init__(self, store):
        self.store = store
        with store.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS aftersales (
                    id TEXT PRIMARY KEY, order_id TEXT NOT NULL, kind TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL, reason TEXT NOT NULL,
                    status TEXT NOT NULL, actor TEXT NOT NULL REFERENCES users(id),
                    request_key TEXT NOT NULL, order_version INTEGER NOT NULL,
                    decision_note TEXT NOT NULL DEFAULT '', created REAL NOT NULL,updated REAL NOT NULL,
                    UNIQUE(actor,request_key));
                CREATE INDEX IF NOT EXISTS aftersales_order ON aftersales(order_id,status);
            ''')

    def _order(self, db, oid):
        row = db.execute("SELECT * FROM records WHERE kind='orders' AND id=?", (oid,)).fetchone()
        if not row:
            raise Problem(404, '订单不存在')
        return json.loads(row['data']), row['version']

    def _eligible(self, order, kind):
        allowed = {'cancel': ('待付款',), 'refund_only': ('待发货',), 'return_refund': ('运输中', '已完成')}
        if kind not in allowed or order['status'] not in allowed[kind]:
            raise Problem(409, '订单状态不符合此操作：待付款可取消，待发货可仅退款，已发货须退货退款')
        if kind != 'cancel' and cents(order['amount']) <= 0:
            raise Problem(409, '订单金额无效，须人工核实')

    def eligibility(self, oid):
        with self.store.connection() as db:
            order, version = self._order(db, oid)
            cases = [dict(r) for r in db.execute('SELECT * FROM aftersales WHERE order_id=? ORDER BY created DESC', (oid,))]
        kind = {'待付款':'cancel','待发货':'refund_only','运输中':'return_refund','已完成':'return_refund'}.get(order['status'])
        return {'order_id': oid, 'order_status': order['status'], 'order_version':version, 'amount':order['amount'], 'suggested_kind':kind, 'cases':cases, 'execution':'simulation', 'rule':'仅本地模拟；待付款取消自动完成，未发货全额退款不超过100元自动模拟完成；其他退款须管理员审批，退货须确认验收。'}

    def request(self, uid, body, request_key):
        oid = text(body.get('order_id'), '订单编号', 60)
        kind = body.get('kind')
        reason = text(body.get('reason'), '申请原因', 500)
        key = text(request_key, '请求编号', 160)
        with self.store.connection(True) as db:
            existing = db.execute('SELECT * FROM aftersales WHERE actor=? AND request_key=?', (uid,key)).fetchone()
            if existing:
                if (existing['order_id'],existing['kind'],existing['reason']) != (oid,kind,reason):
                    raise Problem(409, '同一请求编号不能用于不同售后申请')
                return dict(existing)
            order, version = self._order(db,oid)
            self._eligible(order,kind)
            if db.execute("SELECT 1 FROM aftersales WHERE order_id=? AND status IN ('pending_review','awaiting_return','simulated_completed')",(oid,)).fetchone():
                raise Problem(409,'该订单已有进行中或已完成的售后，不能重复办理')
            amount = 0 if kind=='cancel' else cents(order['amount'])
            status = 'simulated_completed' if kind=='cancel' or kind=='refund_only' and amount <= self.AUTO_LIMIT_CENTS else 'pending_review'
            case_id,now='AS-'+uuid.uuid4().hex[:12].upper(),time.time()
            db.execute('INSERT INTO aftersales(id,order_id,kind,amount_cents,reason,status,actor,request_key,order_version,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)',(case_id,oid,kind,amount,reason,status,uid,key,version,now,now))
            if status=='simulated_completed':
                self._complete(db,order,kind,case_id)
            self.store.audit(db,uid,'aftersales.'+status,case_id)
            return dict(db.execute('SELECT * FROM aftersales WHERE id=?',(case_id,)).fetchone())

    def _complete(self,db,order,kind,case_id):
        order['status']='已取消' if kind in ('cancel','refund_only') else '已退款'
        order['timeline'].append(f'本地模拟售后完成：{case_id}（无真实资金操作）')
        self.store._put(db,'orders',order)

    def list(self):
        with self.store.connection() as db:
            return [dict(r) for r in db.execute('SELECT a.*,u.username AS creator FROM aftersales a JOIN users u ON u.id=a.actor ORDER BY a.created DESC LIMIT 500')]

    def decide(self,uid,body):
        cid=text(body.get('id'),'售后编号',40)
        decision=body.get('decision')
        note=text(body.get('note'),'审核或验收说明',1000)
        with self.store.connection(True) as db:
            case=db.execute('SELECT * FROM aftersales WHERE id=?',(cid,)).fetchone()
            if not case:
                raise Problem(404,'售后申请不存在')
            if decision=='reject' and case['status'] in ('pending_review','awaiting_return'):
                target='rejected'
            elif decision=='approve' and case['status']=='pending_review':
                target='awaiting_return' if case['kind']=='return_refund' else 'simulated_completed'
            elif decision=='received' and case['status']=='awaiting_return':
                target='simulated_completed'
            else:
                raise Problem(409,'售后状态已经变化，请刷新；不能重复审批或重复退款')
            if target!='rejected':
                order,version=self._order(db,case['order_id'])
                self._eligible(order,case['kind'])
                if version!=case['order_version'] or cents(order['amount'])!=case['amount_cents']:
                    raise Problem(409,'订单已变化，请驳回原申请并重新核实，不能按旧资料退款')
                if target=='simulated_completed':
                    self._complete(db,order,case['kind'],cid)
            db.execute('UPDATE aftersales SET status=?,decision_note=?,updated=? WHERE id=?',(target,note,time.time(),cid))
            self.store.audit(db,uid,'aftersales.'+target,cid)
            return dict(db.execute('SELECT * FROM aftersales WHERE id=?',(cid,)).fetchone())
