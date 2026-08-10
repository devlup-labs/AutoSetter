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

# Stays inside the declared range, so the validator accepts everything it makes,
# and honours the mode contract in prompts/generator.txt: argv[1] selects a
# shape, and min/max are fixed by the constraints so the seed cannot alter them.
GENERATOR = """
#include "testlib.h"
#include <string>

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    std::string mode = argc > 1 ? std::string(argv[1]) : std::string("random");

    int n;
    if (mode == "min") n = 1;
    else if (mode == "max") n = 100;
    else if (mode == "edge") n = rnd.next(0, 1) ? 1 : 100;
    else n = rnd.next(1, 100);

    printf("%d\\n", n);
    return 0;
}
"""

# Ignores argv entirely, so every "shaped" test it is asked for is really just
# another random one and nothing reaches the declared bound on purpose. This is
# what the example's real generator does, and what _check_modes exists to catch.
GENERATOR_IGNORES_MODE = """
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    printf("%d\\n", rnd.next(1, 100));
    return 0;
}
"""

# Produces values above the stated maximum: the generator misread the
# constraints, which is one of the two ways the pipeline's files can disagree.
# Seed-independent per mode, so it passes the mode check and fails on validity
# alone -- the two checks test different things and must not overlap.
GENERATOR_OUT_OF_RANGE = """
#include "testlib.h"
#include <string>

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    std::string mode = argc > 1 ? std::string(argv[1]) : std::string("random");
    if (mode == "min") { printf("%d\\n", 101); return 0; }
    if (mode == "max") { printf("%d\\n", 200); return 0; }
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

# An independent, deliberately naive implementation of the same problem
# ("read n, print 2n"), built by summing n twice instead of multiplying.
# Agrees with SOLUTION on every input, so it's the default brute fixture.
BRUTE = """
#include <cstdio>

int main() {
    long long n;
    if (scanf("%lld", &n) != 1) return 1;
    long long total = 0;
    for (int i = 0; i < 2; ++i) total += n;
    printf("%lld\\n", total);
    return 0;
}
"""

# Computes 3n instead of 2n: disagrees with SOLUTION on every input where
# n != 0, so it's the fixture for testing that a real mismatch is caught and
# correctly blamed on solution.cpp rather than silently accepted.
BRUTE_DISAGREES = """
#include <cstdio>

int main() {
    long long n;
    if (scanf("%lld", &n) != 1) return 1;
    printf("%lld\\n", 3 * n);
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
