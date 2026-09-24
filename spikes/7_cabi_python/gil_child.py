"""Processo filho: chama churn_shared (célula toca objetos Python) via CDLL ou
PyDLL, opcionalmente com uma thread Python mutando a mesma lista.
Saída: uma linha JSON. Crash/deadlock é detectado pelo pai."""
import ctypes, faulthandler, json, os, sys, threading
faulthandler.enable()
mode, contend, iters, reps = sys.argv[1], sys.argv[2] == "1", int(sys.argv[3]), int(sys.argv[4])
Lib = {"cdll": ctypes.CDLL, "pydll": ctypes.PyDLL}[mode]
lib = Lib(os.path.join(os.path.dirname(__file__), "build", "cell7.so"))
lib.churn_shared.argtypes = [ctypes.c_ssize_t]; lib.churn_shared.restype = ctypes.c_ssize_t
sys._shared = []
stop = threading.Event(); bg = [0]
def worker():
    while not stop.is_set():
        sys._shared.append(-1); bg[0] += 1
t = threading.Thread(target=worker)
if contend: t.start()
bad = 0
for _ in range(reps):
    if lib.churn_shared(iters) != iters: bad += 1
stop.set()
if contend: t.join()
expected = iters * reps + bg[0]
print(json.dumps({"len": len(sys._shared), "expected": expected, "lost": expected - len(sys._shared),
                  "bad_returns": bad, "bg_appends": bg[0]}))
