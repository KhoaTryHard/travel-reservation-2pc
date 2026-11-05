"""Single coordinator, durable decisions, idempotent replay and ACK tracking."""
import json, threading, uuid
from common import db,event,EVENTS,rpc,RpcError

class Coordinator:
    def __init__(self,path,participants,hook=lambda point:None):
        self.path=path; self.participants=participants; self.hook=hook
        self.mutex=threading.RLock()
        with db(path) as c:
            c.execute(EVENTS)
            c.execute('CREATE TABLE IF NOT EXISTS transactions(tx TEXT PRIMARY KEY, token TEXT UNIQUE, payload TEXT, state TEXT, ack TEXT)')
            # Crash in ACTIVE/PREPARING without a durable decision is safely aborted.
            for row in c.execute("SELECT tx FROM transactions WHERE state IN ('ACTIVE','PREPARING')").fetchall():
                c.execute("UPDATE transactions SET state='ABORTED' WHERE tx=?",(row[0],)); event(c,row[0],'ABORTED')

    def read(self,tx):
        with db(self.path) as c:
            row=c.execute('SELECT * FROM transactions WHERE tx=?',(tx,)).fetchone()
            if not row: raise ValueError('Unknown transaction')
            result=dict(row); result['ack']=json.loads(result['ack']); result['payload']=json.loads(result['payload'])
            result['complete']=len(result['ack'])==len(self.participants)
            return result

    def decision(self,tx,state):
        with db(self.path) as c:
            c.execute('UPDATE transactions SET state=? WHERE tx=?',(state,tx)); event(c,tx,state)

    def deliver(self,tx):
        r=self.read(tx)
        if r['state'] not in ('COMMITTED','ABORTED'): return r
        for name,url in self.participants.items():
            if name in r['ack']: continue
            try:
                result=rpc(url,'commit' if r['state']=='COMMITTED' else 'abort',{'tx':tx})
                if result['state']!=r['state']: continue
            except RpcError: continue
            r['ack'].append(name)
            with db(self.path) as c: c.execute('UPDATE transactions SET ack=? WHERE tx=?',(json.dumps(r['ack']),tx))
            self.hook('after_ack_'+name)
        return self.read(tx)

    def recover(self):
        with self.mutex:
            with db(self.path) as c:
                ids=[r[0] for r in c.execute("SELECT tx FROM transactions WHERE state IN ('COMMITTED','ABORTED')")]
            for tx in ids:
                if not self.read(tx)['complete']: self.deliver(tx)

    def handle(self,action,d):
        if action=='snapshot':
            with db(self.path) as c: return {'transactions':[dict(r) for r in c.execute('SELECT * FROM transactions')],'events':[dict(r) for r in c.execute('SELECT * FROM events')]}
        if action=='begin':
            token=d.get('token'); payload={name:d.get(name,'DEFAULT') for name in self.participants}
            if not isinstance(token,str) or not token or len(token)>128: raise ValueError('token required, max 128 characters')
            if any(not isinstance(v,str) or not v or len(v)>128 for v in payload.values()): raise ValueError('Invalid resource')
            encoded=json.dumps(payload,sort_keys=True)
            with db(self.path) as c:
                old=c.execute('SELECT * FROM transactions WHERE token=?',(token,)).fetchone()
                if old:
                    if old['payload']!=encoded: raise ValueError('Idempotency token reused with different payload')
                    tx=old['tx']
                else:
                    tx=str(uuid.uuid4()); c.execute('INSERT INTO transactions VALUES (?,?,?,?,?)',(tx,token,encoded,'ACTIVE','[]')); event(c,tx,'ACTIVE')
            return self.read(tx)
        tx=d.get('tx')
        if action=='status': return self.read(tx)
        if action not in ('end','abort'): raise ValueError('Unknown coordinator action')
        with self.mutex:
            r=self.read(tx)
            if r['state'] in ('COMMITTED','ABORTED'): return self.deliver(tx)
            if action=='abort': self.decision(tx,'ABORTED'); return self.deliver(tx)
            self.decision(tx,'PREPARING'); votes=[]
            for name,url in self.participants.items():
                try: votes.append(rpc(url,'prepare',{'tx':tx,'resource':r['payload'][name]})['vote'])
                except RpcError: votes.append(False)
            self.hook('after_votes')
            self.decision(tx,'COMMITTED' if all(votes) else 'ABORTED')
            self.hook('after_decision')
            return self.deliver(tx)
