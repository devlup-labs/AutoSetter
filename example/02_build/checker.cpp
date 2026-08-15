#include "testlib.h"
#include <vector>

using namespace std;

int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);
    
    int n = inf.readInt();
    long long target = inf.readLong();
    vector<long long> nums(n);
    for (int i = 0; i < n; ++i) {
        nums[i] = inf.readLong();
    }
    
    // Read (and discard) the jury answer — we verify correctness independently.
    ans.readInt(); ans.readInt();

    int ouf1 = ouf.readInt(0, n - 1, "index1");
    int ouf2 = ouf.readInt(0, n - 1, "index2");
    
    if (ouf1 == ouf2) {
        quitf(_wa, "Indices must be distinct, got %d and %d", ouf1, ouf2);
    }
    
    if (nums[ouf1] + nums[ouf2] != target) {
        // Avoid %lld on MinGW — build the message with to_string instead.
        string got  = to_string(nums[ouf1] + nums[ouf2]);
        string want = to_string(target);
        quitf(_wa, "indices %d and %d sum to %s, expected %s",
              ouf1, ouf2, got.c_str(), want.c_str());
    }

    quitf(_ok, "Correct pair found with sum %s", to_string(target).c_str());
}
