import json,secrets,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"ops"))
from guard import guard,k,ROOT
guard()
import subprocess
r=subprocess.run(["kubectl","--context","kind-dva-course","get","namespace","monitoring","-o","json"],capture_output=True,text=True)
if r.returncode==0:
 assert json.loads(r.stdout)["metadata"].get("labels",{}).get("course")=="dva"
else:k("create","-f","-",input=json.dumps({"apiVersion":"v1","kind":"Namespace","metadata":{"name":"monitoring","labels":{"course":"dva"}}}),text=True)
p=ROOT/".runtime/grafana-admin.txt"
if p.exists():raise SystemExit("Admin file exists: inspect the prior setup; do not reset blindly")
password=secrets.token_urlsafe(24)
p.parent.mkdir(mode=0o700,exist_ok=True)
with p.open("x") as f:f.write(password+"\n")
os.chmod(p,0o600)
k("create","-f","-",input=json.dumps({"apiVersion":"v1","kind":"Secret","metadata":{"name":"grafana-admin","namespace":"monitoring"},"type":"Opaque","stringData":{"password":password}}),text=True)
print("Grafana credential saved privately to .runtime/grafana-admin.txt; do not publish it")
