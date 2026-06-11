#!/usr/bin/env python3
import json, os, subprocess, time, socket, urllib.request, urllib.error, shutil, re, ssl, http.cookiejar
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE = Path(__file__).resolve().parents[1]

def load_env_file(path):
    try:
        for line in Path(path).read_text().splitlines():
            line=line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k,v=line.split('=',1)
            os.environ.setdefault(k.strip(), v.strip().strip('\"').strip("'"))
    except FileNotFoundError:
        pass

load_env_file(BASE / '.env')
CONFIG_PATH = Path(os.environ.get('CONFIG_PATH', BASE / 'config.json'))
CONFIG = json.loads(CONFIG_PATH.read_text())
DATA = BASE / 'data'
DATA.mkdir(exist_ok=True)

TZ = timezone(timedelta(hours=8))

def now_iso():
    return datetime.now(TZ).isoformat(timespec='seconds')

def run(cmd, timeout=5):
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
        return {'ok': r.returncode == 0, 'code': r.returncode, 'out': r.stdout.strip()}
    except subprocess.TimeoutExpired:
        return {'ok': False, 'code': None, 'out': 'TIMEOUT'}
    except Exception as e:
        return {'ok': False, 'code': None, 'out': repr(e)}

def http_check(url, timeout=4):
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'homelab-dashboard/0.1'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {'ok': 200 <= resp.status < 500, 'status': resp.status, 'ms': int((time.time()-t0)*1000), 'error': None}
    except urllib.error.HTTPError as e:
        return {'ok': e.code < 500, 'status': e.code, 'ms': int((time.time()-t0)*1000), 'error': str(e)}
    except Exception as e:
        return {'ok': False, 'status': None, 'ms': int((time.time()-t0)*1000), 'error': str(e)}

def docker_container(name):
    if not name:
        return {'configured': False}
    r = run(['docker', 'inspect', name, '--format', '{{.State.Status}}|{{.State.Running}}|{{.State.Restarting}}|{{.RestartCount}}|{{.State.OOMKilled}}'], timeout=6)
    if not r['ok']:
        return {'configured': True, 'found': False, 'error': r['out'][:300]}
    parts = r['out'].split('|')
    return {
        'configured': True,
        'found': True,
        'status': parts[0] if len(parts)>0 else None,
        'running': parts[1] == 'true' if len(parts)>1 else False,
        'restarting': parts[2] == 'true' if len(parts)>2 else False,
        'restartCount': int(parts[3]) if len(parts)>3 and parts[3].isdigit() else None,
        'oomKilled': parts[4] == 'true' if len(parts)>4 else False
    }

def docker_summary():
    version = run(['docker','version','-f','{{.Server.Version}}'], timeout=5)
    ps = run(['docker','ps','-a','--format','{{.Names}}\t{{.Status}}'], timeout=8)
    return {'apiOk': version['ok'], 'version': version['out'] if version['ok'] else None, 'error': None if version['ok'] else version['out'], 'containersText': ps['out'][:5000] if ps['ok'] else ''}

def disk_info(path='/'):
    try:
        u = shutil.disk_usage(path)
        return {'path': path, 'totalGb': round(u.total/1024**3,1), 'usedGb': round(u.used/1024**3,1), 'freeGb': round(u.free/1024**3,1), 'usedPct': round(u.used/u.total*100,1)}
    except Exception as e:
        return {'path': path, 'error': str(e)}


def dir_usage(path, timeout=8):
    path=os.path.expanduser(path)
    if not os.path.exists(path):
        return {'path': path, 'exists': False}
    r=run(['du','-sk',path], timeout=timeout)
    if not r['ok']:
        return {'path': path, 'exists': True, 'ok': False, 'error': (r.get('out') or 'timeout')[:160]}
    try:
        kb=int((r['out'].split() or ['0'])[0])
        return {'path': path, 'exists': True, 'ok': True, 'gb': round(kb/1024/1024,2)}
    except Exception as e:
        return {'path': path, 'exists': True, 'ok': False, 'error': str(e)}

