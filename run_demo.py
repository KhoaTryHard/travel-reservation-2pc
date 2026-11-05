"""Start all three independent processes. Ctrl+C stops; databases persist."""
import subprocess,sys,time
from pathlib import Path
root=Path(__file__).resolve().parent
processes=[]
try:
    for role,port,name in [('participant',8001,'airline'),('participant',8002,'hotel'),('coordinator',8000,'coordinator')]:
        processes.append(subprocess.Popen([sys.executable,'server.py',role,'--port',str(port),'--db',f'data/{name}.db','--enable-chaos'],cwd=root))
    print('Ready URLs: 8000 coordinator, 8001 airline, 8002 hotel. Ctrl+C to stop.',flush=True)
    while all(p.poll() is None for p in processes): time.sleep(.5)
finally:
    for p in processes:
        if p.poll() is None: p.terminate()
    for p in processes: p.wait()
