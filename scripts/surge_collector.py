#!/usr/bin/env python3
import argparse, json, os, re, time, urllib.request, urllib.error, ipaddress
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(os.environ.get('CONFIG_PATH', BASE / 'config.json'))
DATA = BASE / 'data'
DATA.mkdir(exist_ok=True)
OUT = DATA / 'network_events.json'
CACHE = DATA / 'geoip_cache.json'
TZ = timezone(timedelta(hours=8))

DEFAULT_HOME = {"name":"Home / Mac mini", "city":"Shanghai", "country":"China", "lat":31.2304, "lng":121.4737}
GEO_FIELDS = 'status,country,city,lat,lon,query,isp,org,as,proxy,hosting,message'

def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default

def save_json(path, data):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(path)

def cfg():
    return load_json(CONFIG_PATH, {})

def now_iso():
    return datetime.now(TZ).isoformat(timespec='seconds')

def clean_ip(value):
    if not value:
        return None
    m = re.search(r'((?:\d{1,3}\.){3}\d{1,3})', str(value))
    if not m:
        return None
    ip = m.group(1)
    try:
        obj = ipaddress.ip_address(ip)
        if obj.is_private or obj.is_loopback or obj.is_link_local or obj.is_multicast or obj.is_unspecified:
            return None
    except Exception:
        return None
    return ip

def clean_host(value):
    if not value:
        return ''
    s = str(value).strip()
    if '://' in s:
        s = s.split('://',1)[1]
    s = s.split('/',1)[0]
    if s.startswith('['):
        return s.split(']',1)[0].lstrip('[')
    if ':' in s:
        s = s.rsplit(':',1)[0]
    return s

def latency_ms(req):
    for r in req.get('timingRecords') or []:
        name = (r.get('name') or '').lower()
        if 'tcp' in name or 'connection' in name:
            try: return int(round(float(r.get('durationInMillisecond'))))
            except Exception: pass
    a, b = req.get('startDate'), req.get('setupCompletedDate')
    if isinstance(a,(int,float)) and isinstance(b,(int,float)) and b >= a:
        return int(round((b-a)*1000))
    return None

def surge_get(path, base, key):
    req = urllib.request.Request(base.rstrip('/') + path, headers={'X-Key': key, 'User-Agent': 'homelab-globe-collector/0.1'})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r)

def geo_lookup(ip, cache):
    c = cache.get(ip)
    if c and c.get('status') == 'success' and c.get('lat') is not None:
        return c
    url = f'http://ip-api.com/json/{ip}?fields={GEO_FIELDS}'
    try:
        with urllib.request.urlopen(url, timeout=6) as r:
            data = json.load(r)
    except Exception as e:
        data = {'status':'fail','query':ip,'message':str(e)}
    cache[ip] = data
    return data

def request_to_event(req, geo):
    ip = clean_ip(req.get('remoteAddress'))
    if not ip or geo.get('status') != 'success':
        return None
    domain = clean_host(req.get('URL') or req.get('remoteHost'))
    # 从 TLS SNI 补域名
    if not domain or re.match(r'^\d+\.\d+\.\d+\.\d+$', domain):
        for note in req.get('notes') or []:
            m = re.search(r'SNI:\s*([^\s]+)', note)
            if m:
                domain = clean_host(m.group(1)); break
    if not domain:
        domain = ip
    ts = req.get('startDate') or time.time()
    route = req.get('policyName') or 'UNKNOWN'
    if 'Proxy' in str(req.get('remoteAddress')) and 'Proxy' not in route:
        route += ' / Proxy'
    return {
        'id': req.get('id'),
        'domain': domain,
        'resolved_ip': ip,
        'country': geo.get('country') or 'Unknown',
        'city': geo.get('city') or 'Unknown',
        'lat': geo.get('lat'),
        'lng': geo.get('lon'),
        'protocol': req.get('method') or 'TCP',
        'latency_ms': latency_ms(req),
        'route': route,
        'rule': req.get('rule'),
        'device': req.get('deviceName'),
        'interface': req.get('interface'),
        'in_bytes': req.get('inBytes',0),
        'out_bytes': req.get('outBytes',0),
        'status': req.get('status'),
        'timestamp': ts,
        'isp': geo.get('isp'),
        'org': geo.get('org'),
        'hosting': geo.get('hosting'),
        'proxy_ip': 'Proxy' in str(req.get('remoteAddress'))
    }

def collect_once():
    conf = cfg()
    surge = conf.get('surge', {})
    base = surge.get('baseUrl') or 'http://127.0.0.1:9091'
    key = os.environ.get(surge.get('tokenEnv') or 'SURGE_API_TOKEN') or surge.get('apiKey') or surge.get('token')
    if not key:
        raise SystemExit('missing Surge token: set SURGE_API_TOKEN or config.surge.apiKey')
    recent = surge_get('/v1/requests/recent', base, key)
    requests = recent.get('requests') or []
    cache = load_json(CACHE, {})
    events = []
    seen_ids = set()
    for req in requests:
        rid = req.get('id')
        if rid in seen_ids: continue
        seen_ids.add(rid)
        ip = clean_ip(req.get('remoteAddress'))
        if not ip: continue
        geo = geo_lookup(ip, cache)
        ev = request_to_event(req, geo)
        if ev and ev.get('lat') is not None and ev.get('lng') is not None:
            events.append(ev)
        if len(events) >= int(surge.get('maxEvents', 80)):
            break
    save_json(CACHE, cache)
    events.sort(key=lambda e: e.get('timestamp') or 0, reverse=True)
    home = conf.get('globe', {}).get('home') or surge.get('home') or DEFAULT_HOME
    payload = {
        'home': home,
        'updatedAt': now_iso(),
        'source': 'surge',
        'surge': {'baseUrl': base, 'endpoint': '/v1/requests/recent'},
        'events': events
    }
    save_json(OUT, payload)
    print(f"{now_iso()} wrote {len(events)} events -> {OUT}")
    return payload

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--loop', action='store_true')
    ap.add_argument('--interval', type=float, default=5.0)
    args = ap.parse_args()
    while True:
        try:
            collect_once()
        except Exception as e:
            print(f"{now_iso()} ERROR {e}", flush=True)
        if not args.loop:
            break
        time.sleep(max(1.0, args.interval))

if __name__ == '__main__':
    main()
