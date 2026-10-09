import json
import urllib.request
import uuid

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://ticket-api:8000/ready', timeout=10) as response:
    assert response.status == 200
title = 'helm-check-' + uuid.uuid4().hex
request = urllib.request.Request('http://ticket-api:8000/tickets',
    data=json.dumps({'title': title}).encode(), headers={'Content-Type': 'application/json'}, method='POST')
with opener.open(request, timeout=10) as response:
    assert response.status == 201
    created = json.load(response)
with opener.open(f'http://ticket-api:8000/tickets/{created["id"]}', timeout=10) as response:
    assert response.status == 200 and json.load(response) == created
print('Internal readiness and create/read succeeded')
