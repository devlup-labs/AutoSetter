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

# Honours the mode contract in prompts/generator.txt: argv[1] selects a shape,
# and `min`/`max` are fully determined by the constraints, so the seed cannot
# change them.
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

# Ignores argv entirely and always produces a random test. Every "shaped" test
# it is asked for is really just another random one, so nothing ever reaches
# the declared bound of 100 on purpose. This is what the real Two Sum
# generator does, and what _check_modes exists to catch.
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
# alone — the two checks are testing different things and must not overlap.
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


# ---------------------------------------------------------------------------
# A multitest problem, for the budget modes.
#
#   line 1:  t                     1 <= t <= 10
#   then t test cases, each:
#     line:  n                     1 <= n <= 100
#     line:  n integers            1 <= a_i <= 100
#   and:     sum of n over all test cases <= 200
#
# Print the maximum of each test case. The sum cap is the point: it means
# t = 10 with n = 100 each is illegal, and there is no single largest test.
# ---------------------------------------------------------------------------

MULTI_VALIDATOR = """
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerValidation(argc, argv);
    int t = inf.readInt(1, 10, "t");
    inf.readEoln();

    long long sum_n = 0;
    for (int tc = 0; tc < t; tc++) {
        int n = inf.readInt(1, 100, "n");
        inf.readEoln();
        sum_n += n;
        for (int i = 0; i < n; i++) {
            inf.readInt(1, 100, format("a[%d]", i));
            if (i + 1 < n) inf.readSpace();
        }
        inf.readEoln();
    }

    ensuref(sum_n <= 200, "sum of n is %lld, over the limit of 200", sum_n);
    inf.readEof();
    return 0;
}
"""

MULTI_GENERATOR = """
#include "testlib.h"
#include <string>
#include <vector>

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    std::string mode = argc > 1 ? std::string(argv[1]) : std::string("random");

    const int BUDGET = 200, NMAX = 100, TMAX = 10;
    std::vector<int> sizes;

    if (mode == "min") {
        sizes.assign(1, 1);
    } else if (mode == "max") {
        // The heaviest legal file: spend the whole budget in the largest
        // cases the per-case bound allows. Not t=TMAX with n=NMAX, which
        // would be 1000 and illegal.
        sizes.assign(BUDGET / NMAX, NMAX);
    } else if (mode == "one_big") {
        sizes.assign(1, NMAX);
    } else if (mode == "many_small") {
        sizes.assign(TMAX, BUDGET / TMAX);
    } else if (mode == "skewed") {
        sizes.assign(TMAX, 1);
        sizes[0] = NMAX;
    } else {
        int t = rnd.next(1, TMAX);
        sizes.assign(t, 1);
        int spare = BUDGET - t;
        for (int i = 0; i < t && spare > 0; i++) {
            int give = rnd.next(0, std::min(spare, NMAX - sizes[i]));
            sizes[i] += give;
            spare -= give;
        }
    }

    long long total = 0;
    for (int s : sizes) total += s;
    ensuref(total <= BUDGET, "the split spends %lld, over the budget", total);

    printf("%d\\n", (int)sizes.size());
    for (int n : sizes) {
        printf("%d\\n", n);
        for (int i = 0; i < n; i++) {
            int v = (mode == "min") ? 1 : (mode == "max" ? 100 : rnd.next(1, 100));
            printf("%d%c", v, i + 1 == n ? '\\n' : ' ');
        }
    }
    return 0;
}
"""

MULTI_SOLUTION = """
#include <cstdio>
#include <algorithm>

int main() {
    int t;
    if (scanf("%d", &t) != 1) return 1;
    while (t--) {
        int n; scanf("%d", &n);
        int best = 0;
        for (int i = 0; i < n; i++) { int x; scanf("%d", &x); best = std::max(best, x); }
        printf("%d\\n", best);
    }
    return 0;
}
"""

MULTI_CHECKER = """
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);
    int index = 0;
    while (!ans.seekEof()) {
        index++;
        long long expected = ans.readLong();
        long long found = ouf.readLong();
        if (expected != found)
            quitf(_wa, "line %d: expected %lld, found %lld", index, expected, found);
    }
    if (!ouf.seekEof())
        quitf(_pe, "%d lines were expected, but the output continues", index);
    quitf(_ok, "%d line(s) correct", index);
}
"""

# Handles min/max correctly, so it survives the seed-invariance check, but
# treats one_big and many_small as ordinary random tests. The distinction the
# budget modes exist for is never made: the same total, always the same shape.
MULTI_GENERATOR_IGNORES_BUDGET = """
#include "testlib.h"
#include <string>

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    std::string mode = argc > 1 ? std::string(argv[1]) : std::string("random");

    int t, n, v;
    if (mode == "min") { t = 1; n = 1; v = 1; }
    else if (mode == "max") { t = 2; n = 100; v = 100; }
    else { t = 3; n = 5; v = rnd.next(1, 100); }

    printf("%d\\n", t);
    for (int i = 0; i < t; i++) {
        printf("%d\\n", n);
        for (int j = 0; j < n; j++)
            printf("%d%c", (mode == "min" || mode == "max") ? v : rnd.next(1, 100),
                   j + 1 == n ? '\\n' : ' ');
    }
    return 0;
}
"""

MULTI_SAMPLES = [{"input": "1\n3\n1 5 2\n", "output": "5\n", "explanation": ""}]

# The statement text the multitest detector reads. Only the phrasing matters.
MULTI_PROBLEM = {
    "input_format": (
        "The first line contains a single integer t, the number of test cases.\n"
        "Each test case consists of a line with n, then a line with n integers."
    ),
    "constraints": (
        "1 <= t <= 10\n1 <= n <= 100\n1 <= a_i <= 100\n"
        "The sum of n over all test cases does not exceed 200."
    ),
    "samples": MULTI_SAMPLES,
}
