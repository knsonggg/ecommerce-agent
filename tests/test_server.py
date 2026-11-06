"""WSGI boundary tests, including real persistence and session cookies."""
import io
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from server import App
from store import Store, Problem

PASSWORD = 'unit-test-only-password'


class Client:
    def __init__(self, app):
        self.app, self.cookie, self.csrf = app, '', ''

    def request(self, path, body=None, headers=None):
        raw = json.dumps(body).encode() if body is not None else b''
        env = {'REQUEST_METHOD': 'GET' if body is None else 'POST', 'PATH_INFO': path, 'HTTP_HOST': '127.0.0.1:8000', 'REMOTE_ADDR': '127.0.0.1', 'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw), 'HTTP_COOKIE': self.cookie, 'HTTP_X_CSRF_TOKEN': self.csrf}
        env.update(headers or {})
        meta = {}
        def start(status, hs):
            meta.update(status=int(status[:3]), headers=dict(hs))
        result = b''.join(self.app(env, start))
        if 'Set-Cookie' in meta['headers']:
            self.cookie = meta['headers']['Set-Cookie'].split(';')[0]
        data = json.loads(result) if meta['headers']['Content-Type'].startswith('application/json') else result
        if isinstance(data, dict) and data.get('csrf'):
            self.csrf = data['csrf']
        return meta['status'], data, meta['headers']


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_root = Path(__file__).resolve().parents[1] / 'data/private'
        test_root.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(dir=test_root)
        cls.base = Path(cls.tmp.name) / 'base.sqlite3'
        store = Store(cls.base)
        cls.admin = store.create_user({'username': 'admin', 'password': PASSWORD})
        cls.support = store.create_user({'username': 'support', 'password': PASSWORD, 'role': 'support'}, cls.admin['id'])
        cls.atoken, cls.acsrf = store.login({'username': 'admin', 'password': PASSWORD})
        cls.stoken, cls.scsrf = store.login({'username': 'support', 'password': PASSWORD})

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.path = Path(self.tmp.name) / (uuid.uuid4().hex + '.sqlite3')
        Store(self.base).backup(self.path)
        self.store = Store(self.path)
        self.app = App(self.store)
        self.client = Client(self.app)
        self.client.cookie, self.client.csrf = 'commerce_session=' + self.atoken, self.acsrf
        self.staff = Client(self.app)
        self.staff.cookie, self.staff.csrf = 'commerce_session=' + self.stoken, self.scsrf

    def req(self, path, body=None, status=200, client=None, headers=None):
        code, data, _ = (client or self.client).request(path, body, headers)
        self.assertEqual(code, status, data)
        return data

    def new_conversation(self, client=None):
        return self.req('/api/conversations', {}, 201, client)['id']

    def test_auth_and_csrf(self):
        self.req('/api/records/orders', status=401, client=Client(self.app))
        self.req('/api/conversations', {}, 403, headers={'HTTP_X_CSRF_TOKEN':''})
        self.req('/api/conversations', {}, 403, headers={'HTTP_ORIGIN':'https://hostile.example'})
        self.req('/api/overview', status=400, headers={'HTTP_HOST':'hostile.example'})

    def test_static_allowlist(self):
        for path in ['/', '/app.js', '/style.css']:
            self.req(path)
        for path in ['/.env', '/../agent.py', '/data/private/commerce.sqlite3']:
            self.req(path, status=404)

    def test_login_cookie_logout(self):
        client = Client(self.app)
        _, _, headers = client.request('/api/login', {'username':'admin','password':PASSWORD})
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        self.req('/api/me', client=client)
        self.req('/api/logout', {}, client=client)
        self.req('/api/overview', status=401, client=client)

    def test_role_permissions(self):
        product = self.req('/api/records/products')[0]
        self.req('/api/records/products', product, 403, self.staff)
        self.req('/api/users', {'username':'hacker','password':PASSWORD}, 403, self.staff)
        self.req('/api/audits', status=403, client=self.staff)
        self.req('/api/analysis', client=self.staff)

    def test_record_conflict_and_db_reload(self):
        product = self.req('/api/records/products')[0]
        product['price'] = 399
        self.req('/api/records/products', product)
        self.req('/api/records/products', product, 409)
        self.assertEqual(Store(self.path).records('products')[0]['price'], 399)

    def test_import_atomic_and_no_overwrite(self):
        p = self.req('/api/records/products')[0]
        new = {**p, 'id':'NEW001'}
        self.req('/api/records/products/import', {'records':[new, {**p,'id':'NEW002','stock':-1}]}, 400)
        self.assertFalse(any(r['id']=='NEW001' for r in self.store.records('products')))
        self.req('/api/records/products/import', {'records':[new,p]}, 409)
        self.req('/api/records/products/import', {'records':[new]})

    def test_unpublished_and_inactive_not_in_tools(self):
        policy=self.req('/api/records/policies')[0];policy['published']=False
        self.req('/api/records/policies',policy)
        self.assertFalse(any(r['id']==policy['id'] for r in self.store.snapshot()['policies']))
        product=self.req('/api/records/products')[0];product['active']=False
        self.req('/api/records/products',product)
        self.assertFalse(any(r['id']==product['id'] for r in self.store.snapshot()['products']))

    @patch.dict(os.environ, {'DEEPSEEK_API_KEY':''})
    def test_chat_persistence_and_idempotency(self):
        cid = self.new_conversation()
        body = {'conversation_id':cid,'message':'查询订单 DEMO1001','request_id':'req-001'}
        result = self.req('/api/chat',body)
        self.assertIn('运输中',result['reply'])
        self.assertEqual(self.req('/api/chat',body),result)
        self.assertEqual(len(self.req('/api/conversations/'+cid)),2)
        self.req('/api/chat',{**body,'message':'different'},409)
        self.req('/api/conversations/'+cid,status=404,client=self.staff)
        self.req('/api/chat',{**body,'request_id':'new','history':[]},400)

    def test_model_error_releases_conversation(self):
        cid=self.new_conversation()
        body={'conversation_id':cid,'message':'问题','request_id':'retryable'}
        with patch('server.chat',side_effect=TimeoutError):
            self.req('/api/chat',body,504)
        self.assertEqual(self.req('/api/conversations/'+cid),[])
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY':''}):
            self.req('/api/chat',body)

    def test_parallel_turn_rejected(self):
        cid=self.new_conversation()
        lease,_=self.store.begin_turn(cid,self.admin['id'],'first','a')
        self.req('/api/chat',{'conversation_id':cid,'message':'b','request_id':'second'},409)
        self.store.release_turn(cid,lease)

    def test_ticket_lifecycle_and_ownership(self):
        cid=self.new_conversation()
        ticket=self.req('/api/tickets',{'conversation_id':cid,'subject':'核实售后','note':'需要人工确认'},201)
        self.req('/api/tickets',{'conversation_id':cid,'subject':'重复'},409)
        self.req('/api/tickets',{'conversation_id':cid,'subject':'越权'},404,self.staff)
        tid=ticket['id']
        self.req('/api/tickets/update',{'id':tid,'status':'resolved','resolution':'done'},409)
        self.req('/api/tickets/update',{'id':tid,'status':'in_progress'},client=self.staff)
        self.req('/api/tickets/update',{'id':tid,'status':'resolved','resolution':'done'},409)
        self.req('/api/tickets/update',{'id':tid,'status':'resolved'},400,self.staff)
        self.req('/api/tickets/update',{'id':tid,'status':'resolved','resolution':'已核实店铺政策并答复'},client=self.staff)
        detail=self.req('/api/tickets/'+tid)
        self.assertEqual(detail['ticket']['status'],'resolved')
        self.assertTrue(detail['ticket']['resolution'])

    def test_login_rate_limit(self):
        anonymous=Client(self.app)
        for _ in range(10):
            self.req('/api/login',{'username':'wrong','password':PASSWORD},401,anonymous)
        self.req('/api/login',{'username':'wrong','password':PASSWORD},429,anonymous)

    def test_password_reset_revokes_session(self):
        self.store.reset_password('support',PASSWORD+'new')
        self.req('/api/overview',status=401,client=self.staff)

    def test_disable_member_revokes_access(self):
        self.req('/api/users/status', {'id':self.support['id'],'active':False})
        self.req('/api/overview',status=401,client=self.staff)
        self.req('/api/login',{'username':'support','password':PASSWORD},401,Client(self.app))
        self.req('/api/users/status', {'id':self.admin['id'],'active':False},400)
        self.req('/api/users/status', {'id':self.support['id'],'active':True})
        self.req('/api/login',{'username':'support','password':PASSWORD},client=Client(self.app))

    def test_initial_setup_once(self):
        fresh=Client(App(Store(Path(self.tmp.name)/(uuid.uuid4().hex+'.sqlite3'))))
        self.req('/api/setup',{'username':'firstadmin','password':PASSWORD},client=fresh)
        self.req('/api/setup',{'username':'another','password':PASSWORD},409,fresh)

    def test_production_setup_closed_and_cookie_secure(self):
        app=App(self.store,origin='https://shop.example',production=True)
        client=Client(app)
        self.req('/api/setup',{'username':'firstadmin','password':PASSWORD},403,client,{'HTTP_HOST':'shop.example'})
        code,_,headers=client.request('/api/login',{'username':'admin','password':PASSWORD},{'HTTP_HOST':'shop.example'})
        self.assertEqual(code,200)
        self.assertIn('Secure',headers['Set-Cookie'])

    def test_no_secrets_in_user_or_audit_responses(self):
        rows=self.req('/api/users')
        self.assertTrue(all('password' not in r for r in rows))
        data=json.dumps(self.req('/api/audits'))
        self.assertNotIn(PASSWORD,data)


if __name__=='__main__':
    unittest.main()