def disk_usage_details():
    candidates=[
        ('OpenClaw 工作区', str(BASE)),
        ('大屏日志', str(BASE/'logs')),
        ('大屏数据', str(BASE/'data')),
        ('临时目录', '/tmp'),
        ('Docker 容器数据', os.path.expanduser('~/Library/Containers/com.docker.docker')),
        ('用户缓存', os.path.expanduser('~/Library/Caches')),
        ('下载目录', os.path.expanduser('~/Downloads')),
    ]
    items=[]
    for name,path in candidates:
        d=dir_usage(path, timeout=8)
        d['name']=name
        items.append(d)
    items.sort(key=lambda x: x.get('gb') or 0, reverse=True)
    return {'items': items[:8]}

def path_check(path):
    # Run path probing in a separate Python process with timeout.
    # Some NAS/FUSE mounts can hang the caller; never let dashboard collection block.
    code = r"""
import json, sys, time
from pathlib import Path
t0=time.time(); path=sys.argv[1]
try:
    p=Path(path)
    exists=p.exists()
    is_dir=p.is_dir() if exists else False
    readable=False
    if is_dir:
        try:
            it=iter(p.iterdir())
            next(it, None)
            readable=True
        except Exception:
            readable=False
    print(json.dumps({'path':path,'ok':exists and (not is_dir or readable),'exists':exists,'isDir':is_dir,'readable':readable,'ms':int((time.time()-t0)*1000)}))
except Exception as e:
    print(json.dumps({'path':path,'ok':False,'error':str(e),'ms':int((time.time()-t0)*1000)}))
"""
    r=run(['python3','-c',code,path], timeout=3)
    if not r['ok']:
        return {'path': path, 'ok': False, 'error': r['out'] or 'TIMEOUT', 'timeout': True}
    try:
        return json.loads(r['out'])
    except Exception:
        return {'path': path, 'ok': False, 'error': 'parse_failed', 'raw': r['out'][:200]}

def mp_log_summary():
    r = run(['docker','logs','--tail','260','moviepilot-v2'], timeout=8)
    if not r['ok']:
        return {'ok': False, 'error': r['out'][:300]}
    text = r['out']
    patterns = {
        'cookieCloudFailed': r'CookieCloud同步成功：更新了(\d+)个站点，新增了(\d+)个站点，(\d+)个站点添加失败',
        'qbFailed': r'qbittorrent 登录失败|Connection refused.*8080',
        'webhookFailed': r'webhook - 发送失败',
        'cloudflare': r'检测到Cloudflare',
        'siteLoginFailed': r'登录失败，没有该站点账号或Cookie已失效'
    }
    out={'ok': True, 'errors': text.count('ERROR'), 'warnings': text.count('WARNING')}
    m=list(re.finditer(patterns['cookieCloudFailed'], text))
    if m:
        last=m[-1]
        out['cookieCloud']={'updated': int(last.group(1)), 'added': int(last.group(2)), 'failed': int(last.group(3))}
    out['qbFailed']=bool(re.search(patterns['qbFailed'], text))
    out['webhookFailedCount']=len(re.findall(patterns['webhookFailed'], text))
    out['cloudflareCount']=len(re.findall(patterns['cloudflare'], text))
    out['siteLoginFailedCount']=len(re.findall(patterns['siteLoginFailed'], text))
    return out

def surge_placeholder():
    cfg=CONFIG.get('surge',{})
    if not cfg.get('enabled'):
        return {'enabled': False, 'status': '未启用', 'note': '等待配置 Surge External Controller'}
    # future: query /v1/traffic, /v1/requests etc.
    return {'enabled': True, 'status': '待实现'}

def ubnt_api_get(opener, base, path, timeout=8):
    req = urllib.request.Request(base.rstrip('/') + path, headers={'User-Agent':'homelab-dashboard-ubnt/0.1','Accept':'application/json'})
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8','replace'))

