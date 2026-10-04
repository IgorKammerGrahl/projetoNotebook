# Prelude appended after every Mojo cell (D-009). Must not import std.python (D-002).
# Arrays are row-major and C-contiguous; rank 1 or 2 (D-024). A 1-D array has _d1 == 1.
# alloc_fn(index, d0, d1) -> address: index >= 0 is an output, -1 the error message buffer.
comptime __NbAllocFn = def(Int, Int, Int) thin abi("C") -> Int
comptime __NB_MAX_ELEMENTS = 1 << 40


def __nb_oob(i: Int, d0: Int) -> Error:
    return Error(String("index ", i, " out of bounds for length ", d0))


def __nb_oob(i: Int, j: Int, d0: Int, d1: Int) -> Error:
    return Error(String("index (", i, ", ", j, ") out of bounds for shape (", d0, ", ", d1, ")"))


def __nb_dim(d: Int, rank: Int, d0: Int, d1: Int) raises -> Int:
    if d < 0 or d >= rank:
        raise Error(String("dim(", d, ") on a ", rank, "-D array"))
    return d0 if d == 0 else d1


struct ArrayIn[dtype: DType, rank: Int = 1](Copyable, Movable, Sized):
    var _addr: Int
    var _d0: Int
    var _d1: Int

    def __init__(out self, addr: Int, d0: Int, d1: Int):
        self._addr = addr
        self._d0 = d0
        self._d1 = d1

    def __len__(self) -> Int:
        return self._d0

    def dim(self, d: Int) raises -> Int:
        return __nb_dim(d, Self.rank, self._d0, self._d1)

    def unsafe_ptr(self) -> Pointer[Scalar[Self.dtype], MutAnyOrigin]:
        return Pointer[Scalar[Self.dtype], MutAnyOrigin](unsafe_from_address=self._addr)

    def __getitem__(self, i: Int) raises -> Scalar[Self.dtype]:
        comptime assert Self.rank == 1, "x[i] needs a 1-D array: use x[i, j] on a 2-D array"
        if i < 0 or i >= self._d0:
            raise __nb_oob(i, self._d0)
        return self.unsafe_ptr().unsafe_offset(i).unsafe_load()

    def __getitem__(self, i: Int, j: Int) raises -> Scalar[Self.dtype]:
        comptime assert Self.rank == 2, "x[i, j] needs a 2-D array: use x[i] on a 1-D array"
        if i < 0 or i >= self._d0 or j < 0 or j >= self._d1:
            raise __nb_oob(i, j, self._d0, self._d1)
        return self.unsafe_ptr().unsafe_offset(i * self._d1 + j).unsafe_load()

    # No bounds check (Mojo 1.x `unsafe_` convention). Out of range = undefined behavior.
    def unsafe_get(self, i: Int) -> Scalar[Self.dtype]:
        comptime assert Self.rank == 1, "x.unsafe_get(i) needs a 1-D array"
        return self.unsafe_ptr().unsafe_offset(i).unsafe_load()

    def unsafe_get(self, i: Int, j: Int) -> Scalar[Self.dtype]:
        comptime assert Self.rank == 2, "x.unsafe_get(i, j) needs a 2-D array"
        return self.unsafe_ptr().unsafe_offset(i * self._d1 + j).unsafe_load()


