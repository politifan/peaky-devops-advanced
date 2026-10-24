"""Bounded passive metadata collector, hosted disposable Linux CI ONLY.

No HTTP requests, packet payload export, application retry, rule/sysctl writes.
CLI is not executed by offline tests. Raw API/log/packet text is never saved.
"""
import argparse,contextlib,datetime,ipaddress,json,os,re,signal,subprocess,sys,threading,time,uuid
from pathlib import Path

CTX='kind-dva-course'
PORT=8000
MAX_BYTES=20*1024*1024
MAX_ROWS=12000
MAX_OBJECT=1024*1024
SYN_FILTER='ip and tcp port 8000 and (tcp[tcpflags] & (tcp-syn|tcp-rst) != 0) and (ip[2:2] = ((ip[0] & 0x0f) << 2) + ((tcp[12] & 0xf0) >> 2))'

def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def ip(value):
 try:return str(ipaddress.IPv4Address(value))
 except (ValueError,TypeError):return None

def pod_meta(obj):
 m=obj.get('metadata',{});s=obj.get('status',{})
 return {'kind':'Pod','name':m.get('name'),'namespace':m.get('namespace'),'uid':m.get('uid'),
 'resourceVersion':m.get('resourceVersion'),'ip':ip(s.get('podIP')),'node':obj.get('spec',{}).get('nodeName'),
 'deletionTimestamp':m.get('deletionTimestamp'),'revision':m.get('labels',{}).get('pod-template-hash'),
 'owners':[{'uid':r.get('uid'),'kind':r.get('kind'),'name':r.get('name')} for r in m.get('ownerReferences',[])],
 'phase':s.get('phase'),'conditions':[{'type':x.get('type'),'status':x.get('status'),'lastTransitionTime':x.get('lastTransitionTime')} for x in s.get('conditions',[])],
 'containers':[{'name':x.get('name'),'ready':x.get('ready'),'restartCount':x.get('restartCount'),'imageID':x.get('imageID'),'containerID':x.get('containerID'),
 'terminated':{k:x.get('state',{}).get('terminated',{}).get(k) for k in ['exitCode','reason','startedAt','finishedAt']}} for x in s.get('containerStatuses',[])]}

def endpoint_meta(obj):
 m=obj.get('metadata',{})
 return {'kind':'EndpointSlice','namespace':m.get('namespace'),'uid':m.get('uid'),'resourceVersion':m.get('resourceVersion'),
 'service':m.get('labels',{}).get('kubernetes.io/service-name'),'addressType':obj.get('addressType'),
 'ports':[{k:x.get(k) for k in ['name','port','protocol']} for x in obj.get('ports',[])],
 'endpoints':[{'ips':[ip(v) for v in x.get('addresses',[]) if ip(v)],'conditions':{k:x.get('conditions',{}).get(k) for k in ['ready','serving','terminating']},
 'node':x.get('nodeName'),'target':{k:x.get('targetRef',{}).get(k) for k in ['kind','namespace','name','uid']}} for x in obj.get('endpoints',[])]}

def service_meta(obj):
 s=obj.get('spec',{});m=obj.get('metadata',{})
 return {'uid':m.get('uid'),'namespace':m.get('namespace'),'resourceVersion':m.get('resourceVersion'),
 'vip':ip(s.get('clusterIP')),'selector':s.get('selector'),'sessionAffinity':s.get('sessionAffinity'),
 'internalTrafficPolicy':s.get('internalTrafficPolicy'),'externalTrafficPolicy':s.get('externalTrafficPolicy'),
 'publishNotReadyAddresses':s.get('publishNotReadyAddresses',False),
 'ports':[{k:x.get(k) for k in ['port','targetPort','protocol','nodePort']} for x in s.get('ports',[])]}

def conntrack_meta(line,vips):
 tuples=re.findall(r'src=(\S+) dst=(\S+) sport=(\d+) dport=(\d+)',line)
 if len(tuples)!=2 or ip(tuples[0][1]) not in vips:return None
 if int(tuples[0][3])!=PORT:return None
 result=[]
 for src,dst,sport,dport in tuples:
  if not ip(src) or not ip(dst):return None
  result.append({'src':src,'dst':dst,'sport':int(sport),'dport':int(dport)})
 state=next((v for v in ['SYN_SENT','SYN_RECV','ESTABLISHED','TIME_WAIT','CLOSE','FIN_WAIT','CLOSE_WAIT','LAST_ACK'] if re.search(r'\b'+v+r'\b',line)),None)
 event=next((v for v in ['NEW','UPDATE','DESTROY'] if re.search(r'\[\s*'+v+r'\s*\]',line)),None)
 return {'event':event,'state':state,'unreplied':'[UNREPLIED]' in line,'assured':'[ASSURED]' in line,'original':result[0],'reply':result[1]}

