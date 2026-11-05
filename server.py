"""RPC transport and explicit opt-in, one-shot chaos hooks for local demos."""
import argparse,json,os,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from participant import Participant
from coordinator import Coordinator

def main():
    p=argparse.ArgumentParser(); p.add_argument('role',choices=['coordinator','participant'])
    p.add_argument('--port',type=int,required=True); p.add_argument('--db',required=True)
    p.add_argument('--airline',default='http://127.0.0.1:8001'); p.add_argument('--hotel',default='http://127.0.0.1:8002')
    p.add_argument('--capacity',type=int,default=5); p.add_argument('--enable-chaos',action='store_true')
    a=p.parse_args(); fault={}; lock=threading.Lock()
    def hook(point):
        with lock: f=fault.pop(point,None)
        if f:
            if f['mode']=='crash': os._exit(86)
            time.sleep(f.get('seconds',3))
    app=Coordinator(a.db,{'airline':a.airline,'hotel':a.hotel},hook) if a.role=='coordinator' else Participant(a.db,a.capacity)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                size=int(self.headers.get('Content-Length','0'))
                if size>65536: raise ValueError('Request too large')
                d=json.loads(self.rfile.read(size)); action=self.path.strip('/')
                if not isinstance(d,dict): raise ValueError('JSON object required')
                if action=='health': out={'ok':True,'role':a.role}
                elif action=='fault':
                    if not a.enable_chaos: raise ValueError('Chaos disabled')
                    points=['before_prepare','after_prepare','after_votes','after_decision','after_ack_airline','before_commit','after_commit']
                    if d.get('point') not in points or d.get('mode') not in ('crash','delay'): raise ValueError('Invalid fault')
                    if not 0<=d.get('seconds',3)<=30: raise ValueError('Delay must be 0..30 seconds')
                    with lock: fault[d['point']]=d
                    out={'armed':d['point']}
                else:
                    hook('before_'+action); out=app.handle(action,d); hook('after_'+action)
            except Exception as exc: out={'error':str(exc)}
            b=json.dumps(out).encode()
            try:
                self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
            except (BrokenPipeError,ConnectionResetError): pass
        def log_message(self,*args): pass
    if a.role=='coordinator':
        def recovery():
            while True:
                try: app.recover()
                except Exception as exc: print('Recovery error:',exc,flush=True)
                time.sleep(.5)
        threading.Thread(target=recovery,daemon=True).start()
    print(f'{a.role} listening on 127.0.0.1:{a.port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',a.port),Handler).serve_forever()
if __name__=='__main__': main()
