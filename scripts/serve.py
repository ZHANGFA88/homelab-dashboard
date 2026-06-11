#!/usr/bin/env python3
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import os, json, secrets, time, mimetypes, urllib.parse, urllib.request

BASE=Path(__file__).resolve().parents[1]
os.chdir(BASE)

def load_env_file(path):
    try:
        for line in Path(path).read_text().splitlines():
            line=line.strip()
            if not line or line.startswith('#') or '=' not in line: continue
            k,v=line.split('=',1)
            os.environ.setdefault(k.strip(), v.strip().strip('\"').strip("'"))
    except FileNotFoundError:
        pass

load_env_file(BASE/'.env')
CONFIG_PATH=Path(os.environ.get('CONFIG_PATH', BASE/'config.json'))
MEDIA_EXTS={'.jpg','.jpeg','.png','.webp'}
ADMIN_PASSWORD=os.environ.get('DASHBOARD_ADMIN_PASSWORD')
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

def cfg_get():
    return read_json(CONFIG_PATH)

def emby_cfg():
    cfg=cfg_get()
    e=(cfg.get('emby') or {})
    svc=(cfg.get('services') or {}).get('emby') or {}
    return {
        'enabled': e.get('enabled', True),
        'internalUrl': (e.get('internalUrl') or svc.get('url') or 'http://127.0.0.1:8096').rstrip('/'),
        'publicUrl': (e.get('publicUrl') or 'http://192.168.1.205:8096').rstrip('/'),
        'apiKey': os.environ.get('EMBY_API_KEY') or os.environ.get('EMBY_TOKEN') or e.get('apiKey') or e.get('token'),
        'userId': os.environ.get('EMBY_USER_ID') or e.get('userId'),
    }

def emby_api(path, timeout=10):
    e=emby_cfg()
    if not e.get('enabled') or not e.get('apiKey'):
        raise RuntimeError('emby api not configured')
    url=e['internalUrl'] + path
    req=urllib.request.Request(url, headers={'X-Emby-Token': e['apiKey']})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))

def emby_user_id():
    e=emby_cfg()
    if e.get('userId'):
        return e['userId']
    users=emby_api('/Users', timeout=10)
    if not users:
        raise RuntimeError('no emby users')
    return users[0]['Id']


ADULT_KEYWORDS={
    '成人','三级','福利姬','写真','无码','有码','女优','巨乳','人妻','熟女','素人','痴女','出轨','不伦','调教','凌辱','痴汉','偷拍','流出','约炮','口交','性交','高潮','中出','颜射','潮吹','乳交','肛交','强奸','乱伦','ntr','av','jav','fc2','heyzo','一本道','caribbeancom','tokyo-hot','1pondo','pacopacomama','carib','麻豆','swag','onlyfans','porn','xxx','hentai','adult','sex','erotic','r18','r-18','uncensored','censored','nude','naked',
    'midv','kwbd','juy','juq','snos','ssis','ipzz','ipx','mide','pred','abp','abw','miaa','stars','ssni','dass','mukd','meyd','rbd','vec','dvaj','jul','mdyd','iptd','pppd','dvdms','fss','cjod','atid','venu','nsfs','mimk','roe','hunt','hmn','adn','sdde','juvr','waaa','mird','mifd','dasd','ebod','apns','shkd','snis','soe','jux','miae','ipz','meyd'
}
ADULT_RATING_MARKERS={'xxx','nc-17','r18','r-18','adult','x'}

def is_adult_item(it):
    parts=[]
    for k in ('Name','OriginalTitle','SortName','Path','Overview','OfficialRating','CustomRating','Type'):
        v=it.get(k)
        if v: parts.append(str(v))
    for tag in (it.get('Tags') or []): parts.append(str(tag))
    for g in (it.get('Genres') or []): parts.append(str(g))
    text=' '.join(parts).lower()
    rating=str(it.get('OfficialRating') or it.get('CustomRating') or '').lower()
    if any(m in rating for m in ADULT_RATING_MARKERS): return True
    if any(k in text for k in ADULT_KEYWORDS): return True
    # 常见番号模式：ABC-123 / ABCD-123 等，避免误伤纯中文影视标题。
    import re
    if re.search(r'\b[a-z]{2,6}[-_ ]?\d{2,5}\b', text): return True
    return False

