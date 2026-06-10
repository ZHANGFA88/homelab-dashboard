#!/usr/bin/env python3
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import os, json, secrets, time, mimetypes, urllib.parse

BASE=Path(__file__).resolve().parents[1]
os.chdir(BASE)
CONFIG_PATH=Path(os.environ.get('CONFIG_PATH', BASE/'config.json'))
MEDIA_EXTS={'.jpg','.jpeg','.png','.webp'}
ADMIN_PASSWORD=os.environ.get('DASHBOARD_ADMIN_PASSWORD','admin123')
SESSIONS={}
SESSION_TTL=3600

SAFE_TOP_LEVEL={'refreshSeconds'}
SAFE_NESTED={
    'surge': {'enabled','baseUrl','maxEvents'},
    'ubnt': {'enabled','baseUrl','site'},
    'thresholds': {'diskFreeGbWarn','diskFreeGbCritical','httpTimeoutSec'},
}

def read_json(path):
    with open(path,'r',encoding='utf-8') as f:
        return json.load(f)

def write_json(path,obj):
    tmp=path.with_suffix(path.suffix+'.tmp')
    with open(tmp,'w',encoding='utf-8') as f:
        json.dump(obj,f,ensure_ascii=False,indent=2)
        f.write('\n')
    tmp.replace(path)

def public_config(cfg):
    return {
        'refreshSeconds': cfg.get('refreshSeconds'),
        'surge': {k: cfg.get('surge',{}).get(k) for k in ['enabled','baseUrl','maxEvents']},
        'ubnt': {k: cfg.get('ubnt',{}).get(k) for k in ['enabled','baseUrl','site']},
        'thresholds': {k: cfg.get('thresholds',{}).get(k) for k in ['diskFreeGbWarn','diskFreeGbCritical','httpTimeoutSec']},
    }

def merge_safe(cfg, patch):
    out=json.loads(json.dumps(cfg))
    for k in SAFE_TOP_LEVEL:
        if k in patch:
            out[k]=patch[k]
    for section, keys in SAFE_NESTED.items():
        if section in patch and isinstance(patch[section], dict):
            out.setdefault(section,{})
            for k in keys:
                if k in patch[section]:
                    out[section][k]=patch[section][k]
    return out

def valid_token(token):
    if not token: return False
    now=time.time()
    expired=[t for t,ts in SESSIONS.items() if now-ts>SESSION_TTL]
    for t in expired: SESSIONS.pop(t,None)
    return token in SESSIONS

def media_roots():
    try:
        cfg=read_json(CONFIG_PATH)
        roots=[]
        for v in (cfg.get('paths') or {}).values():
            if v:
                p=Path(v).expanduser().resolve()
                if p.exists(): roots.append(p)
        return roots
    except Exception:
        return []

def is_under(path, root):
    try:
        path=Path(path).resolve(); root=Path(root).resolve()
        return path == root or root in path.parents
    except Exception:
        return False

def poster_title(path):
    p=Path(path)
    parent=p.parent.name
    if parent.lower().startswith('season ') and p.parent.parent.name:
        return p.parent.parent.name
    return parent or p.stem

def poster_category(path, roots):
    p=Path(path).resolve()
    for root in roots:
        if is_under(p, root):
            try:
                rel=p.relative_to(root)
                if len(rel.parts)>2 and rel.parts[0].lower() in ('mp','media'):
                    return rel.parts[1]
                return rel.parts[0] if len(rel.parts)>2 else '媒体库'
            except Exception:
                pass
    return '媒体库'

