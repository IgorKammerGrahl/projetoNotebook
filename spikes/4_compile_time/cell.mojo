# "Célula" de ~20 linhas no formato que a Fase 2 geraria: função + export.
from std.os import abort
from std.python import PythonObject
from std.python.bindings import PythonModuleBuilder

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

def count(n: PythonObject) raises -> PythonObject:
    var total = 0
    var k = Int(py=n)
    for i in range(k):
        total += mandel(Float64(i) / Float64(k) * 3.0 - 2.0, 0.1, 200)
    return PythonObject(total)  # EDIT-MARKER

@export
def PyInit_cell() abi("C") -> PythonObject:
    try:
        var m = PythonModuleBuilder("cell")
        m.def_function[count]("count")
        return m.finalize()
    except e:
        abort(String("failed: ", e))
