"""Persistent exclusive resource locks and tentative reservations.
PREPARED survives restart. No participant timeout may unilaterally abort it.
"""
from common import db,event,EVENTS

class Participant:
    def __init__(self,path,capacity=5):
        self.path=path
        with db(path) as c:
            c.execute(EVENTS)
            c.execute('CREATE TABLE IF NOT EXISTS inventory(resource TEXT PRIMARY KEY, available INTEGER CHECK(available>=0))')
            c.execute('CREATE TABLE IF NOT EXISTS reservations(tx TEXT PRIMARY KEY, resource TEXT, state TEXT)')
            c.execute('CREATE TABLE IF NOT EXISTS locks(resource TEXT PRIMARY KEY, tx TEXT UNIQUE)')
            c.execute('INSERT OR IGNORE INTO inventory VALUES (?,?)',('DEFAULT',capacity))

    def handle(self,action,d):
        if action=='snapshot':
            with db(self.path) as c:
                return {t:[dict(r) for r in c.execute('SELECT * FROM '+t)] for t in ['inventory','reservations','locks','events']}
        tx=d.get('tx')
        if not isinstance(tx,str) or not tx or len(tx)>128: raise ValueError('tx is required')
        if action not in ('prepare','commit','abort','status'): raise ValueError('Unknown participant action')
        resource=d.get('resource','DEFAULT')
        with db(self.path) as c:
            old=c.execute('SELECT * FROM reservations WHERE tx=?',(tx,)).fetchone()
            if action=='status': return {'state':old['state'] if old else 'UNKNOWN'}
            if action=='prepare':
                if old:
                    if old['resource'] not in (None,resource): raise ValueError('tx reused with different resource')
                    return {'state':old['state'],'vote':old['state'] in ('PREPARED','COMMITTED')}
                item=c.execute('SELECT available FROM inventory WHERE resource=?',(resource,)).fetchone()
                locked=c.execute('SELECT 1 FROM locks WHERE resource=?',(resource,)).fetchone()
                state='PREPARED' if item and item[0]>0 and not locked else 'ABORTED'
                c.execute('INSERT INTO reservations VALUES (?,?,?)',(tx,resource,state))
                if state=='PREPARED': c.execute('INSERT INTO locks VALUES (?,?)',(resource,tx))
                event(c,tx,state)
                return {'state':state,'vote':state=='PREPARED'}
            target='COMMITTED' if action=='commit' else 'ABORTED'
            if old and old['state'] in ('COMMITTED','ABORTED'):
                if old['state']!=target: raise ValueError('Conflicting final decision')
                return {'state':target}
            if action=='commit':
                if not old or old['state']!='PREPARED': raise ValueError('Commit requires PREPARED')
                c.execute('UPDATE inventory SET available=available-1 WHERE resource=?',(old['resource'],))
                c.execute('UPDATE reservations SET state=? WHERE tx=?',(target,tx))
            elif old: c.execute('UPDATE reservations SET state=? WHERE tx=?',(target,tx))
            else:
                # Tombstone prevents a delayed PREPARE from resurrecting an aborted tx.
                c.execute('INSERT INTO reservations VALUES (?,NULL,?)',(tx,target))
            c.execute('DELETE FROM locks WHERE tx=?',(tx,))
            event(c,tx,target)
            return {'state':target}