def ubnt_login(cfg):
    base = cfg.get('baseUrl','https://192.168.1.1').rstrip('/')
    username = os.environ.get(cfg.get('usernameEnv') or 'UBNT_USERNAME') or cfg.get('username')
    password = os.environ.get(cfg.get('passwordEnv') or 'UBNT_PASSWORD') or cfg.get('password')
    if not username or not password:
        return None, {'enabled': True, 'ok': False, 'status': '缺少账号', 'note': '需要配置 UniFi 只读账号/密码'}
    ctx = ssl._create_unverified_context()
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), urllib.request.HTTPCookieProcessor(cj))
    body = json.dumps({'username': username, 'password': password, 'remember': False, 'strict': True}).encode()
    req = urllib.request.Request(base + '/api/auth/login', data=body, headers={'Content-Type':'application/json','User-Agent':'homelab-dashboard-ubnt/0.1'}, method='POST')
    try:
        with opener.open(req, timeout=10) as r:
            r.read(500)
        return opener, {'base': base}
    except Exception as e:
        return None, {'enabled': True, 'ok': False, 'status': '登录失败', 'note': str(e)[:220]}

def ubnt_collect():
    cfg=CONFIG.get('ubnt',{})
    if not cfg.get('enabled'):
        return {'enabled': False, 'status': '未启用', 'note': '等待配置 UniFi 只读账号/API'}
    site = cfg.get('site','default')
    opener, state = ubnt_login(cfg)
    if not opener:
        return state
    base = state['base']
    try:
        sites = ubnt_api_get(opener, base, '/proxy/network/api/self/sites').get('data', [])
        devices = ubnt_api_get(opener, base, f'/proxy/network/api/s/{site}/stat/device').get('data', [])
        clients = ubnt_api_get(opener, base, f'/proxy/network/api/s/{site}/stat/sta').get('data', [])
        health = ubnt_api_get(opener, base, f'/proxy/network/api/s/{site}/stat/health').get('data', [])
        sysinfo = ubnt_api_get(opener, base, f'/proxy/network/api/s/{site}/stat/sysinfo').get('data', [])
    except Exception as e:
        return {'enabled': True, 'ok': False, 'status': 'API异常', 'note': str(e)[:220]}
    hmap = {x.get('subsystem'): x for x in health if isinstance(x, dict)}
    wan = hmap.get('wan', {})
    wlan = hmap.get('wlan', {})
    devs=[]
    for d in devices:
        devs.append({
            'name': d.get('name') or d.get('hostname') or d.get('model') or d.get('mac'),
            'type': d.get('type'), 'model': d.get('model'), 'ip': d.get('ip'),
            'version': d.get('version'), 'state': d.get('state'),
            'adopted': d.get('adopted') or d.get('adoption_completed'),
            'connected': not bool(d.get('disconnected')) and bool(d.get('last_seen')),
            'uptime': d.get('uptime'), 'mac': d.get('mac')
        })
    clis=[]
    for c in sorted(clients, key=lambda x: (x.get('rx_bytes-r',0)+x.get('tx_bytes-r',0)), reverse=True)[:20]:
        clis.append({
            'name': c.get('name') or c.get('hostname') or c.get('mac'),
            'ip': c.get('last_ip'), 'mac': c.get('mac'),
            'ap': c.get('last_uplink_name'), 'network': c.get('last_connection_network_name'),
            'wired': c.get('is_wired'), 'radio': c.get('last_radio'),
            'rxRate': c.get('rx_bytes-r',0), 'txRate': c.get('tx_bytes-r',0),
            'signal': c.get('signal'), 'rssi': c.get('rssi'), 'oui': c.get('oui')
        })
    sys0 = sysinfo[0] if sysinfo else {}
    return {
        'enabled': True, 'ok': True, 'status': wan.get('status') or 'ok',
        'note': f"{wan.get('gw_name') or sys0.get('name') or 'UniFi'} · 客户端 {len(clients)} · AP {wlan.get('num_ap','-')}",
        'site': site, 'sites': [{'name':x.get('name'), 'desc':x.get('desc'), 'role':x.get('role')} for x in sites],
        'wan': {
            'status': wan.get('status'), 'ip': wan.get('wan_ip'), 'isp': wan.get('isp_name') or wan.get('isp_organization'),
            'rxRate': wan.get('rx_bytes-r'), 'txRate': wan.get('tx_bytes-r'),
            'gatewayName': wan.get('gw_name') or sys0.get('name'), 'gatewayVersion': wan.get('gw_version'),
            'cpu': (wan.get('gw_system-stats') or {}).get('cpu'), 'mem': (wan.get('gw_system-stats') or {}).get('mem')
        },
        'wlan': {'status': wlan.get('status'), 'clients': wlan.get('num_user'), 'guests': wlan.get('num_guest'), 'aps': wlan.get('num_ap'), 'rxRate': wlan.get('rx_bytes-r'), 'txRate': wlan.get('tx_bytes-r')},
        'counts': {'devices': len(devices), 'clients': len(clients), 'aps': sum(1 for d in devices if d.get('type')=='uap'), 'switches': sum(1 for d in devices if d.get('type')=='usw'), 'gateways': sum(1 for d in devices if d.get('type')=='ugw')},
        'devices': devs, 'clients': clis,
        'controller': {'name': sys0.get('name'), 'hostname': sys0.get('hostname'), 'version': sys0.get('version'), 'uptime': sys0.get('uptime')}
    }

