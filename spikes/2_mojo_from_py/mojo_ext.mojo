# Módulo de extensão Python escrito em Mojo.
from std.os import abort
from std.python import PythonObject
from std.python.bindings import PythonModuleBuilder

@export
def PyInit_mojo_ext() abi("C") -> PythonObject:
    try:
        var m = PythonModuleBuilder("mojo_ext")
        m.def_function[noop]("noop")
        m.def_function[add]("add")
        m.def_function[sum_to]("sum_to")
        return m.finalize()
    except e:
        abort(String("failed to create module: ", e))

def noop(x: PythonObject) raises -> PythonObject:
    return x

def add(a: PythonObject, b: PythonObject) raises -> PythonObject:
    return PythonObject(Int(py=a) + Int(py=b))

# Laço nativo: mostra que o trabalho acontece em Mojo, não em Python.
def sum_to(n: PythonObject) raises -> PythonObject:
    var acc = 0
    for i in range(Int(py=n)):
        acc = (acc + i * i) % 1_000_003
    return PythonObject(acc)
