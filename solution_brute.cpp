#include <iostream>
#include <vector>
#include <algorithm>

/* 
Search Space:
The search space for this problem is the set of all possible triplets (i, j, k) where i, j, and k are indices of the array nums such that 0 <= i < j < k < nums.size(). This means we need to consider every combination of three distinct elements from the array.

To ensure completeness, we will use a brute-force approach with three nested loops:
1. The outer loop iterates over all possible values of i.
2. The middle loop iterates over all possible values of j greater than i.
3. The inner loop iterates over all possible values of k greater than j.

For each triplet (i, j, k), we will check if nums[i] + nums[j] + nums[k] == 0. If it does, we will add the triplet to our result set, ensuring that we do not include duplicate triplets by checking for uniqueness before adding.
*/

std::vector<std::vector<int>> threeSum(std::vector<int>& nums) {
    std::sort(nums.begin(), nums.end()); // Sort the array to handle duplicates and simplify the search
    std::vector<std::vector<int>> result;
    
    for (int i = 0; i < nums.size() - 2; ++i) {
        if (i > 0 && nums[i] == nums[i - 1]) continue; // Skip duplicate elements
        
        for (int j = i + 1; j < nums.size() - 1; ++j) {
            if (j > i + 1 && nums[j] == nums[j - 1]) continue; // Skip duplicate elements
            
            for (int k = j + 1; k < nums.size(); ++k) {
                if (nums[i] + nums[j] + nums[k] == 0) {
                    result.push_back({nums[i], nums[j], nums[k]});
                }
            }
        }
    }
    
    return result;
}

int main() {
    std::vector<int> nums = {-1, 0, 1, 2, -1, -4};
    auto triplets = threeSum(nums);
    
    for (const auto& triplet : triplets) {
        std::cout << "[" << triplet[0] << ", " << triplet[1] << ", " << triplet[2] << "]" << std::endl;
    }
    
    return 0;
}