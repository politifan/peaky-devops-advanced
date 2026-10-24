# Acceptance and safety assertions must never be disabled.
if not __debug__:
    raise RuntimeError('Optimized Python is unsupported; unset PYTHONOPTIMIZE and do not use -O')

"""Author acceptance only: real browser screens from the isolated live lab."""
import argparse
import http.cookiejar
import json
import math
import time
from pathlib import Path
import shutil
import urllib.request
import urllib.parse
from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser()
parser.add_argument('mode',choices=['alert','dashboard'])
args=parser.parse_args()

def verify_observed_metrics(timeout=120):
    """Generate only real read traffic; wait for observations, never set metrics."""
    dashboard=json.loads(Path('monitoring/dashboard.json').read_text())
    queries={
        'up':'min by (namespace) (up{job="ticket-pods"})',
        'rps':dashboard['panels'][0]['targets'][0]['expr'],
        'histogram_count':'sum by (namespace) (rate(ticket_http_request_duration_seconds_count{status=~"2.."}[1m]))',
        'ratio':dashboard['panels'][1]['targets'][0]['expr'],
        'p95':dashboard['panels'][2]['targets'][0]['expr'],
    }
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl): return None
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    deadline=time.monotonic()+timeout
    observations=[]
    while time.monotonic()<deadline:
        row={'at_unix':time.time(),'queries':{},'reads':{},'passed':False}
        try:
            for ns,port in [('dev',18220),('stage',18230)]:
                with opener.open(f'http://127.0.0.1:{port}/tickets?limit=1',timeout=3) as response:
                    body=json.load(response)
                    if response.status!=200 or not isinstance(body,list): raise RuntimeError('Read contract failed')
                row['reads'][ns]={'status':200,'returned':len(body)}
            valid=True
            for name,query in queries.items():
                url='http://127.0.0.1:19090/api/v1/query?'+urllib.parse.urlencode({'query':query})
                with opener.open(url,timeout=3) as response: data=json.load(response)
                row['queries'][name]=data
                if data.get('status')!='success' or data.get('warnings'): raise RuntimeError('Prometheus query unsuccessful')
                values={item['metric'].get('namespace'):float(item['value'][1]) for item in data['data']['result']}
                for ns in ['dev','stage']:
                    value=values.get(ns,float('nan'))
                    valid=valid and math.isfinite(value)
                    if name=='up': valid=valid and value==1
                    elif name in {'rps','histogram_count'}: valid=valid and value>0
                    elif name=='ratio': valid=valid and 0<=value<=1
                    else: valid=valid and value>=0
            row['passed']=valid
        except Exception as error:
            row['error_type']=type(error).__name__
        observations.append(row)
        Path('evidence/dashboard-metrics.json').write_text(json.dumps({'passed':row['passed'],'attempts':observations},indent=2))
        if row['passed']: return
        time.sleep(2)
    raise RuntimeError('Dashboard metrics were not observed within the deadline; see dashboard-metrics.json')

if args.mode=='dashboard': verify_observed_metrics()

with sync_playwright() as p:
    chrome=shutil.which('google-chrome') or shutil.which('chromium')
    assert chrome,'A real Chrome installation is required for author UI acceptance'
    browser=p.chromium.launch(executable_path=chrome,headless=True,args=['--no-sandbox'])
    context=browser.new_context(viewport={'width':1440,'height':1050})
    if args.mode=='dashboard':
        jar=http.cookiejar.CookieJar()
        opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        password=Path('.runtime/grafana-admin.txt').read_text().strip()
        request=urllib.request.Request('http://127.0.0.1:13000/login',data=json.dumps({'user':'admin','password':password}).encode(),headers={'Content-Type':'application/json'})
        with opener.open(request,timeout=20) as response: assert response.status==200
        cookies=[{'name':c.name,'value':c.value,'domain':'127.0.0.1','path':c.path,'httpOnly':True,'secure':False} for c in jar]
        assert cookies,'Grafana authenticated browser session was not issued'
        context.add_cookies(cookies)
    page=context.new_page()
    query_results=[]
    seen_panels=set()
    expected_panels={' '.join(panel['targets'][0]['expr'].split()):panel['id'] for panel in json.loads(Path('monitoring/dashboard.json').read_text())['panels']}
    def inspect_query(response):
        if args.mode!='dashboard' or '/api/ds/query' not in response.url: return
        try:
            data=response.json()
            results=data.get('results',{})
            sent=response.request.post_data_json or {}
            panels={expected_panels[' '.join(query.get('expr','').split())] for query in sent.get('queries',[]) if ' '.join(query.get('expr','').split()) in expected_panels}
            if not panels: return
            errors=[key for key,value in results.items() if value.get('error') or int(value.get('status',200))>=400]
            # Store only structural evidence, never request headers/cookies/session state.
            item={'panels':sorted(panels),'http_status':response.status,'errors':errors,'results':{
                key:{'frames':len(value.get('frames',[])),
                     'samples':sum(1 for frame in value.get('frames',[]) for field,column in zip(frame.get('schema',{}).get('fields',[]),frame.get('data',{}).get('values',[])) if field.get('type')=='number' for sample in column if sample is not None and math.isfinite(float(sample)))}
                for key,value in results.items()}}
            query_results.append(item)
            if response.status==200 and not errors and results and all(value['samples']>0 for value in item['results'].values()): seen_panels.update(panels)
        except Exception:
            query_results.append({'http_status':response.status,'parse_error':True})
    page.on('response',inspect_query)
    url='http://127.0.0.1:19090/alerts' if args.mode=='alert' else 'http://127.0.0.1:13000/d/dva-ticket/ticket-lab-operations?from=now-10m&to=now&refresh=5s'
    # Both interfaces poll continuously; an idle network is not readiness.
    page.goto(url,wait_until='domcontentloaded',timeout=60000)
    phrase='TicketReadinessLost' if args.mode=='alert' else 'Requests per second'
    try:
        page.get_by_text(phrase,exact=False).first.wait_for(timeout=30000)
    except Exception:
        page.screenshot(path='evidence/'+args.mode+'-browser-failed.png',full_page=True)
        Path('evidence/'+args.mode+'-browser-failed.txt').write_text(page.locator('body').inner_text())
        raise
    if args.mode=='dashboard':
        until=time.monotonic()+45
        while time.monotonic()<until:
            if seen_panels=={1,2,3,4} and all(not item.get('errors') and not item.get('parse_error') and item['http_status']==200 for item in query_results): break
            page.wait_for_timeout(500)
        else:
            Path('evidence/dashboard-query-responses.json').write_text(json.dumps(query_results,indent=2))
            raise RuntimeError('Grafana queries did not yield data for every panel without errors')
        Path('evidence/dashboard-query-responses.json').write_text(json.dumps(query_results,indent=2))
    else: page.wait_for_timeout(4000)
    text=page.locator('body').inner_text()
    if args.mode=='dashboard':
        assert 'Ready instances' in text or 'Ready' in text
        assert 'Sign in' not in text
        assert not any(phrase in text.lower() for phrase in ['query error','error querying','data source error'])
    Path('evidence/'+args.mode+'-browser.txt').write_text(text)
    page.screenshot(path='evidence/'+args.mode+'-browser.png',full_page=True)
    browser.close()
print('Captured live '+args.mode+' browser UI without exporting authentication state')