def is_adult_path_title(path):
    text=str(path).lower()
    if any(k in text for k in ADULT_KEYWORDS): return True
    import re
    return bool(re.search(r'\b[a-z]{2,6}[-_ ]?\d{2,5}\b', text))

def emby_recent(limit=10):
    uid=emby_user_id()
    qs=urllib.parse.urlencode({
        'Limit': max(30,min(200,int(limit)*8)),
        'Fields': 'Path,DateCreated,PrimaryImageAspectRatio,ProductionYear,Genres,Tags,OfficialRating,CustomRating,OriginalTitle,SortName',
        'ImageTypeLimit': 1,
        'EnableImageTypes': 'Primary',
    })
    items=emby_api(f'/Users/{urllib.parse.quote(uid)}/Items/Latest?{qs}', timeout=15)
    e=emby_cfg()
    out=[]
    for it in items:
        if is_adult_item(it):
            continue
        iid=str(it.get('Id') or '')
        if not iid: continue
        img_tag=(it.get('ImageTags') or {}).get('Primary')
        # 跳过没有主海报的 Emby 项目，避免首页裂图/空图。
        if not img_tag:
            continue
        name=it.get('Name') or '未命名'
        year=it.get('ProductionYear')
        title=f'{name} ({year})' if year and str(year) not in str(name) else name
        poster=f'/api/emby/image/{urllib.parse.quote(iid)}'
        poster += '?tag=' + urllib.parse.quote(str(img_tag))
        server_id=str(it.get('ServerId') or '')
        item_url=f"{e['publicUrl']}/web/index.html#!/item?id={urllib.parse.quote(iid)}"
        if server_id:
            item_url += '&serverId=' + urllib.parse.quote(server_id)
        out.append({
            'id': iid,
            'title': title,
            'category': it.get('Type') or 'Emby',
            'mtime': it.get('DateCreated'),
            'poster': poster,
            'href': item_url,
            'source': 'emby-api',
        })
        if len(out)>=limit: break
    return out

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
                            if is_adult_path_title(f):
                                continue
                            st=f.stat()
                            if st.st_size <= 0:
                                continue
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
                try:
                    items=emby_recent(limit)
                except Exception:
                    items=recent_posters(limit)
                return self._json(200, {'ok':True,'items':items})
            except Exception as e:
                return self._json(500, {'ok':False,'error':str(e)})
        if self.path.startswith('/api/emby/image/'):
            try:
                item_id=urllib.parse.unquote(urllib.parse.urlparse(self.path).path.rsplit('/',1)[-1])
                if not item_id:
                    return self._json(400, {'ok':False,'error':'missing item id'})
                e=emby_cfg()
                if not e.get('apiKey'):
                    return self._json(503, {'ok':False,'error':'emby api not configured'})
                q=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                params={'quality':'88','maxWidth':'480'}
                if (q.get('tag') or [''])[0]:
                    params['tag']=(q.get('tag') or [''])[0]
                url=e['internalUrl'] + f"/Items/{urllib.parse.quote(item_id)}/Images/Primary?" + urllib.parse.urlencode(params)
                req=urllib.request.Request(url, headers={'X-Emby-Token': e['apiKey']})
                with urllib.request.urlopen(req, timeout=12) as r:
                    data=r.read()
                    ctype=r.headers.get('Content-Type') or 'image/jpeg'
                if not data:
                    return self._json(404, {'ok':False,'error':'empty image'})
                self.send_response(200)
                self.send_header('Content-Type', ctype)
                self.send_header('Cache-Control','public, max-age=600')
                self.send_header('Content-Length',str(len(data)))
                self.end_headers(); self.wfile.write(data); return
            except Exception as e:
                return self._json(502, {'ok':False,'error':str(e)})
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
                if not ADMIN_PASSWORD:
                    return self._json(403, {'ok':False,'error':'admin password not configured'})
                if secrets.compare_digest(str(data.get('password','')), str(ADMIN_PASSWORD)):
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
