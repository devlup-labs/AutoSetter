"""
Miniature C++ problem fixtures used for pipeline validation tests.

The fixture problem is: "read n, print 2n", with 1 <= n <= 100.
Contains correct and deliberately flawed implementations for testing attribution.
"""

from __future__ import annotations

VALIDATOR = """
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerValidation(argc, argv);
    inf.readInt(1, 100, "n");
    inf.readEoln();
    inf.readEof();
    return 0;
}
"""

GENERATOR = """
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    printf("%d\\n", rnd.next(1, 100));
    return 0;
}
"""

GENERATOR_OUT_OF_RANGE = """
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    printf("%d\\n", rnd.next(101, 200));
    return 0;
}
"""

SOLUTION = """
#include <cstdio>

int main() {
    long long n;
    if (scanf("%lld", &n) != 1) return 1;
    printf("%lld\\n", 2 * n);
    return 0;
}
"""

CHECKER = """
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);
    long long expected = ans.readLong();
    long long found = ouf.readLong();
    if (expected != found)
        quitf(_wa, "expected %lld, found %lld", expected, found);
    if (!ouf.seekEof())
        quitf(_pe, "extra output after the answer");
    quitf(_ok, "correct");
}
"""

CHECKER_ACCEPTS_ANYTHING = """
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);
    quitf(_ok, "looks fine to me");
}
"""

CHECKER_NEVER_COMPARES = """
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);
    ans.readLong();
    ouf.readLong();
    quitf(_ok, "looks fine to me");
}
"""

CHECKER_IGNORES_TRAILING = """
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);
    long long expected = ans.readLong();
    long long found = ouf.readLong();
    if (expected != found)
        quitf(_wa, "expected %lld, found %lld", expected, found);
    quitf(_ok, "correct");
}
"""

SAMPLES = [{"input": "5\n", "output": "10\n", "explanation": ""}]
