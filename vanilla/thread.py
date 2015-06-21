import collections
import threading
import os

from Queue import Queue

from vanilla.exception import Closed


class Oneshot(object):
    def __init__(self, hub, f, a):
        pipe_r, self.pipe_w = os.pipe()
        self.recver = hub.io.fd_in(pipe_r).map(self.done)
        self.t = threading.Thread(target=self.run, args=(f, a))
        self.t.start()

    def done(self, x):
        return self.result

    def run(self, f, a):
        self.result = f(*a)
        os.write(self.pipe_w, chr(1))


class Wrap(object):
    def __init__(self, pool, target):
        self.pool = pool
        self.target = target

    def __call__(self, *a, **kw):
        return self.pool.call(self.target, *a, **kw)

    def __getattr__(self, name):
        return Wrap(self.pool, getattr(self.target, name))


class Pool(object):
    def __init__(self, hub, size):
        self.hub = hub
        self.size = size

        pipe_r, self.pipe_w = os.pipe()
        self.pipe_r = hub.io.fd_in(pipe_r)

        self.requests = Queue()
        self.results = collections.deque()
        self.closed = False
        self.threads = 0

        for i in xrange(size):
            t = threading.Thread(target=self.runner)
            t.daemon = True
            t.start()
            self.threads += 1

        hub.spawn(self.responder)

    def wrap(self, target):
        return Wrap(self, target)

    def runner(self):
        while True:
            item = self.requests.get()
            if type(item) == Closed:
                self.threads -= 1
                if self.threads <= 0:
                    # entire pool has stopped, send signal
                    os.write(self.pipe_w, chr(0))
                return
            sender, f, a, kw = item
            result = f(*a, **kw)
            self.requests.task_done()
            self.results.append((sender, result))
            # send signal to wake up main thread
            os.write(self.pipe_w, chr(1))

    def responder(self):
        for s in self.pipe_r:
            for ch in s:
                ch = ord(ch)
                if not ch:
                    break
                sender, result = self.results.popleft()
                sender.send(result)
        self.pipe_r.close()

    def call(self, f, *a, **kw):
        if self.closed:
            raise Closed
        sender, recver = self.hub.pipe()
        self.requests.put((sender, f, a, kw))
        return recver

    def close(self):
        self.closed = True
        for i in xrange(self.size):
            # tell thread pool to stop when they have finished the last request
            self.requests.put(Closed())


class __plugin__(object):
    def __init__(self, hub):
        self.hub = hub

    def call(self, f, *a):
        return Oneshot(self.hub, f, a).recver

    def pool(self, size):
        return Pool(self.hub, size)
