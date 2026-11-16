#!/usr/bin/env python3
"""Локальный сервер для прототипа «Сбер Здоровье».
Раздаёт index.html и сохраняет данные в папку data/ (только на этом компьютере).
Запуск: python server.py   (откроется http://127.0.0.1:8765)
"""
import json, os, re, sys, threading, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'data')
ACC = os.path.join(DATA, 'accounts')      # профили: <номер>.json
ANA = os.path.join(DATA, 'analyses')      # анализы: <номер>.json
FIL = os.path.join(DATA, 'files')         # оригиналы файлов: <id>.bin + <id>.json
SESSION = os.path.join(DATA, 'session.json')  # вход: кто сейчас в системе
for d in (ACC, ANA, FIL):
    os.makedirs(d, exist_ok=True)

LOCK = threading.Lock()
KEY = re.compile(r'^sh_(auth|user|u_\d{5,15}|d_\d{5,15})$')
FID = re.compile(r'^f[a-z0-9]{6,40}$')
MAX_BODY = 60 * 1024 * 1024


def read_text(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except OSError:
        return None


def write_text(path, text):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp, path)


def key_path(key):
    if key.startswith('sh_u_'):
        return os.path.join(ACC, key[5:] + '.json')
    if key.startswith('sh_d_'):
        return os.path.join(ANA, key[5:] + '.json')
    return None


def load_session():
    try:
        return json.loads(read_text(SESSION) or '{}')
    except ValueError:
        return {}


def load_all():
    out = dict(load_session())
    for folder, prefix in ((ACC, 'sh_u_'), (ANA, 'sh_d_')):
        for name in os.listdir(folder):
            if name.endswith('.json'):
                txt = read_text(os.path.join(folder, name))
                if txt:
                    out[prefix + name[:-5]] = txt
    return out


def save_key(key, value):
    p = key_path(key)
    if p is None:  # сессия
        s = load_session()
        if value is None:
            s.pop(key, None)
        else:
            s[key] = value
        write_text(SESSION, json.dumps(s, ensure_ascii=False, indent=2))
    elif value is None:
        if os.path.exists(p):
            os.remove(p)
    else:
        try:
            value = json.dumps(json.loads(value), ensure_ascii=False, indent=2)
        except ValueError:
            pass
        write_text(p, value)


def list_files():
    res = []
    for n in os.listdir(FIL):
        if n.endswith('.json'):
            try:
                m = json.loads(read_text(os.path.join(FIL, n)))
                res.append({'id': n[:-5], 'acc': m.get('acc', ''), 'name': m.get('name', '')})
            except ValueError:
                pass
    return res


class H(BaseHTTPRequestHandler):
    server_version = 'SberHealthLocal/1.0'

    def log_message(self, *a):
        pass

    def send(self, code, body=b'', ctype='application/json; charset=utf-8', extra=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > MAX_BODY:
            raise ValueError('too big')
        return self.rfile.read(n)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ('/', '/index.html'):
            with open(os.path.join(BASE, 'index.html'), 'rb') as f:
                return self.send(200, f.read(), 'text/html; charset=utf-8')
        if u.path == '/api/db':
            with LOCK:
                return self.send(200, json.dumps(load_all(), ensure_ascii=False).encode('utf-8'))
        if u.path == '/api/files':
            with LOCK:
                return self.send(200, json.dumps(list_files(), ensure_ascii=False).encode('utf-8'))
        m = re.match(r'^/api/file/([a-z0-9]+)$', u.path)
        if m and FID.match(m.group(1)):
            fid = m.group(1)
            try:
                meta = json.loads(read_text(os.path.join(FIL, fid + '.json')))
                with open(os.path.join(FIL, fid + '.bin'), 'rb') as f:
                    data = f.read()
            except (OSError, ValueError, TypeError):
                return self.send(404)
            return self.send(200, data, meta.get('type') or 'application/octet-stream',
                             {'X-Name': quote(meta.get('name', ''))})
        self.send(404)

    def do_POST(self):
        if urlparse(self.path).path != '/api/db':
            return self.send(404)
        try:
            o = json.loads(self.body().decode('utf-8'))
            if not KEY.match(o.get('key', '')):
                return self.send(400)
            with LOCK:
                save_key(o['key'], o.get('value'))
            self.send(200, b'{"ok":true}')
        except Exception:
            self.send(400)

    def do_PUT(self):
        u = urlparse(self.path)
        m = re.match(r'^/api/file/([a-z0-9]+)$', u.path)
        if not (m and FID.match(m.group(1))):
            return self.send(404)
        q = parse_qs(u.query)
        try:
            data = self.body()
        except ValueError:
            return self.send(413)
        fid = m.group(1)
        meta = {'acc': q.get('acc', [''])[0], 'name': q.get('name', [''])[0], 'type': q.get('type', [''])[0]}
        with LOCK:
            with open(os.path.join(FIL, fid + '.bin'), 'wb') as f:
                f.write(data)
            write_text(os.path.join(FIL, fid + '.json'), json.dumps(meta, ensure_ascii=False))
        self.send(200, b'{"ok":true}')

    def do_DELETE(self):
        u = urlparse(self.path)
        if u.path != '/api/files':
            return self.send(404)
        acc = parse_qs(u.query).get('acc', [''])[0]
        with LOCK:
            for f in list_files():
                if f['acc'] == acc and FID.match(f['id']):
                    for ext in ('.bin', '.json'):
                        try:
                            os.remove(os.path.join(FIL, f['id'] + ext))
                        except OSError:
                            pass
        self.send(200, b'{"ok":true}')


def main():
    port = int(os.environ.get('PORT', '8765'))
    srv = None
    for p in range(port, port + 20):
        try:
            srv = ThreadingHTTPServer(('127.0.0.1', p), H)
            port = p
            break
        except OSError:
            continue
    if srv is None:
        sys.exit('Не удалось занять порт. Закройте другие копии сервера.')
    url = 'http://127.0.0.1:%d/' % port
    print('Сбер Здоровье (локальный прототип)\nАдрес: ' + url + '\nДанные: ' + DATA + '\nОстановить: Ctrl+C')
    if '--no-browser' not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
