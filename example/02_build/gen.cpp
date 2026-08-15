// gen.cpp
// Test-case generator for Two Sum.
// Uses testlib's rnd + opt<> so it can be driven by a stress-test script.
//
// Usage (examples):
//   ./gen --type=random --n=100 --seed=42
//   ./gen --type=edge_small
//   ./gen --type=edge_large
//   ./gen --type=negative
//   ./gen --type=duplicates
//   ./gen --type=max
//
// --type options:
//   random      Random array of the given size (default)
//   edge_small  n=2 (minimum size), guaranteed answer at indices 0 and 1
//   edge_large  n=1000 (maximum size), answer hidden at last two positions
//   negative    All-negative values with a negative target
//   duplicates  Array with many duplicate values; answer uses two copies
//               of the same value (tests that the checker handles i != j)
//   max         Worst-case: n=1000, values and target at the extreme ±10^9

#include "testlib.h"
#include <vector>
#include <string>
#include <iostream>

using namespace std;

static const long long MAXV = 1000000000LL;
static const int       MAXN = 1000;

// Generate a random test of size n with values in [-max_val, max_val].
// Ensures exactly one valid pair.
void gen_random(int n, long long max_val) {
    // Choose the two "answer" positions.
    int idx1 = rnd.next(0, n - 2);
    int idx2 = rnd.next(idx1 + 1, n - 1);

    long long num1   = rnd.next(-max_val, max_val);
    long long num2   = rnd.next(-max_val, max_val);
    long long target = num1 + num2;

    vector<long long> nums(n);
    nums[idx1] = num1;
    nums[idx2] = num2;

    for (int i = 0; i < n; ++i) {
        if (i == idx1 || i == idx2) continue;
        while (true) {
            long long val = rnd.next(-max_val, max_val);
            // Reject if this value creates a second valid pair.
            bool ok = true;
            if (val + num1 == target || val + num2 == target) { ok = false; }
            if (ok) {
                for (int j = 0; j < i; ++j) {
                    if (j == idx1 || j == idx2) continue;
                    if (nums[j] + val == target) { ok = false; break; }
                }
            }
            if (ok) { nums[i] = val; break; }
        }
    }

    cout << n << " " << target << "\n";
    for (int i = 0; i < n; ++i)
        cout << nums[i] << (i == n - 1 ? "\n" : " ");
}

// n=2, answer is always indices 0 and 1.
void gen_edge_small() {
    long long a = rnd.next(-MAXV, MAXV);
    long long b = rnd.next(-MAXV, MAXV);
    cout << 2 << " " << (a + b) << "\n";
    cout << a << " " << b << "\n";
}

// n=1000, answer hidden at the very last two positions.
void gen_edge_large() {
    int n = MAXN;
    long long num1   = rnd.next(-MAXV, MAXV);
    long long num2   = rnd.next(-MAXV, MAXV);
    long long target = num1 + num2;

    vector<long long> nums(n);
    nums[n - 2] = num1;
    nums[n - 1] = num2;

    for (int i = 0; i < n - 2; ++i) {
        while (true) {
            long long val = rnd.next(-MAXV, MAXV);
            bool ok = (val + num1 != target && val + num2 != target);
            if (ok) {
                for (int j = 0; j < i; ++j)
                    if (nums[j] + val == target) { ok = false; break; }
            }
            if (ok) { nums[i] = val; break; }
        }
    }

    cout << n << " " << target << "\n";
    for (int i = 0; i < n; ++i)
        cout << nums[i] << (i == n - 1 ? "\n" : " ");
}

// All-negative values, negative target.
void gen_negative() {
    int n = rnd.next(2, MAXN);
    long long num1   = rnd.next(-MAXV, -1LL);
    long long num2   = rnd.next(-MAXV, -1LL);
    long long target = num1 + num2;

    int idx1 = rnd.next(0, n - 2);
    int idx2 = rnd.next(idx1 + 1, n - 1);

    vector<long long> nums(n);
    nums[idx1] = num1;
    nums[idx2] = num2;

    for (int i = 0; i < n; ++i) {
        if (i == idx1 || i == idx2) continue;
        while (true) {
            long long val = rnd.next(-MAXV, -1LL);
            bool ok = (val + num1 != target && val + num2 != target);
            if (ok) {
                for (int j = 0; j < i; ++j)
                    if (j != idx1 && j != idx2 && nums[j] + val == target)
                        { ok = false; break; }
            }
            if (ok) { nums[i] = val; break; }
        }
    }

    cout << n << " " << target << "\n";
    for (int i = 0; i < n; ++i)
        cout << nums[i] << (i == n - 1 ? "\n" : " ");
}

// Many duplicate values. The unique answer uses two elements with the same
// numeric value (e.g. [3, 5, 3] target=6, answer indices 0 and 2).
void gen_duplicates() {
    int n = rnd.next(3, MAXN);

    // Choose a value v such that 2*v is the target.
    long long v      = rnd.next(-MAXV / 2, MAXV / 2);
    long long target = 2 * v;

    // Place v at two distinct positions.
    int idx1 = rnd.next(0, n - 2);
    int idx2 = rnd.next(idx1 + 1, n - 1);

    vector<long long> nums(n, 0LL);
    nums[idx1] = v;
    nums[idx2] = v;

    // Fill the rest with copies of v + 1 (safe: (v+1)+v == target+1 != target).
    long long fill = v + 1;
    for (int i = 0; i < n; ++i) {
        if (i == idx1 || i == idx2) continue;
        nums[i] = fill;
        // Ensure fill doesn't accidentally form a second pair with v.
        // fill + v == target+1 which != target, so this is always fine.
    }

    cout << n << " " << target << "\n";
    for (int i = 0; i < n; ++i)
        cout << nums[i] << (i == n - 1 ? "\n" : " ");
}

// Worst-case stress: n=1000, values pushed to ±10^9 boundary.
void gen_max() {
    int n            = MAXN;
    long long num1   = MAXV;
    long long num2   = -MAXV;
    long long target = num1 + num2; // = 0

    // Fill: alternate +MAXV-1 and -(MAXV-1); their sum is 0 too -- clash!
    // Use values that don't sum to 0 instead: fill with MAXV-1 for all.
    long long fill = MAXV - 1; // fill + fill = 2*(MAXV-1) != 0

    vector<long long> nums(n, fill);
    nums[0]     = num1;
    nums[n - 1] = num2;

    cout << n << " " << target << "\n";
    for (int i = 0; i < n; ++i)
        cout << nums[i] << (i == n - 1 ? "\n" : " ");
}

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    string type = opt<string>("type", "random");
    int    n    = opt<int>("n", rnd.next(2, 100));

    if (type == "random")     gen_random(n, MAXV);
    else if (type == "edge_small")  gen_edge_small();
    else if (type == "edge_large")  gen_edge_large();
    else if (type == "negative")    gen_negative();
    else if (type == "duplicates")  gen_duplicates();
    else if (type == "max")         gen_max();
    else {
        // Fall back to random if an unknown type is given.
        gen_random(n, MAXV);
    }

    return 0;
}
