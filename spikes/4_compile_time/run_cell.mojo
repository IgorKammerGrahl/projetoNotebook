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

def main():
    var t = 0
    for i in range(1000):
        t += mandel(Float64(i) / 1000.0 * 3.0 - 2.0, 0.1, 200)
    print(t)
