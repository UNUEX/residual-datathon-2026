"""Local review workspace. Run: python -m src.serve --port 8765."""
import argparse
import csv
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
LOCK = Lock()
STATUSES = {'pending', 'confirmed', 'accepted'}


def read_json(path, fallback):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else fallback


def load_state(root=ROOT):
    local=read_json(root/'private_outputs/quality.json',None)
    if local is None:
        local={'summary':read_json(root/'reports/quality_summary.json',{}),'records':[]}
    decisions=read_json(root/'private_outputs/reviews.json',{})
    key=local['summary'].get('input_sha256','')
    current=decisions.get(key,{})
    for record in local['records']:
        record['status']=current.get(record['id'],'pending')
    return dict(**local,mode='local' if local['records'] else 'aggregate',
        eda=read_json(root/'reports/findings.json',{}),
        tables=list(csv.DictReader((root/'reports/table_profile.csv').open(encoding='utf-8-sig')))
               if (root/'reports/table_profile.csv').exists() else [])


class Handler(BaseHTTPRequestHandler):
    def reply(self,code,data,content_type='application/json; charset=utf-8',filename=None):
        raw=data if isinstance(data,bytes) else json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(code);self.send_header('Content-Type',content_type)
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        if filename:self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
        self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)

    def valid_host(self):
        return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}

    def do_GET(self):
        if not self.valid_host():return self.reply(403,{'error':'Local access only'})
        path=urlparse(self.path).path
        if path=='/api/state':return self.reply(200,load_state())
        if path=='/api/export':
            state=load_state()
            return self.reply(200,state,'application/json; charset=utf-8','residual-review.json')
        assets={'/':('index.html','text/html'),'/app.js':('app.js','text/javascript'),'/style.css':('style.css','text/css')}
        if path in assets:
            name,mime=assets[path]
            return self.reply(200,(ROOT/'app'/name).read_bytes(),mime+'; charset=utf-8')
        self.reply(404,{'error':'Not found'})

    def do_POST(self):
        if not self.valid_host():return self.reply(403,{'error':'Local access only'})
        origin=self.headers.get('Origin')
        if origin and origin not in {f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'}:
            return self.reply(403,{'error':'Invalid origin'})
        if self.path!='/api/review':return self.reply(404,{'error':'Not found'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<4096:raise ValueError('Invalid body size')
            body=json.loads(self.rfile.read(size))
            state=load_state()
            if body.get('status') not in STATUSES or body.get('id') not in {r['id'] for r in state['records']}:
                raise ValueError('Unknown record or review status')
            with LOCK:
                path=ROOT/'private_outputs/reviews.json'
                reviews=read_json(path,{})
                reviews.setdefault(state['summary']['input_sha256'],{})[body['id']]=body['status']
                path.parent.mkdir(exist_ok=True)
                temp=path.with_suffix('.tmp');temp.write_text(json.dumps(reviews),encoding='utf-8');temp.replace(path)
            self.reply(200,{'ok':True})
        except (ValueError,TypeError,json.JSONDecodeError) as e:self.reply(400,{'error':str(e)})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',type=int,default=8765)
    args=p.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'Residual: http://127.0.0.1:{args.port}',flush=True)
    server.serve_forever()