def ubnt_placeholder():
    return ubnt_collect()

def recommendations(report):
    rec=[]
    svc=report['services']
    qb=svc.get('qbittorrent',{})
    if not qb.get('http',{}).get('ok'):
        rec.append({'level':'error','tag':'异常','text':'qBittorrent WebUI 不通，下载链路需要优先恢复'})
    disk=report.get('disk',{})
    free=disk.get('freeGb')
    th=CONFIG['thresholds']
    if isinstance(free,(int,float)) and free < th['diskFreeGbWarn']:
        rec.append({'level':'warn','tag':'存储','text':f'磁盘剩余 {free}GB，建议查看占用明细并清理缓存/旧下载'})
    mp=report.get('moviepilotLogs',{})
    cc=mp.get('cookieCloud') or {}
    if cc.get('failed',0)>0:
        rec.append({'level':'warn','tag':'媒体','text':f'CookieCloud 有 {cc.get("failed")} 个站点失败，回家后可刷新 Cookie/UA'})
    if mp.get('webhookFailedCount',0)>0:
        rec.append({'level':'warn','tag':'通知','text':'MP Webhook 最近发送失败，后续可检查通知地址'})
    if not report.get('surge',{}).get('enabled'):
        rec.append({'level':'info','tag':'网络','text':'接入 Surge Controller 后可统计代理/直连/策略流量'})
    if not report.get('ubnt',{}).get('enabled'):
        rec.append({'level':'info','tag':'网络','text':'接入 UniFi/UBNT 后可展示 WAN、AP、客户端流量排行'})
    if len(rec)<5:
        rec.append({'level':'ok','tag':'自动化','text':'建议后续开启每日微信健康报告'})
    return rec[:8]

def collect():
    timeout=CONFIG['thresholds'].get('httpTimeoutSec',4)
    report={'ts': now_iso(), 'title': CONFIG.get('title','HomeLab'), 'services': {}}
    report['docker']=docker_summary()
    for key,cfg in CONFIG['services'].items():
        item={'name': cfg['name'], 'url': cfg.get('url')}
        if cfg.get('url'):
            item['http']=http_check(cfg['url'], timeout=timeout)
        item['container']=docker_container(cfg.get('container')) if cfg.get('container') else {'configured': False}
        report['services'][key]=item
    report['disk']=disk_info('/')
    report['diskUsage']=disk_usage_details()
    report['paths']={k:path_check(v) for k,v in CONFIG.get('paths',{}).items()}
    report['moviepilotLogs']=mp_log_summary()
    report['surge']=surge_placeholder()
    report['ubnt']=ubnt_placeholder()
    report['recommendations']=recommendations(report)
    # derived kpis
    online=sum(1 for s in report['services'].values() if s.get('http',{}).get('ok') or s.get('container',{}).get('running'))
    total=len(report['services'])
    critical=sum(1 for r in report['recommendations'] if r['level']=='error')
    warn=sum(1 for r in report['recommendations'] if r['level']=='warn')
    report['kpi']={'onlineServices': online, 'totalServices': total, 'critical': critical, 'warnings': warn, 'dailyTrafficGb': None, 'currentMbps': None}
    return report

if __name__=='__main__':
    rep=collect()
    (DATA/'status.json').write_text(json.dumps(rep,ensure_ascii=False,indent=2))
    hist=DATA/'history.jsonl'
    with hist.open('a') as f:
        f.write(json.dumps(rep,ensure_ascii=False)+'\n')
    print(json.dumps(rep,ensure_ascii=False,indent=2))
