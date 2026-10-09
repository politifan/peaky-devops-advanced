"""Author acceptance only: real browser screens from the isolated live lab."""
import argparse
import http.cookiejar
import json
from pathlib import Path
import shutil
import urllib.request
from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser()
parser.add_argument('mode',choices=['alert','dashboard'])
args=parser.parse_args()
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
    url='http://127.0.0.1:19090/alerts' if args.mode=='alert' else 'http://127.0.0.1:13000/d/dva-ticket/ticket-lab-operations?from=now-10m&to=now&refresh=5s'
    page.goto(url,wait_until='networkidle',timeout=60000)
    phrase='TicketReadinessLost' if args.mode=='alert' else 'Requests per second'
    page.get_by_text(phrase,exact=False).first.wait_for(timeout=30000)
    page.wait_for_timeout(4000)
    text=page.locator('body').inner_text()
    if args.mode=='dashboard':
        assert 'Ready instances' in text or 'Ready' in text
        assert 'Sign in' not in text
    Path('evidence/'+args.mode+'-browser.txt').write_text(text)
    page.screenshot(path='evidence/'+args.mode+'-browser.png',full_page=True)
    browser.close()
print('Captured live '+args.mode+' browser UI without exporting authentication state')
