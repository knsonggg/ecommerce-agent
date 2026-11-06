"""Authenticated single-store WSGI application."""
import argparse
import collections
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.error
from http import HTTPStatus
from http.cookies import SimpleCookie
from socketserver import ThreadingMixIn
from urllib.parse import urlsplit
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server
from agent import ROOT, chat, data_scope, load_env, run_tool, explicit_aftersale
from store import Store, Problem, text
from aftersales import AfterSales

load_env()
LOG = logging.getLogger('commerce')


class Limiter:
    def __init__(self):
        self.entries = {}
        self.lock = threading.Lock()

    def check(self, key, limit, seconds):
        now = time.monotonic()
        with self.lock:
            if len(self.entries) > 10000:
                self.entries = {k: v for k, v in self.entries.items() if v and v[-1] > now - 600}
                if len(self.entries) > 10000:
                    raise Problem(429, '请求过多，请稍后重试')
            queue = self.entries.setdefault(key, collections.deque())
            while queue and queue[0] <= now - seconds:
                queue.popleft()
            if len(queue) >= limit:
                raise Problem(429, '请求过于频繁，请稍后重试')
            queue.append(now)


class App:
    def __init__(self, store=None, origin=None, production=False):
        self.store = store or Store(os.environ.get('COMMERCE_DB') or None)
        self.aftersales = AfterSales(self.store)
        self.origin = (origin or os.environ.get('APP_ORIGIN', 'http://127.0.0.1:8000')).rstrip('/')
        self.production = production
        parsed = urlsplit(self.origin)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.path:
            raise ValueError('APP_ORIGIN 必须是没有路径的完整 HTTP(S) 地址')
        if production and parsed.scheme != 'https':
            raise ValueError('生产模式要求 APP_ORIGIN 使用 HTTPS，TLS 由反向代理提供')
        self.origins = {self.origin}
        if parsed.hostname in ('127.0.0.1', 'localhost') and not production:
            port = parsed.port or 80
            self.origins.update({f'http://127.0.0.1:{port}', f'http://localhost:{port}'})
        self.hosts = {urlsplit(o).netloc for o in self.origins}
        self.limiter = Limiter()
        self.model_slots = threading.BoundedSemaphore(4)

    def __call__(self, env, start_response):
        request_id = secrets.token_hex(8)
        headers = []
        try:
            if env.get('HTTP_HOST') not in self.hosts:
                raise Problem(400, '主机名不在 APP_ORIGIN 允许范围内')
            method, path = env['REQUEST_METHOD'], env['PATH_INFO']
            if method not in ('GET', 'POST'):
                raise Problem(405, '不支持的请求方法')
            if method == 'POST':
                if env.get('HTTP_ORIGIN') and env['HTTP_ORIGIN'] not in self.origins:
                    raise Problem(403, '不允许跨站请求')
                if env.get('HTTP_SEC_FETCH_SITE') == 'cross-site':
                    raise Problem(403, '不允许跨站请求')
                if env.get('CONTENT_TYPE', '').split(';')[0] != 'application/json':
                    raise Problem(415, '请使用 application/json')
                try:
                    size = int(env.get('CONTENT_LENGTH') or 0)
                except ValueError:
                    raise Problem(400, '请求长度不合法') from None
                if not 0 < size <= 1048576:
                    raise Problem(413, '请求大小须小于 1 MB')
                try:
                    body = json.loads(env['wsgi.input'].read(size))
                except (ValueError, UnicodeError):
                    raise Problem(400, 'JSON 格式错误') from None
                if not isinstance(body, dict):
                    raise Problem(400, '请求须为 JSON 对象')
            else:
                body = {}
            cookie = SimpleCookie()
            try:
                cookie.load(env.get('HTTP_COOKIE', ''))
            except Exception:
                pass
            token = cookie['commerce_session'].value if 'commerce_session' in cookie else ''
            user = self.store.session(token) if token else None
            if method == 'POST' and user and not hmac.compare_digest(env.get('HTTP_X_CSRF_TOKEN', ''), user['csrf']):
                raise Problem(403, '安全校验失败，请刷新页面后重试')
            status, data, mime, extra = self.route(method, path, body, env, token, user)
            headers.extend(extra)
        except Problem as error:
            status, data, mime = error.status, {'error': error.message, 'request_id': request_id}, 'application/json; charset=utf-8'
        except urllib.error.HTTPError as error:
            status, data, mime = 502, {'error': f'DeepSeek 请求失败（HTTP {error.code}），请检查本地模型配置及额度。', 'request_id': request_id}, 'application/json; charset=utf-8'
        except (TimeoutError, urllib.error.URLError):
            status, data, mime = 504, {'error': '模型连接超时或网络不可用，请重试。', 'request_id': request_id}, 'application/json; charset=utf-8'
        except Exception as error:
            LOG.error('request=%s type=%s', request_id, type(error).__name__)
            status, data, mime = 500, {'error': '服务暂时无法完成请求，请联系管理员并提供请求编号。', 'request_id': request_id}, 'application/json; charset=utf-8'
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        headers += [('Content-Type', mime), ('Content-Length', str(len(raw))), ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'), ('X-Frame-Options', 'DENY'), ('Referrer-Policy', 'same-origin'), ('X-Request-ID', request_id), ('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")]
        if self.production:
            headers.append(('Strict-Transport-Security', 'max-age=31536000'))
        start_response(f'{status} {HTTPStatus(status).phrase}', headers)
        return [raw]

    def cookie(self, token, clear=False):
        return ('Set-Cookie', 'commerce_session=' + token + '; Path=/; HttpOnly; SameSite=Strict; Max-Age=' + ('0' if clear else '28800') + ('; Secure' if self.production else ''))

    def route(self, method, path, body, env, token, user):
        def ok(data, status=200, headers=None):
            return status, data, 'application/json; charset=utf-8', headers or []
        if method == 'GET':
            files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if path in files:
                filename, mime = files[path]
                return 200, (ROOT / 'web' / filename).read_bytes(), mime, []
            if path == '/api/status':
                return ok({'ready': True, 'setup_required': self.store.bootstrap_needed(), 'local_setup': not self.production})
            if path == '/api/me':
                return ok({'user': user})
        if method == 'POST' and path in ('/api/setup', '/api/login'):
            ip = env.get('REMOTE_ADDR', '')
            self.limiter.check('login-ip:' + ip, 10, 600)
            self.limiter.check('login-name:' + str(body.get('username', '')).lower()[:40], 10, 600)
            if path == '/api/setup':
                if self.production or ip not in ('127.0.0.1', '::1'):
                    raise Problem(403, '只能在本机开发模式初始化；生产环境请使用 manage.py create-admin')
                self.store.create_user(body)
            session_token, csrf = self.store.login(body)
            return ok({'csrf': csrf}, headers=[self.cookie(session_token)])
        if not user:
            raise Problem(401, '请先登录商家工作台')
        uid = user['id']
        if method == 'POST' and path == '/api/logout':
            self.store.logout(token)
            return ok({'ok': True}, headers=[self.cookie('', True)])
        if method == 'GET':
            if path == '/api/overview':
                return ok({**self.store.metrics(), 'product_count': len(self.store.records('products')), 'order_count': len(self.store.records('orders')), 'knowledge_count': len(self.store.records('policies')), 'mode': 'deepseek' if os.environ.get('DEEPSEEK_API_KEY') else 'demo', 'model': os.environ.get('DEEPSEEK_MODEL', 'deepseek-flash'), 'channel': 'manual', 'scope': 'single-store'})
            if path == '/api/conversations':
                return ok(self.store.conversations(uid))
            match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})', path)
            if match:
                return ok(self.store.messages(match[1], uid))
            match = re.fullmatch(r'/api/records/(products|orders|policies)', path)
            if match:
                return ok(self.store.records(match[1]))
            if path == '/api/analysis':
                with data_scope(self.store.snapshot()):
                    return ok(run_tool('analyze_selection', {}))
            if path == '/api/tickets':
                return ok(self.store.tickets())
            if path == '/api/aftersales':
                return ok(self.aftersales.list())
            match = re.fullmatch(r'/api/tickets/(TK-[A-F0-9]{10})', path)
            if match:
                return ok(self.store.ticket_detail(match[1]))
            if path in ('/api/audits', '/api/users'):
                self.admin(user)
                return ok(self.store.audits() if path == '/api/audits' else self.store.users())
        if method == 'POST':
            if path == '/api/conversations':
                return ok(self.store.new_conversation(uid), 201)
            if path == '/api/chat':
                if 'history' in body:
                    raise Problem(400, '对话历史由服务端管理，不接受客户端覆盖')
                message = text(body.get('message'), '消息', 2000)
                cid = text(body.get('conversation_id'), '咨询编号', 60)
                rid = text(body.get('request_id'), '请求编号', 80)
                self.limiter.check('chat:' + uid, 20, 60)
                lease, prior = self.store.begin_turn(cid, uid, rid, message)
                if lease is None:
                    return ok(prior)
                if not self.model_slots.acquire(blocking=False):
                    self.store.release_turn(cid, lease)
                    raise Problem(429, '客服助手当前繁忙，请稍后重试')
                try:
                    started = time.monotonic()
                    order_ids=re.findall(r'[A-Za-z][A-Za-z0-9_-]*\d[A-Za-z0-9_-]*',message)
                    with data_scope(self.store.snapshot(), self.aftersales, uid, rid, explicit_aftersale(message), order_ids):
                        result = chat(message, prior)
                    result['elapsed_ms'] = round((time.monotonic() - started) * 1000)
                    self.store.finish_turn(cid, uid, lease, rid, message, result)
                    return ok(result)
                finally:
                    self.model_slots.release()
                    self.store.release_turn(cid, lease)
            if path == '/api/tickets':
                return ok(self.store.create_ticket(uid, body), 201)
            if path == '/api/tickets/update':
                return ok(self.store.update_ticket(uid, body))
            if path == '/api/aftersales':
                return ok(self.aftersales.request(uid, body, body.get('request_id')))
            if path == '/api/aftersales/decision':
                self.admin(user)
                return ok(self.aftersales.decide(uid, body))
            match = re.fullmatch(r'/api/records/(products|orders|policies)(/import)?', path)
            if match:
                self.admin(user)
                return ok(self.store.import_records(match[1], body.get('records'), uid) if match[2] else self.store.save_record(match[1], body, uid))
            if path == '/api/users':
                self.admin(user)
                return ok(self.store.create_user(body, uid), 201)
            if path == '/api/users/status':
                self.admin(user)
                return ok(self.store.toggle_user(uid, body))
        raise Problem(404, '未找到资源')

    @staticmethod
    def admin(user):
        if user['role'] != 'admin':
            raise Problem(403, '只有管理员可以维护数据和账号')


class ThreadedServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *_):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--production', action='store_true')
    args = parser.parse_args()
    app = App(origin=os.environ.get('APP_ORIGIN') or f'http://127.0.0.1:{args.port}', production=args.production)
    print(f'Commerce Copilot: {app.origin}', flush=True)
    if args.production:
        from waitress import serve
        serve(app, host='127.0.0.1', port=args.port, threads=8, max_request_body_size=1048576, channel_timeout=150, expose_tracebacks=False)
    else:
        make_server('127.0.0.1', args.port, app, server_class=ThreadedServer, handler_class=QuietHandler).serve_forever()


if __name__ == '__main__':
    main()
