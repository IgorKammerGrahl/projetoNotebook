# Célula C-ABI (sem PythonModuleBuilder) que usa Python e pede memória ao host.
# ABI C não propaga exceção: erros viram códigos de retorno negativos.
from std.ffi import external_call
from std.python import Python, PythonObject

# Callback do host: recebe nbytes, devolve endereço de um buffer que o Python
# possui (0 = falha).
comptime AllocFn = def(Int) thin abi("C") -> Int

def _gil() -> Int32:
    return external_call["PyGILState_Check", Int32]()

@export
def gil_state() abi("C") -> Int32:
    return _gil()

# GIL depois de usar Python: o runtime Mojo o adquire por conta própria?
@export
def gil_after_python() abi("C") -> Int32:
    try:
        _ = Python.import_module("sys")
    except:
        return -1
    return _gil()

# Lê um marcador que o host pôs em sys: prova que é o mesmo interpretador.
@export
def probe_marker() abi("C") -> Int:
    try:
        var sys = Python.import_module("sys")
        return Int(py=Python.import_module("builtins").getattr(sys, "_host_marker", -1))
    except:
        return -2

@export
def linalg_norm(n: Int) abi("C") -> Float64:
    try:
        var np = Python.import_module("numpy")
        return Float64(py=np.linalg.norm(np.arange(n, dtype=np.float64)))
    except:
        return -1.0

# Muta uma lista que uma thread Python do host também muta.
@export
def churn_shared(iters: Int) abi("C") -> Int:
    try:
        var shared = Python.import_module("sys")._shared
        for i in range(iters):
            shared.append(PythonObject(i))
            _ = Python.dict(k=i)
        return iters
    except:
        return -1

# Saída de tamanho só conhecido depois de calcular (sequência de Collatz).
@export
def collatz(start: Int, alloc_fn: AllocFn) abi("C") -> Int:
    var n = start
    var steps = 1
    while n != 1:
        if n % 2 == 0:
            n = n // 2
        else:
            n = 3 * n + 1
        steps += 1
    var addr = alloc_fn(steps * 8)
    if addr == 0:
        return -1
    var p = Pointer[Int64, MutAnyOrigin](unsafe_from_address=addr)
    n = start
    for i in range(steps):
        p.unsafe_offset(i).unsafe_store(Int64(n))
        if n % 2 == 0:
            n = n // 2
        else:
            n = 3 * n + 1
    return steps

# k pedidos de alocação pequenos: custo por callback.
@export
def many_allocs(k: Int, alloc_fn: AllocFn) abi("C") -> Int:
    for i in range(k):
        var addr = alloc_fn(8)
        if addr == 0:
            return -1
        Pointer[Int64, MutAnyOrigin](unsafe_from_address=addr).unsafe_store(Int64(i))
    return k

# Baseline e caso grande: preenche n float64, pré-alocado vs via callback.
@export
def fill_prealloc(addr: Int, n: Int) abi("C") -> Int:
    var p = Pointer[Float64, MutAnyOrigin](unsafe_from_address=addr)
    for i in range(n):
        p.unsafe_offset(i).unsafe_store(Float64(i))
    return n

@export
def fill_alloc(n: Int, alloc_fn: AllocFn) abi("C") -> Int:
    var addr = alloc_fn(n * 8)
    if addr == 0:
        return -1
    return fill_prealloc(addr, n)