def syn_meta(line):
 # tcpdump's normal textual summary only; never export raw/unparsed suffix.
 m=re.search(r'(?P<time>\d+\.\d+)\s+(?:(?P<if>\S+)\s+(?P<direction>In|Out)\s+)?IP\s+(?P<src>\d+\.\d+\.\d+\.\d+)\.(?P<sport>\d+)\s+>\s+(?P<dst>\d+\.\d+\.\d+\.\d+)\.(?P<dport>\d+):\s+Flags\s+\[(?P<flags>[A-Z.]+)\]',line)
 if not m:return None
 d=m.groupdict()
 if not ip(d['src']) or not ip(d['dst']) or PORT not in [int(d['sport']),int(d['dport'])] or not any(x in d['flags'] for x in ['S','R']):return None
 seq=re.search(r'\bseq\s+(\d+)',line)
 return {'capture_epoch':d['time'],'interface':d['if'],'direction':d['direction'],'src':d['src'],'sport':int(d['sport']),'dst':d['dst'],'dport':int(d['dport']),'flags':d['flags'],'seq':int(seq.group(1)) if seq else None}

def scoped_nat(text,vips):
 rules=[z for z in text.splitlines() if z.startswith('-A ')]
 selected=[z for z in rules if z.startswith('-A KUBE-SERVICES ') and any('-d '+v+'/32 ' in z for v in vips) and '--dport 8000' in z]
 pending={m for z in selected for m in re.findall(r'-j (KUBE-[A-Z0-9-]+)',z)};seen=set()
 while pending:
  chain=pending.pop()
  if chain in seen:continue
  seen.add(chain)
  children=[z for z in rules if z.startswith('-A '+chain+' ')];selected+=children
  pending.update(m for z in children for m in re.findall(r'-j (KUBE-[A-Z0-9-]+)',z) if m not in seen)
 return selected

def phase_meta(line):
 m=re.search(r'\bstage=(ready|create|read|check)\s+event=([a-z_]+)(.*)',line)
 if not m:return None
 result={'kind':'hook_phase','stage':m.group(1),'event':m.group(2)}
 stamp=re.match(r'(\d{4}-\d\d-\d\dT\S+)\s',line)
 if stamp:result['container_log_utc']=stamp.group(1)
 for k,v in re.findall(r'\b(attempt|id|error_type)=([A-Za-z0-9_]+)',m.group(3)):
  result[k]=int(v) if v.isdigit() else v[:80]
 return result

class JsonObjects:
 def __init__(self):self.buffer='';self.decoder=json.JSONDecoder()
 def feed(self,chunk):
  self.buffer+=chunk
  if len(self.buffer)>MAX_OBJECT:raise ValueError('oversize object')
  result=[]
  while self.buffer.strip():
   self.buffer=self.buffer.lstrip()
   try:obj,end=self.decoder.raw_decode(self.buffer)
   except json.JSONDecodeError:break
   result.append(obj);self.buffer=self.buffer[end:]
  return result

class Sink:
 def __init__(self,folder):
  self.folder=folder;self.lock=threading.Lock();self.bytes=0;self.rows=0;self.truncated=False
 def emit(self,file,obj):
  if file not in ['events.jsonl','rules.jsonl','hook-phases.jsonl']:raise ValueError('sink file allowlist')
  line=json.dumps({'observed_utc':utc(),'monotonic_ns':time.monotonic_ns(),**obj},ensure_ascii=True)+'\n'
  with self.lock:
   if self.rows>=MAX_ROWS or self.bytes+len(line.encode())>MAX_BYTES:self.truncated=True;return
   with (self.folder/file).open('a',encoding='utf-8') as f:f.write(line)
   self.rows+=1;self.bytes+=len(line.encode())

def query(args):
 result=subprocess.run(args,capture_output=True,text=True,timeout=5,check=True)
 if len(result.stdout)>MAX_OBJECT:raise ValueError('query output limit')
 return result.stdout
def kubectl(*args):return ['kubectl','--context',CTX,'--request-timeout=4s',*args]

