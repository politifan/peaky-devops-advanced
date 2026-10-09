from pathlib import Path
import secrets,sys,os
ns=sys.argv[1]
assert ns in ['dev','stage']
root=Path(__file__).resolve().parents[1]/'foundation/.runtime'
root.mkdir(mode=0o700,exist_ok=True)
names=[root/(ns+x) for x in ['.db.env','.app.env','.init.env']]
if any(p.exists() for p in names):
    if not all(p.exists() for p in names):raise RuntimeError('Неполная настройка: сначала осмотрите существующие файлы')
    print('Existing credentials retained');raise SystemExit(0)
admin=secrets.token_hex(24);app=secrets.token_hex(24)
texts=[f'POSTGRES_PASSWORD={admin}\nPOSTGRES_DB=postgres\n',f'DATABASE_URL=postgresql+psycopg://ticket_app:{app}@db:5432/ticket_lab\n',f'ADMIN_PASSWORD={admin}\nAPP_PASSWORD={app}\n']
for p,text in zip(names,texts):
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:f.write(text)
print('Created private training credentials; values not printed')