def recent_posters(limit=10):
    roots=media_roots()
    items=[]
    names=('poster.jpg','poster.png','folder.jpg','cover.jpg')
    for root in roots:
        if not root.exists(): continue
        for name in names:
            try:
                for f in root.rglob(name):
                    if f.is_file() and f.suffix.lower() in MEDIA_EXTS:
                        try:
                            if f.parent.name.lower().startswith('season ') and (f.parent.parent/'poster.jpg').exists():
                                continue
                            st=f.stat()
                            items.append({'path':str(f.resolve()),'title':poster_title(f),'category':poster_category(f, roots),'mtime':st.st_mtime,'size':st.st_size})
                        except Exception:
                            pass
            except Exception:
                pass
    items.sort(key=lambda x:x.get('mtime') or 0, reverse=True)
    out=[]; seen=set()
    for it in items:
        key=str(Path(it['path']).parent)
        if key in seen: continue
        seen.add(key)
        token=urllib.parse.quote(it['path'], safe='')
        out.append({'title':it['title'],'category':it['category'],'mtime':it['mtime'],'poster':'/api/media/poster?path='+token})
        if len(out)>=limit: break
    return out

class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, code, obj):
        body=json.dumps(obj,ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body_json(self):
        n=int(self.headers.get('Content-Length','0') or 0)
        if n<=0: return {}
        return json.loads(self.rfile.read(n).decode('utf-8'))

    def do_GET(self):
        if self.path.startswith('/api/emby/recent'):
            try:
                q=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                limit=max(1,min(30,int((q.get('limit') or ['10'])[0])))
                return self._json(200, {'ok':True,'items':recent_posters(limit)})
            except Exception as e:
                return self._json(500, {'ok':False,'error':str(e)})
        if self.path.startswith('/api/media/poster'):
            try:
                q=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                raw=(q.get('path') or [''])[0]
                path=Path(raw).expanduser().resolve()
                roots=media_roots()
                if not any(is_under(path, root) for root in roots):
                    return self._json(403, {'ok':False,'error':'forbidden'})
                if not path.exists() or path.suffix.lower() not in MEDIA_EXTS:
                    return self._json(404, {'ok':False,'error':'not found'})
                data=path.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', mimetypes.guess_type(str(path))[0] or 'image/jpeg')
                self.send_header('Cache-Control','public, max-age=300')
                self.send_header('Content-Length',str(len(data)))
                self.end_headers(); self.wfile.write(data); return
            except Exception as e:
                return self._json(500, {'ok':False,'error':str(e)})
        if self.path.startswith('/api/admin/config'):
            token=self.headers.get('X-Dashboard-Token')
            if not valid_token(token):
                return self._json(401, {'ok':False,'error':'unauthorized'})
            try:
                cfg=read_json(CONFIG_PATH)
                return self._json(200, {'ok':True,'config':public_config(cfg)})
            except Exception as e:
                return self._json(500, {'ok':False,'error':str(e)})
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith('/api/admin/login'):
            try:
                data=self._body_json()
                if secrets.compare_digest(str(data.get('password','')), ADMIN_PASSWORD):
                    token=secrets.token_urlsafe(32)
                    SESSIONS[token]=time.time()
                    return self._json(200, {'ok':True,'token':token,'ttl':SESSION_TTL})
                return self._json(403, {'ok':False,'error':'bad password'})
            except Exception as e:
                return self._json(400, {'ok':False,'error':str(e)})
        if self.path.startswith('/api/admin/config'):
            token=self.headers.get('X-Dashboard-Token')
            if not valid_token(token):
                return self._json(401, {'ok':False,'error':'unauthorized'})
            try:
                data=self._body_json()
                patch=data.get('config',{})
                cfg=read_json(CONFIG_PATH)
                new_cfg=merge_safe(cfg, patch)
                write_json(CONFIG_PATH,new_cfg)
                return self._json(200, {'ok':True,'config':public_config(new_cfg)})
            except Exception as e:
                return self._json(400, {'ok':False,'error':str(e)})
        return self._json(404, {'ok':False,'error':'not found'})

ThreadingHTTPServer((os.environ.get('DASHBOARD_HOST','127.0.0.1'), int(os.environ.get('DASHBOARD_PORT','8765'))), Handler).serve_forever()
