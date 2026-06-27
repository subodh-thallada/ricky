def fibonacci(n: int) -> int:
    if n < 0:
        raise ValueError('n must be non-negative')
    def _fib(k: int) -> tuple[int, int]:
        if k == 0:
            return 0, 1
        a, b = _fib(k >> 1)
        c = a * ((b << 1) - a)
        d = a * a + b * b
        return (d, c + d) if (k & 1) else (c, d)
    return _fib(n)[0]