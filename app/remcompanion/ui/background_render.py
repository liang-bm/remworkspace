"""One latest-only Pillow worker. Never calls Tk from a worker thread."""
import queue
import threading
import time
from ..chat_background import render_faded_cover

class BackgroundRenderQueue:
    def __init__(self):
        self.requests=queue.Queue(maxsize=1)
        self.results=queue.Queue(maxsize=2)
        self.stopped=threading.Event()
        self.thread=threading.Thread(target=self._run,name='remchat-background-pixels',daemon=True)
        self.thread.start()

    @staticmethod
    def _latest(target,item):
        try:target.put_nowait(item)
        except queue.Full:
            try:target.get_nowait()
            except queue.Empty:pass
            try:target.put_nowait(item)
            except queue.Full:pass

    def submit(self,generation,key,path,size,color,fade):
        self._latest(self.requests,(generation,key,path,size,color,fade))

    def _run(self):
        while not self.stopped.is_set():
            try:request=self.requests.get(timeout=.2)
            except queue.Empty:continue
            generation,key,path,size,color,fade=request
            start=time.perf_counter()
            try:image=render_faded_cover(path,size,base_color=color,fade_percent=fade);error=None
            except Exception as exc:
                # Includes Pillow's decompression-bomb and unsupported decoder
                # exceptions. A failure must resolve, not kill the worker while
                # the UI waits forever; process interrupts are not swallowed.
                image=None;error=str(exc)
            if not self.stopped.is_set():
                self._latest(self.results,(generation,key,image,error,(time.perf_counter()-start)*1000))

    def close(self):
        self.stopped.set()  # No Tk-thread join; an already decoding file finishes off-thread.
