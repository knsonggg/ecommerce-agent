"""Local-only demo server. No third-party dependencies."""
import json
import os
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from agent import ROOT, chat, load_env

load_env()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Do not log user messages or credentials.

    def send(self, status, data, content_type='application/json; charset=utf-8'):
        body = json.dumps(data, ensure_ascii=False).encode() if not isinstance(data, bytes) else data
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/api/status':
            return self.send(200, {'mode': 'deepseek' if os.environ.get('DEEPSEEK_API_KEY') else 'demo', 'products': 6, 'orders': 2})
        files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if self.path not in files:
            return self.send(404, {'error': '未找到资源'})
        filename, mime = files[self.path]
        self.send(200, (ROOT / 'web' / filename).read_bytes(), mime)

    def do_POST(self):
        if self.path != '/api/chat':
            return self.send(404, {'error': '未找到接口'})
        origin = self.headers.get('Origin')
        if origin and origin not in ('http://127.0.0.1:8000', 'http://localhost:8000'):
            return self.send(403, {'error': '不允许跨站调用'})
        try:
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size <= 64000:
                return self.send(413, {'error': '请求大小不合法'})
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError()
            message, history = body.get('message'), body.get('history', [])
            if not isinstance(message, str) or not message.strip() or len(message) > 2000:
                raise ValueError()
            if not isinstance(history, list) or len(history) > 20:
                raise ValueError()
            for item in history:
                if not isinstance(item, dict) or item.get('role') not in ('user', 'assistant') or not isinstance(item.get('content'), str) or len(item['content']) > 8000:
                    raise ValueError()
            history = [{'role': m['role'], 'content': m['content']} for m in history]
            self.send(200, chat(message.strip(), history))
        except (ValueError, TypeError, UnicodeError):
            self.send(400, {'error': '请求格式错误：消息限 2000 字，历史限 20 条。'})
        except urllib.error.HTTPError as error:
            self.send(502, {'error': f'DeepSeek 请求失败（HTTP {error.code}），请检查本地密钥、模型配置或额度。'})
        except Exception:
            self.send(502, {'error': '模型服务暂不可用，请稍后重试；未自动切换演示回答。'})


if __name__ == '__main__':
    print('Commerce Copilot: http://127.0.0.1:8000', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 8000), Handler).serve_forever()
