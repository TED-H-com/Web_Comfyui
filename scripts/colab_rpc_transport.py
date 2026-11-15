"""Scoped adapter for the pinned Jupyter WebSocket client, in bridge subprocess only."""
from contextlib import contextmanager
from pathlib import Path
import math,signal,time,traceback

class NotReady(ConnectionError):pass

class Budget:
    def __init__(self,seconds,wall=time.time,mono=time.monotonic):
        self.wall,self.mono=wall,mono;self.w=wall();self.m=mono();self.seconds=seconds;self.last=self.w
        self.phase='connect';self.sent=False;self.close_code=None;self.attempt=0;self.frames=[];self.events=[]
    def remaining(self):
        w=self.wall();m=self.mono()
        if not all(math.isfinite(x) for x in (w,m)) or w<self.last or m<self.m:raise TimeoutError()
        self.last=w;r=min(self.seconds-(w-self.w),self.seconds-(m-self.m))
        if r<=0:raise TimeoutError()
        return r
    def mark(self,phase):
        assert phase in {'connect','open','send','ack','close'}
        self.phase=phase
        self.events.append({'phase':phase,'wall_ms':max(0,min(300000,int((self.wall()-self.w)*1000))),'monotonic_ms':max(0,min(300000,int((self.mono()-self.m)*1000)))})
    def evidence(self,error=None):
        if error:
            self.frames=[f'{Path(f.filename).name}:{f.lineno}' for f in traceback.extract_tb(error.__traceback__)[-8:]
                if Path(f.filename).name in {'wsclient.py','client.py','runtime.py','colab_rpc_transport.py','colab_bridge.py'}]
        return {'phase':self.phase,'sent':self.sent,'attempts':self.attempt,
                'wall_ms':max(0,min(300000,int((self.wall()-self.w)*1000))),
                'monotonic_ms':max(0,min(300000,int((self.mono()-self.m)*1000))),
                'close_code':self.close_code,'frames':self.frames,'events':self.events[-12:]}

class BoundedEvent:
    def __init__(self,event,budget):self.event,self.budget=event,budget
    def wait(self,timeout=None):
        remaining=self.budget.remaining()
        result=self.event.wait(min(remaining,timeout) if timeout is not None else remaining)
        self.budget.remaining()
        return result
    def __getattr__(self,name):return getattr(self.event,name)

@contextmanager
def scoped(budget,client_class=None):
    if client_class is None:
        from jupyter_kernel_client.wsclient import KernelWebSocketClient
        client_class=KernelWebSocketClient
    originals={name:getattr(client_class,name) for name in ('start_channels','execute','_on_open','_on_close')}
    def start(client,*args,**kwargs):
        budget.mark('connect');client.timeout=min(10,budget.remaining())
        client.connection_ready=BoundedEvent(client.connection_ready,budget)
        client._message_received=BoundedEvent(client._message_received,budget)
        originals['start_channels'](client,*args,**kwargs)
        budget.remaining()
        if not client.connection_ready.is_set():raise NotReady()
    def execute(client,*args,**kwargs):
        budget.remaining()
        if not client.connection_ready.is_set():raise NotReady()
        # Set before entering the send boundary: exceptions after this point
        # are ambiguous even if the underlying socket wrote zero bytes.
        budget.mark('send');budget.sent=True
        return originals['execute'](client,*args,**kwargs)
    def opened(client,*args,**kwargs):
        budget.mark('open');return originals['_on_open'](client,*args,**kwargs)
    def closed(client,socket,code,reason):
        budget.mark('close')
        if type(code) is int and 0<=code<=65535:budget.close_code=code
        return originals['_on_close'](client,socket,code,reason)
    for name,value in [('start_channels',start),('execute',execute),('_on_open',opened),('_on_close',closed)]:setattr(client_class,name,value)
    # The SDK's silent IOPub loop can spin at timeout=0. Interrupt it even when
    # no Event wait blocks. Never lengthen the parent bridge alarm.
    old_handler=None;old_timer=None;fired=False
    try:
        old_handler=signal.getsignal(signal.SIGALRM);old_timer=signal.getitimer(signal.ITIMER_REAL)
        def expired(*_):
            nonlocal fired
            fired=True
            raise TimeoutError()
        signal.signal(signal.SIGALRM,expired)
        signal.setitimer(signal.ITIMER_REAL,min(budget.remaining(),old_timer[0]) if old_timer[0]>0 else budget.remaining())
        try:
            yield
        except Exception as error:
            if fired and not isinstance(error, TimeoutError):
                raise TimeoutError() from error
            raise
    finally:
        for name,value in originals.items():setattr(client_class,name,value)
        if old_timer is not None:
            signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,old_handler)
            if old_timer[0]>0:signal.setitimer(signal.ITIMER_REAL,max(.001,old_timer[0]-(time.monotonic()-budget.m)),old_timer[1])