struct ArrayOut[dtype: DType, rank: Int = 1](Movable, Sized):
    var _index: Int
    var _alloc_fn: __NbAllocFn
    var _addr: Int
    var _d0: Int  # -1 until alloc()
    var _d1: Int

    def __init__(out self, index: Int, alloc_fn: __NbAllocFn):
        self._index = index
        self._alloc_fn = alloc_fn
        self._addr = 0
        self._d0 = -1
        self._d1 = 1

    def alloc(mut self, n: Int) raises:
        comptime assert Self.rank == 1, "alloc(n) needs a 1-D output: use alloc(rows, cols) on a 2-D output"
        self._alloc(n, 1)

    def alloc(mut self, rows: Int, cols: Int) raises:
        comptime assert Self.rank == 2, "alloc(rows, cols) needs a 2-D output: use alloc(n) on a 1-D output"
        self._alloc(rows, cols)

    def _alloc(mut self, d0: Int, d1: Int) raises:
        if self._d0 >= 0:
            raise Error("output already allocated")
        if d0 < 0 or d1 < 0:
            raise Error(String("cannot allocate shape with a negative dimension (", d0, ", ", d1, ")"))
        # the host checks the shape against the declared rank and allocates exactly d0 * d1
        var addr = self._alloc_fn(self._index, d0, d1)
        if addr == 0:
            raise Error(String("host could not allocate shape (", d0, ", ", d1, ")"))
        self._addr = addr
        self._d0 = d0
        self._d1 = d1

    def __len__(self) -> Int:
        return max(self._d0, 0)

    def dim(self, d: Int) raises -> Int:
        if self._d0 < 0:
            raise Error("output used before alloc()")
        return __nb_dim(d, Self.rank, self._d0, self._d1)

    def unsafe_ptr(self) raises -> Pointer[Scalar[Self.dtype], MutAnyOrigin]:
        if self._d0 < 0:
            raise Error("output used before alloc()")
        return Pointer[Scalar[Self.dtype], MutAnyOrigin](unsafe_from_address=self._addr)

    def _at(self, k: Int) -> Pointer[Scalar[Self.dtype], MutAnyOrigin]:
        return Pointer[Scalar[Self.dtype], MutAnyOrigin](unsafe_from_address=self._addr).unsafe_offset(k)

    def _check(self, i: Int) raises:
        if self._d0 < 0:
            raise Error("output used before alloc()")
        if i < 0 or i >= self._d0:
            raise __nb_oob(i, self._d0)

    def _check(self, i: Int, j: Int) raises:
        if self._d0 < 0:
            raise Error("output used before alloc()")
        if i < 0 or i >= self._d0 or j < 0 or j >= self._d1:
            raise __nb_oob(i, j, self._d0, self._d1)

    def __getitem__(self, i: Int) raises -> Scalar[Self.dtype]:
        comptime assert Self.rank == 1, "x[i] needs a 1-D array: use x[i, j] on a 2-D array"
        self._check(i)
        return self._at(i).unsafe_load()

    def __getitem__(self, i: Int, j: Int) raises -> Scalar[Self.dtype]:
        comptime assert Self.rank == 2, "x[i, j] needs a 2-D array: use x[i] on a 1-D array"
        self._check(i, j)
        return self._at(i * self._d1 + j).unsafe_load()

    def __setitem__(mut self, i: Int, value: Scalar[Self.dtype]) raises:
        comptime assert Self.rank == 1, "x[i] needs a 1-D array: use x[i, j] on a 2-D array"
        self._check(i)
        self._at(i).unsafe_store(value)

    def __setitem__(mut self, i: Int, j: Int, value: Scalar[Self.dtype]) raises:
        comptime assert Self.rank == 2, "x[i, j] needs a 2-D array: use x[i] on a 1-D array"
        self._check(i, j)
        self._at(i * self._d1 + j).unsafe_store(value)

    # No bounds or alloc() check (Mojo 1.x `unsafe_` convention). Misuse = undefined behavior.
    def unsafe_get(self, i: Int) -> Scalar[Self.dtype]:
        comptime assert Self.rank == 1, "x.unsafe_get(i) needs a 1-D array"
        return self._at(i).unsafe_load()

    def unsafe_get(self, i: Int, j: Int) -> Scalar[Self.dtype]:
        comptime assert Self.rank == 2, "x.unsafe_get(i, j) needs a 2-D array"
        return self._at(i * self._d1 + j).unsafe_load()

    def unsafe_set(mut self, i: Int, value: Scalar[Self.dtype]):
        comptime assert Self.rank == 1, "x.unsafe_set(i, v) needs a 1-D array"
        self._at(i).unsafe_store(value)

    def unsafe_set(mut self, i: Int, j: Int, value: Scalar[Self.dtype]):
        comptime assert Self.rank == 2, "x.unsafe_set(i, j, v) needs a 2-D array"
        self._at(i * self._d1 + j).unsafe_store(value)


