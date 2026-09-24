# Prelude appended after every Mojo cell (D-009). Must not import std.python (D-002).
comptime __NbAllocFn = def(Int, Int) thin abi("C") -> Int


struct ArrayIn[dtype: DType](Copyable, Movable, Sized):
    var _addr: Int
    var _size: Int

    def __init__(out self, addr: Int, size: Int):
        self._addr = addr
        self._size = size

    def __len__(self) -> Int:
        return self._size

    def unsafe_ptr(self) -> Pointer[Scalar[Self.dtype], MutAnyOrigin]:
        return Pointer[Scalar[Self.dtype], MutAnyOrigin](unsafe_from_address=self._addr)

    def __getitem__(self, i: Int) raises -> Scalar[Self.dtype]:
        if i < 0 or i >= self._size:
            raise Error(String("index ", i, " out of bounds for input of length ", self._size))
        return self.unsafe_ptr().unsafe_offset(i).unsafe_load()

    # No bounds check (Mojo 1.x `unsafe_` convention). Out of range = undefined behavior.
    def unsafe_get(self, i: Int) -> Scalar[Self.dtype]:
        return self.unsafe_ptr().unsafe_offset(i).unsafe_load()


struct ArrayOut[dtype: DType](Movable, Sized):
    var _index: Int
    var _alloc_fn: __NbAllocFn
    var _addr: Int
    var _size: Int

    def __init__(out self, index: Int, alloc_fn: __NbAllocFn):
        self._index = index
        self._alloc_fn = alloc_fn
        self._addr = 0
        self._size = -1

    def alloc(mut self, n: Int) raises:
        if self._size >= 0:
            raise Error("output already allocated")
        if n < 0:
            raise Error(String("cannot allocate ", n, " elements"))
        var addr = self._alloc_fn(self._index, n)
        if addr == 0:
            raise Error(String("host could not allocate ", n, " elements"))
        self._addr = addr
        self._size = n

    def __len__(self) -> Int:
        return max(self._size, 0)

    def unsafe_ptr(self) raises -> Pointer[Scalar[Self.dtype], MutAnyOrigin]:
        if self._size < 0:
            raise Error("output used before alloc()")
        return Pointer[Scalar[Self.dtype], MutAnyOrigin](unsafe_from_address=self._addr)

    def _check(self, i: Int) raises:
        if self._size < 0:
            raise Error("output used before alloc()")
        if i < 0 or i >= self._size:
            raise Error(String("index ", i, " out of bounds for output of length ", self._size))

    def __getitem__(self, i: Int) raises -> Scalar[Self.dtype]:
        self._check(i)
        return self.unsafe_ptr().unsafe_offset(i).unsafe_load()

    def __setitem__(mut self, i: Int, value: Scalar[Self.dtype]) raises:
        self._check(i)
        self.unsafe_ptr().unsafe_offset(i).unsafe_store(value)

    # No bounds or alloc() check (Mojo 1.x `unsafe_` convention). Misuse = undefined behavior.
    def unsafe_get(self, i: Int) -> Scalar[Self.dtype]:
        return Pointer[Scalar[Self.dtype], MutAnyOrigin](unsafe_from_address=self._addr).unsafe_offset(i).unsafe_load()

    def unsafe_set(mut self, i: Int, value: Scalar[Self.dtype]):
        Pointer[Scalar[Self.dtype], MutAnyOrigin](unsafe_from_address=self._addr).unsafe_offset(i).unsafe_store(value)


def __nb_slot(slots: Int, i: Int) -> Int:
    return Pointer[Int, MutAnyOrigin](unsafe_from_address=slots).unsafe_offset(i).unsafe_load()


def __nb_report(e: Error, alloc_fn: __NbAllocFn) -> Int:
    var msg = String(e)
    var bytes = msg.as_bytes()
    var addr = alloc_fn(-1, len(bytes))
    if addr != 0:
        var p = Pointer[UInt8, MutAnyOrigin](unsafe_from_address=addr)
        for i in range(len(bytes)):
            p.unsafe_offset(i).unsafe_store(bytes[i])
    return 1