class Observer:
 def __init__(self,root,duration=600):
  if duration<1 or duration>600:raise ValueError('duration must be <=600')
  self.root=Path(root).resolve();self.duration=duration;self.stop=threading.Event();self.threads=[];self.processes=[];self.errors=[]
  self.hook_uids=set();self.capture_counters={}
 def gate(self):
  if sys.platform!='linux' or os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted' or os.environ.get('RUNNER_OS')!='Linux':raise RuntimeError('hosted disposable Linux required')
  if os.environ.get('DOCKER_HOST') or os.environ.get('DOCKER_CONTEXT'):raise RuntimeError('Docker overrides forbidden')
  endpoint=json.loads(query(['docker','context','inspect','--format','{{json .Endpoints.docker.Host}}']))
  if endpoint not in ['unix:///var/run/docker.sock','unix:///run/docker.sock']:raise RuntimeError('local Docker required')
  sys.path.insert(0,str(self.root/'ops'));from guard import guard
  guard()  # existing cluster-id/UID/localhost Docker binding, no new binding
  self.nodes=query(['kind','get','nodes','--name','dva-course']).split()
  if not self.nodes or len(self.nodes)>4:raise RuntimeError('bounded node inventory required')
  self.node_pids={}
  for node in self.nodes:
   if not re.fullmatch(r'dva-course-(?:control-plane|worker\d*)',node):raise RuntimeError('unexpected node name')
   meta=json.loads(query(['docker','inspect','--format','{"cluster":{{json (index .Config.Labels "io.x-k8s.kind.cluster")}},"running":{{json .State.Running}},"pid":{{.State.Pid}}}',node]))
   if meta['cluster']!='dva-course' or not meta['running']:raise RuntimeError('node identity mismatch')
   self.node_pids[node]=int(meta['pid'])
  self.services=[service_meta(json.loads(query(kubectl('-n',ns,'get','service','ticket-api','-o','json')))) for ns in ['dev','stage']]
  self.vips={x['vip'] for x in self.services}
  if None in self.vips or len(self.vips)!=2:raise RuntimeError('two IPv4 service identities required')
  base=self.root/'evidence'/'passive-routing'
  if (self.root/'evidence').is_symlink() or base.is_symlink():raise RuntimeError('symlink evidence rejected')
  base.mkdir(parents=True,exist_ok=True);self.folder=base/uuid.uuid4().hex;self.folder.mkdir(mode=0o700)
  self.sink=Sink(self.folder)
 def error(self,where,error):
  self.errors.append({'collector':where,'error_type':type(error).__name__})
  self.sink.emit('events.jsonl',{'kind':'collector_error','collector':where,'error_type':type(error).__name__})
 def stream(self,name,args,parse,json_mode=False,allow_eof=False):
  if self.stop.is_set():return
  def diagnostics(proc):
   # No raw stderr export (it can contain arbitrary tool strings). Only known
   # packet/drop counters and error categories; a bounded buffer avoids backpressure.
   size=0
   for line in proc.stderr:
    size+=len(line)
    if size>65536:
     self.error(name+'-stderr',ValueError('diagnostic limit'));proc.terminate();break
    m=re.search(r'(\d+) packets (captured|received by filter|dropped by kernel)',line)
    if m:
     self.capture_counters.setdefault(name,{})[m.group(2)]=int(m.group(1))
     self.sink.emit('events.jsonl',{'collector':name,'kind':'capture_counter','counter':m.group(2),'value':int(m.group(1))})
     if m.group(2)=='dropped by kernel' and int(m.group(1)):self.error(name+'-drops',RuntimeError('packets dropped'))
    elif any(x in line.lower() for x in ['no buffer space','permission denied','not permitted','not found']):self.error(name+'-capability',RuntimeError('capability or event loss'))
  def reader():
   proc=None
   try:
    proc=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1,start_new_session=True);self.processes.append(proc)
    t=threading.Thread(target=diagnostics,args=(proc,),daemon=True);t.start();self.threads.append(t)
    decoder=JsonObjects()
    for line in proc.stdout:
     if self.stop.is_set():break
     if len(line)>MAX_OBJECT:raise ValueError('stream line limit')
     values=decoder.feed(line) if json_mode else [line]
     for value in values:
      result=parse(value)
      if result:self.sink.emit('hook-phases.jsonl' if result.get('kind')=='hook_phase' else 'events.jsonl',{'collector':name,**result})
    if not self.stop.is_set() and (not allow_eof or proc.wait(timeout=1)!=0):raise RuntimeError('collector stream ended before requested stop')
   except Exception as e:self.error(name,e)
  t=threading.Thread(target=reader,name=name,daemon=True);t.start();self.threads.append(t)
 def snapshot(self):
  for node in self.nodes:
   start=time.monotonic_ns()
   try:
    rules=scoped_nat(query(['docker','exec',node,'iptables-save','-t','nat']),self.vips)
    if not rules:self.error('nat-'+node,RuntimeError('assigned NAT graph missing'))
    self.sink.emit('rules.jsonl',{'node':node,'started_monotonic_ns':start,'finished_monotonic_ns':time.monotonic_ns(),'rules':rules})
   except Exception as e:self.error('nat-'+node,e)
 def baseline(self):
  data={'status':'PASSIVE OBSERVATION; not acceptance/root-cause proof','services':self.services,'nodes':[],'duration_limit_seconds':self.duration,'max_bytes':MAX_BYTES,'max_rows':MAX_ROWS,'payload_exported':False}
  for node in self.nodes:
   item={'name':node,'docker_pid':self.node_pids[node]}
   for key,args in {
    'kernel':['docker','exec',node,'uname','-r'],
    'iptables_version':['docker','exec',node,'iptables','--version'],
    'node_image':['docker','inspect','--format','{{.Image}}',node],
    'conntrack_count':['docker','exec',node,'cat','/proc/sys/net/netfilter/nf_conntrack_count'],
    'conntrack_max':['docker','exec',node,'cat','/proc/sys/net/netfilter/nf_conntrack_max'],
    'syn_sent_timeout':['docker','exec',node,'cat','/proc/sys/net/netfilter/nf_conntrack_tcp_timeout_syn_sent']}.items():
    try:item[key]=query(args).strip()[:200]
    except Exception as e:
     item[key]={'error_type':type(e).__name__};self.error('baseline-'+node+'-'+key,e)
   data['nodes'].append(item)
  try:
   cm=json.loads(query(kubectl('-n','kube-system','get','configmap','kube-proxy','-o','json')))
   config=cm.get('data',{}).get('config.conf','')
   match=re.search(r'^mode:\s*([^\n]*)',config,re.M);data['proxy_mode_scalar']=match.group(1).strip(' "') if match else None
   proxies=json.loads(query(kubectl('-n','kube-system','get','pods','-l','k8s-app=kube-proxy','-o','json')))
   data['proxy_images']=[{'node':x.get('spec',{}).get('nodeName'),'images':[c.get('image') for c in x.get('spec',{}).get('containers',[])]} for x in proxies['items']]
   logs=query(kubectl('-n','kube-system','logs','-l','k8s-app=kube-proxy','--tail=150','--timestamps','--prefix'))
   data['proxy_mode_startup_lines']=[z[:400] for z in logs.splitlines() if re.search(r'Using (?:iptables|ipvs|nftables).*Proxier|[Pp]roxy mode',z)][:8]
  except Exception as e:
   data['proxy_error_type']=type(e).__name__;self.error('baseline-proxy',e)
  (self.folder/'metadata.json').write_text(json.dumps(data,indent=2)+'\n')
 def start(self):
  self.gate();self.sink.emit('events.jsonl',{'kind':'collector_started','nodes':self.nodes})
  # All collectors launched before the main thread's metadata queries. No
  # convergence/ready check or sleep is imposed on the application route.
  for ns in ['dev','stage']:
   for resource in ['pods','endpointslices']:
    selector='app=ticket-api' if resource=='pods' else 'kubernetes.io/service-name=ticket-api'
    def parse_event(event,resource=resource,ns=ns):
     if event.get('type')=='ERROR':self.error('watch-'+resource,RuntimeError('watch error'));return None
     obj=event.get('object',{});m=obj.get('metadata',{})
     if resource=='pods' and m.get('name')!='ticket-api-check' and m.get('labels',{}).get('app')!='ticket-api':return None
     if resource=='pods' and m.get('name')=='ticket-api-check' and m.get('uid') not in self.hook_uids and any('running' in c.get('state',{}) or 'terminated' in c.get('state',{}) for c in obj.get('status',{}).get('containerStatuses',[])):
      statuses=obj.get('status',{}).get('containerStatuses',[]);node=obj.get('spec',{}).get('nodeName')
      container=statuses[0].get('containerID','') if len(statuses)==1 else ''
      if node not in self.nodes or not re.fullmatch('containerd://[0-9a-f]{64}',container):
       self.error('hook-container-binding',RuntimeError('immutable container binding unavailable'));return {'watch_type':event.get('type'),'object':pod_meta(obj)}
      self.hook_uids.add(m['uid']);uid=m['uid'];cid=container.split('://',1)[1]
      def parse_log(line,uid=uid,ns=ns,cid=cid,node=node):
       value=phase_meta(line)
       if value:return {'namespace':ns,'hook_uid':uid,'container_id':cid,'node':node,**value}
      # CRI logs are bound to immutable container ID, not a reused Pod name.
      self.stream(ns+'-hook-'+uid,['docker','exec',node,'timeout','--signal=TERM','--kill-after=2s',str(self.duration)+'s','crictl','logs','--timestamps','--follow','--tail=100',cid],parse_log,allow_eof=True)
     return {'watch_type':event.get('type'),'object':pod_meta(obj) if resource=='pods' else endpoint_meta(obj)}
    # Hook Pod may lack app label: all pod objects parsed and strictly filtered
    # before export; env/commands/spec credentials never persisted.
    args=['kubectl','--context',CTX,'--request-timeout='+str(self.duration+5)+'s','-n',ns,'get',resource,'--watch','--output-watch-events','-o','json']
    if resource!='pods':args+=['-l',selector]
    self.stream(ns+'-'+resource,args,parse_event,True)
  for node,pid in self.node_pids.items():
   prefix=['sudo','-n','timeout','--signal=TERM','--kill-after=2s',str(self.duration)+'s','nsenter','--target',str(pid),'--net']
   self.stream(node+'-conntrack',prefix+['conntrack','-E','-p','tcp','--dport',str(PORT),'-o','timestamp,extended'],lambda z:conntrack_meta(z,self.vips))
   # No -w/-A/-X/-XX, no promiscuity. Only TCP SYN/SYNACK/RST headers,
   # scoped to port8000 in this owned disposable node network namespace.
   # IPv4 total length must equal IP+TCP header lengths: even TCP Fast Open
   # SYN-with-data is excluded, so no application payload reaches capture.
   self.stream(node+'-syn',prefix+['tcpdump','-p','-i','any','-nn','-tt','-l','-s','160','-S',SYN_FILTER],syn_meta)
  self.baseline()
 def run(self):
  deadline=time.monotonic()+self.duration
  try:
   self.start()
   while not self.stop.is_set() and time.monotonic()<deadline and not self.sink.truncated:
    self.snapshot();self.stop.wait(1)
  finally:
   if hasattr(self,'sink'):self.finish()
 def finish(self):
  self.stop.set()
  for proc in self.processes:
   if proc.poll() is None:
    with contextlib.suppress(OSError):proc.terminate()
  for t in self.threads:t.join(timeout=1)
  for proc in self.processes:
   if proc.poll() is None:
    with contextlib.suppress(OSError):proc.kill()
   with contextlib.suppress(OSError,subprocess.TimeoutExpired):proc.wait(timeout=1)
  for t in self.threads:t.join(timeout=1)
  leftover=[x.pid for x in self.processes if x.poll() is None]
  if leftover:self.error('cleanup-processes',RuntimeError('collector processes remain'))
  if any(t.is_alive() for t in self.threads):self.error('cleanup-threads',RuntimeError('collector threads remain'))
  for node in self.nodes:
   required={'captured','received by filter','dropped by kernel'}
   if not required.issubset(self.capture_counters.get(node+'-syn',{})):
    self.error('capture-counters-'+node,RuntimeError('capture counters unavailable'))
  final_counters=[]
  for node in self.nodes:
   item={'node':node}
   try:
    item['nf_conntrack_count']=int(query(['docker','exec',node,'cat','/proc/sys/net/netfilter/nf_conntrack_count']).strip())
    stats=query(['sudo','-n','nsenter','--target',str(self.node_pids[node]),'--net','conntrack','-S'])
    item['conntrack_stats']=[{k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',line)} for line in stats.splitlines()]
    if not item['conntrack_stats'] or not all(item['conntrack_stats']):raise ValueError('empty conntrack statistics')
   except Exception as e:
    item['error_type']=type(e).__name__;self.error('final-counters-'+node,e)
   final_counters.append(item)
  summary={'status':'INCONCLUSIVE' if self.errors or self.sink.truncated else 'COLLECTED; analyst must correlate, not PASS',
   'errors':self.errors,'truncated':self.sink.truncated,'rows':self.sink.rows,'bytes':self.sink.bytes,'ended_utc':utc(),
   'leftover_processes':leftover,'final_node_counters':final_counters,'capture_counters':self.capture_counters,
   'limits':'No captured reproduction means NOT REPRODUCED; collector timing can affect race; no kernel/root-cause repair claim.'}
  (self.folder/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')

def main():
 if sys.flags.optimize:raise RuntimeError('optimized Python unsupported')
 parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--duration',type=int,default=600);args=parser.parse_args()
 observer=Observer(args.root,args.duration)
 signal.signal(signal.SIGTERM,lambda *_:observer.stop.set())
 signal.signal(signal.SIGINT,lambda *_:observer.stop.set())
 observer.run()
if __name__=='__main__':main()
