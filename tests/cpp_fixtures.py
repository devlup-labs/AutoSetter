"""A complete miniature problem, in C++, for the pipeline tests to chew on.

The problem is "read n, print 2n", with 1 <= n <= 100. It is deliberately the
smallest thing that still has all four artifacts, so a test can swap one of
them for a broken version and assert that the pipeline notices.
"""

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

# Stays inside the declared range, so the validator accepts everything it makes.
GENERATOR = """
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    printf("%d\\n", rnd.next(1, 100));
    return 0;
}
"""

# Produces values above the stated maximum: the generator misread the
# constraints, which is one of the two ways the pipeline's files can disagree.
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

# Compares the submission with the jury answer and insists the output ends.
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

# Approves without looking at anything. Caught by the ordinary per-test check
# rather than by a probe: testlib refuses to let a checker quit _ok while the
# contestant's output still has unread tokens in it, so this reports PE on the
# reference answer itself.
CHECKER_ACCEPTS_ANYTHING = """
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);
    quitf(_ok, "looks fine to me");
}
"""

# Reads both files, consuming the output so testlib is satisfied, and then
# never compares them. This is the one the old pipeline could not catch: it
# accepts the reference answer (so every test "passes"), and it survives the
# empty and truncated probes because those fail on the read. Only handing it a
# well-formed *wrong* answer exposes it.
CHECKER_NEVER_COMPARES = """
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);
    ans.readLong();
    ouf.readLong();
    quitf(_ok, "looks fine to me");
}
"""

# Right answers accepted, but it never checks for trailing garbage. A real
# weakness, and one the advisory probes should surface without calling the
# checker untrustworthy.
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
