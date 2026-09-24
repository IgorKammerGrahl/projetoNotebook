# Mesma célula, exportada por ABI C pura (sem PythonObject/PythonModuleBuilder).
# Carregada via ctypes; dados entram como ponteiro + tamanho (zero-copy do NumPy).
def mandel(cx: Float64, cy: Float64, max_iter: Int) -> Int:
    var x: Float64 = 0
    var y: Float64 = 0
    for i in range(max_iter):
        if x * x + y * y > 4.0:
            return i
        var t = x * x - y * y + cx
        y = 2.0 * x * y + cy
        x = t
    return max_iter

@export
def count(n: Int) abi("C") -> Int:
    var total = 0
    for i in range(n):
        total += mandel(Float64(i) / Float64(n) * 3.0 - 2.0, 0.1, 200)
    return total

@export
def sum_f64(addr: Int, n: Int) abi("C") -> Float64:
    var p = Pointer[Float64, MutAnyOrigin](unsafe_from_address=addr)
    var acc: Float64 = 0
    for i in range(n):
        acc += p.unsafe_offset(i).unsafe_load()
    return acc
