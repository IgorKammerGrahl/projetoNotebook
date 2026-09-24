# Zero-copy NumPy <-> Mojo.
from std.os import abort
from std.memory import Layout, Allocation, alloc, dealloc, unsafe_memcpy
from std.python import PythonObject
from std.python.bindings import PythonModuleBuilder

# Buffer cujo dono é o Mojo. Liberado em __deinit__ quando o PythonObject morre.
struct MojoBuf(Movable, Writable):
    var _alloc: Allocation[Float64]
    var n: Int

    def __init__(out self, n: Int):
        self._alloc = alloc(Layout[Float64](count=n))
        self.n = n

    def __deinit__(deinit self):
        dealloc(self._alloc^)

    @staticmethod
    def py_init(out self: MojoBuf, args: PythonObject, kwargs: PythonObject) raises:
        self = Self(Int(py=args[0]))

    @staticmethod
    def addr(self_ptr: Pointer[Self, MutAnyOrigin]) -> PythonObject:
        return PythonObject(Int(self_ptr[]._alloc.unsafe_ptr()))

    @staticmethod
    def size(self_ptr: Pointer[Self, MutAnyOrigin]) -> PythonObject:
        return PythonObject(self_ptr[].n)

    @staticmethod
    def fill_arange(self_ptr: Pointer[Self, MutAnyOrigin]) -> PythonObject:
        var p = self_ptr[]._alloc.unsafe_ptr()
        for i in range(self_ptr[].n):
            p.unsafe_offset(i).unsafe_write(Float64(i))
        return PythonObject(None)

def _f64_ptr(arr: PythonObject) raises -> Pointer[Float64, MutAnyOrigin]:
    # Caller garante: float64, C-contíguo (checado no lado Python).
    return arr.ctypes.data.unsafe_get_as_pointer[DType.float64]()

# NumPy -> Mojo, zero-copy: lê direto da memória do NumPy.
def sum_view(arr: PythonObject) raises -> PythonObject:
    var p = _f64_ptr(arr)
    var acc: Float64 = 0
    for i in range(Int(py=arr.size)):
        acc += p.unsafe_offset(i).unsafe_load()
    return PythonObject(acc)

# NumPy -> Mojo, zero-copy, escrita in-place (visível no Python).
def scale_inplace(arr: PythonObject, k: PythonObject) raises -> PythonObject:
    var p = _f64_ptr(arr)
    var kk = Float64(py=k)
    for i in range(Int(py=arr.size)):
        p.unsafe_offset(i).unsafe_store(p.unsafe_offset(i).unsafe_load() * kk)
    return PythonObject(None)

# Só o custo de obter o ponteiro (O(1)).
def touch(arr: PythonObject) raises -> PythonObject:
    return PythonObject(_f64_ptr(arr).unsafe_load())

# NumPy -> Mojo com cópia (memcpy para buffer próprio do Mojo).
def copy_in(arr: PythonObject) raises -> PythonObject:
    var n = Int(py=arr.size)
    var buf = MojoBuf(n)
    unsafe_memcpy(dest=buf._alloc.unsafe_ptr(), src=_f64_ptr(arr), count=n)
    return PythonObject(alloc=buf^)

# Caminho ingênuo: elemento a elemento via PythonObject (sem buffer protocol).
def sum_naive(arr: PythonObject) raises -> PythonObject:
    var acc: Float64 = 0
    for i in range(Int(py=arr.size)):
        acc += Float64(py=arr[i])
    return PythonObject(acc)

@export
def PyInit_zc() abi("C") -> PythonObject:
    try:
        var m = PythonModuleBuilder("zc")
        _ = (
            m.add_type[MojoBuf]("MojoBuf")
            .def_py_init[MojoBuf.py_init]()
            .def_method[MojoBuf.addr]("addr")
            .def_method[MojoBuf.size]("size")
            .def_method[MojoBuf.fill_arange]("fill_arange")
        )
        m.def_function[sum_view]("sum_view")
        m.def_function[scale_inplace]("scale_inplace")
        m.def_function[touch]("touch")
        m.def_function[copy_in]("copy_in")
        m.def_function[sum_naive]("sum_naive")
        return m.finalize()
    except e:
        abort(String("failed to create module: ", e))
