# Custo por chamada Python a partir de Mojo (Mojo é o processo host).
from std.python import Python, PythonObject
from std.time import perf_counter_ns

comptime N = 200_000

def bench(name: String, f: PythonObject, arg: PythonObject) raises:
    _ = f(arg)  # aquecimento
    var t0 = perf_counter_ns()
    for _ in range(N):
        _ = f(arg)
    var dt = perf_counter_ns() - t0
    print(name, Float64(dt) / N, "ns/call")

def main() raises:
    var t0 = perf_counter_ns()
    var np = Python.import_module("numpy")
    print("import numpy (inclui init do CPython):", Float64(perf_counter_ns() - t0) / 1e6, "ms")
    var mod = Python.evaluate("def noop(x): return x\ndef add1(x): return x + 1", file=True)
    bench("noop(x)      ", mod.noop, PythonObject(1))
    bench("add1(int)    ", mod.add1, PythonObject(1))
    bench("np.sum(10 el)", np.sum, np.arange(10))
    # Atributo + chamada + conversão de volta para Mojo
    var obj = np.arange(10)
    var t1 = perf_counter_ns()
    var acc = 0
    for _ in range(N):
        acc += Int(py=obj.sum())
    print("Int(py=obj.sum())", Float64(perf_counter_ns() - t1) / N, "ns/call", acc)