# Working buffer local to the cell: zero-filled, bounds-checked, freed when the cell returns.
# Backed by List (owned by the struct), so no pointer can outlive it.
struct Scratch[dtype: DType, rank: Int = 1](Movable, Sized):
    var _data: List[Scalar[Self.dtype]]
    var _d0: Int
    var _d1: Int

    def __init__(out self, n: Int) raises:
        comptime assert Self.rank == 1, "Scratch(n) is 1-D: use Scratch[..., 2](rows, cols)"
        self._data = List[Scalar[Self.dtype]](length=Self._size(n, 1), fill=0)
        self._d0 = n
        self._d1 = 1

    def __init__(out self, rows: Int, cols: Int) raises:
        comptime assert Self.rank == 2, "Scratch(rows, cols) is 2-D: declare Scratch[DType..., 2]"
        self._data = List[Scalar[Self.dtype]](length=Self._size(rows, cols), fill=0)
        self._d0 = rows
        self._d1 = cols

    @staticmethod
    def _size(d0: Int, d1: Int) raises -> Int:
        # d0 * d1 must not overflow: a wrapped size would let checked indices pass on a small buffer
        if d0 < 0 or d1 < 0:
            raise Error(String("cannot allocate shape with a negative dimension (", d0, ", ", d1, ")"))
        if d1 != 0 and d0 > __NB_MAX_ELEMENTS // d1:
            raise Error(String("Scratch shape (", d0, ", ", d1, ") is too large"))
        return d0 * d1

    def __len__(self) -> Int:
        return self._d0

    def dim(self, d: Int) raises -> Int:
        return __nb_dim(d, Self.rank, self._d0, self._d1)

    def __getitem__(self, i: Int) raises -> Scalar[Self.dtype]:
        comptime assert Self.rank == 1, "x[i] needs a 1-D array: use x[i, j] on a 2-D array"
        if i < 0 or i >= self._d0:
            raise __nb_oob(i, self._d0)
        return self._data.unsafe_get(i)

    def __getitem__(self, i: Int, j: Int) raises -> Scalar[Self.dtype]:
        comptime assert Self.rank == 2, "x[i, j] needs a 2-D array: use x[i] on a 1-D array"
        if i < 0 or i >= self._d0 or j < 0 or j >= self._d1:
            raise __nb_oob(i, j, self._d0, self._d1)
        return self._data.unsafe_get(i * self._d1 + j)

    def __setitem__(mut self, i: Int, value: Scalar[Self.dtype]) raises:
        comptime assert Self.rank == 1, "x[i] needs a 1-D array: use x[i, j] on a 2-D array"
        if i < 0 or i >= self._d0:
            raise __nb_oob(i, self._d0)
        self._data.unsafe_set(i, value)

    def __setitem__(mut self, i: Int, j: Int, value: Scalar[Self.dtype]) raises:
        comptime assert Self.rank == 2, "x[i, j] needs a 2-D array: use x[i] on a 1-D array"
        if i < 0 or i >= self._d0 or j < 0 or j >= self._d1:
            raise __nb_oob(i, j, self._d0, self._d1)
        self._data.unsafe_set(i * self._d1 + j, value)

    # No bounds check (Mojo 1.x `unsafe_` convention). Out of range = undefined behavior.
    def unsafe_get(self, i: Int) -> Scalar[Self.dtype]:
        comptime assert Self.rank == 1, "x.unsafe_get(i) needs a 1-D array"
        return self._data.unsafe_get(i)

    def unsafe_get(self, i: Int, j: Int) -> Scalar[Self.dtype]:
        comptime assert Self.rank == 2, "x.unsafe_get(i, j) needs a 2-D array"
        return self._data.unsafe_get(i * self._d1 + j)

    def unsafe_set(mut self, i: Int, value: Scalar[Self.dtype]):
        comptime assert Self.rank == 1, "x.unsafe_set(i, v) needs a 1-D array"
        self._data.unsafe_set(i, value)

    def unsafe_set(mut self, i: Int, j: Int, value: Scalar[Self.dtype]):
        comptime assert Self.rank == 2, "x.unsafe_set(i, j, v) needs a 2-D array"
        self._data.unsafe_set(i * self._d1 + j, value)


def __nb_slot(slots: Int, i: Int) -> Int:
    return Pointer[Int, MutAnyOrigin](unsafe_from_address=slots).unsafe_offset(i).unsafe_load()


def __nb_report(e: Error, alloc_fn: __NbAllocFn) -> Int:
    var msg = String(e)
    var bytes = msg.as_bytes()
    var addr = alloc_fn(-1, len(bytes), 1)
    if addr != 0:
        var p = Pointer[UInt8, MutAnyOrigin](unsafe_from_address=addr)
        for i in range(len(bytes)):
            p.unsafe_offset(i).unsafe_store(bytes[i])
    return 1
