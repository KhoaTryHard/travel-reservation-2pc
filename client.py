"""CLI for primitives, concurrent requests, status, and fault injection."""
import argparse,json,uuid
from concurrent.futures import ThreadPoolExecutor
from common import rpc,RpcError

def main():
    p=argparse.ArgumentParser(); p.add_argument('action',choices=['begin','end','abort','status','snapshot','book','load','fault'])
    p.add_argument('--url',default='http://127.0.0.1:8000'); p.add_argument('--token'); p.add_argument('--tx')
    p.add_argument('--airline',default='DEFAULT'); p.add_argument('--hotel',default='DEFAULT')
    p.add_argument('--count',type=int,default=10); p.add_argument('--workers',type=int,default=4)
    p.add_argument('--point',default='after_prepare'); p.add_argument('--mode',choices=['crash','delay'],default='crash'); p.add_argument('--seconds',type=float,default=3)
    a=p.parse_args()
    def book(token):
        r=rpc(a.url,'begin',{'token':token,'airline':a.airline,'hotel':a.hotel},timeout=30)
        return rpc(a.url,'end',{'tx':r['tx']},timeout=30)
    try:
        if a.action=='book': result=book(a.token or str(uuid.uuid4()))
        elif a.action=='load':
            with ThreadPoolExecutor(max_workers=a.workers) as pool: result=list(pool.map(book,[str(uuid.uuid4()) for _ in range(a.count)]))
        else: result=rpc(a.url,a.action,vars(a),timeout=30)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except RpcError as exc:
        print(json.dumps({'error':str(exc),'hint':'Outcome may be unknown. Retry using the SAME token or query status; do not assume abort.'})); raise SystemExit(1)
if __name__=='__main__': main()
