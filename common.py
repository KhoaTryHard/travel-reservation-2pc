"""JSON RPC and durable SQLite helpers; Python standard library only."""
import json, sqlite3, urllib.request
from contextlib import contextmanager
from pathlib import Path

class RpcError(Exception): pass

def rpc(url, action, data=None, timeout=2):
    req=urllib.request.Request(url+'/'+action, json.dumps(data or {}).encode(), {'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            result=json.load(response)
    except Exception as exc: raise RpcError(str(exc)) from exc
    if 'error' in result: raise RpcError(result['error'])
    return result

@contextmanager
def db(path):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(path,timeout=10)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA synchronous=FULL')
    try:
        c.execute('BEGIN IMMEDIATE')
        yield c
        c.commit()
    except BaseException:
        c.rollback(); raise
    finally: c.close()

def event(c,tx,state):
    c.execute('INSERT INTO events(tx,state) VALUES (?,?)',(tx,state))

EVENTS='CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, tx TEXT, state TEXT, at TEXT DEFAULT CURRENT_TIMESTAMP)'
