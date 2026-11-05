import sys,unittest,tempfile,subprocess,socket,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import rpc,RpcError
ROOT=Path(__file__).resolve().parents[1]

class SystemTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.procs={}; self.urls={}; self.ports={}
        for name in ('airline','hotel','coordinator'):
            with socket.socket() as s: s.bind(('127.0.0.1',0)); self.ports[name]=s.getsockname()[1]
            self.urls[name]='http://127.0.0.1:'+str(self.ports[name])
        for name in self.urls: self.start(name)
    def start(self,name):
        args=[sys.executable,str(ROOT/'server.py'),'coordinator' if name=='coordinator' else 'participant','--port',str(self.ports[name]),'--db',str(Path(self.tmp.name)/(name+'.db')),'--enable-chaos']
        if name=='coordinator': args+=['--airline',self.urls['airline'],'--hotel',self.urls['hotel']]
        self.procs[name]=subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(100):
            try: rpc(self.urls[name],'health',timeout=.2); return
            except RpcError: time.sleep(.03)
        self.fail('Service failed to start '+name)
    def stop(self,name):
        p=self.procs[name]
        if p.poll() is None: p.terminate()
        p.wait(timeout=5)
    def tearDown(self):
        for name in self.procs: self.stop(name)
        self.tmp.cleanup()
    def call(self,action,data=None,name='coordinator'): return rpc(self.urls[name],action,data,timeout=15)
    def begin(self,token='t',**kw): return self.call('begin',dict(token=token,**kw))['tx']
    def end(self,tx): return self.call('end',{'tx':tx})
    def fault(self,name,point,mode='crash',seconds=3): self.call('fault',dict(point=point,mode=mode,seconds=seconds),name)
    def settled(self,tx,state):
        for _ in range(100):
            r=self.call('status',{'tx':tx})
            if r['complete']:
                self.assertEqual(r['state'],state)
                for name in ('airline','hotel'): self.assertEqual(self.call('status',{'tx':tx},name)['state'],state)
                return
            time.sleep(.1)
        self.fail('Recovery did not complete')
    def capacity(self,name): return self.call('snapshot',name=name)['inventory'][0]['available']
    def test_success_and_duplicate_end(self):
        tx=self.begin(); self.end(tx); self.end(tx); self.settled(tx,'COMMITTED')
        self.assertEqual(self.capacity('airline'),4); self.assertEqual(self.capacity('hotel'),4)
    def test_invalid_hotel_aborts_both(self):
        tx=self.begin(hotel='MISSING'); self.end(tx); self.settled(tx,'ABORTED'); self.assertEqual(self.capacity('airline'),5)
    def test_token_conflict(self):
        tx=self.begin(); self.assertEqual(self.begin(),tx)
        with self.assertRaises(RpcError): self.begin(hotel='OTHER')
    def test_concurrent_same_token(self):
        with ThreadPoolExecutor(8) as pool: ids=list(pool.map(lambda _:self.begin(),range(16)))
        self.assertEqual(len(set(ids)),1)
        with ThreadPoolExecutor(8) as pool: list(pool.map(self.end,ids))
        self.assertEqual(self.capacity('hotel'),4)
    def test_concurrent_bookings_no_overselling(self):
        def book(i): return self.end(self.begin(str(i)))
        with ThreadPoolExecutor(8) as pool: out=list(pool.map(book,range(12)))
        self.assertEqual(sum(r['state']=='COMMITTED' for r in out),5)
        self.assertEqual(self.capacity('airline'),0); self.assertEqual(self.capacity('hotel'),0)
    def test_explicit_abort(self):
        tx=self.begin(); self.call('abort',{'tx':tx}); self.settled(tx,'ABORTED')
        self.assertEqual(self.end(tx)['state'],'ABORTED')
    def test_participant_crash_after_prepare(self):
        tx=self.begin(); self.fault('hotel','after_prepare'); self.end(tx)
        self.stop('hotel'); self.start('hotel'); self.settled(tx,'ABORTED'); self.assertEqual(self.capacity('airline'),5)
    def test_coordinator_crash_before_decision_blocks_then_aborts(self):
        tx=self.begin(); self.fault('coordinator','after_votes')
        with self.assertRaises(RpcError): self.end(tx)
        self.stop('coordinator'); time.sleep(.6)
        for name in ('airline','hotel'):
            self.assertEqual(self.call('status',{'tx':tx},name)['state'],'PREPARED')
            self.stop(name); self.start(name)
            self.assertEqual(self.call('status',{'tx':tx},name)['state'],'PREPARED')
        self.start('coordinator'); self.settled(tx,'ABORTED')
    def test_coordinator_crash_after_decision_replays_commit(self):
        tx=self.begin(); self.fault('coordinator','after_decision')
        with self.assertRaises(RpcError): self.end(tx)
        self.stop('coordinator'); self.start('coordinator'); self.settled(tx,'COMMITTED')
        self.assertEqual(self.capacity('hotel'),4)
    def test_coordinator_crash_after_one_ack(self):
        tx=self.begin(); self.fault('coordinator','after_ack_airline')
        with self.assertRaises(RpcError): self.end(tx)
        self.stop('coordinator'); self.start('coordinator'); self.settled(tx,'COMMITTED')
        self.assertEqual(self.capacity('airline'),4)
    def test_lost_commit_ack_replay(self):
        tx=self.begin(); self.fault('hotel','after_commit'); self.end(tx)
        self.stop('hotel'); self.start('hotel'); self.settled(tx,'COMMITTED'); self.assertEqual(self.capacity('hotel'),4)
    def test_timeout_and_late_prepare_tombstone(self):
        tx=self.begin(); self.fault('hotel','before_prepare','delay',3)
        self.end(tx); time.sleep(1.3); self.settled(tx,'ABORTED'); self.assertEqual(self.capacity('hotel'),5)
        self.assertEqual(self.call('snapshot',name='hotel')['locks'],[])
    def test_persistent_lock_conflict(self):
        self.assertTrue(self.call('prepare',{'tx':'x'},'airline')['vote'])
        self.stop('airline'); self.start('airline')
        self.assertFalse(self.call('prepare',{'tx':'y'},'airline')['vote'])
        self.call('abort',{'tx':'x'},'airline')
        self.assertTrue(self.call('prepare',{'tx':'z'},'airline')['vote'])
    def test_reject_opposite_decision(self):
        tx=self.begin(); self.end(tx)
        with self.assertRaises(RpcError): self.call('abort',{'tx':tx},'hotel')
        self.assertEqual(self.call('abort',{'tx':tx})['state'],'COMMITTED')

if __name__=='__main__': unittest.main(verbosity=2)
