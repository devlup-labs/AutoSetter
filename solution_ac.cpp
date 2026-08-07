/*
SUBSEQUENCE vs SUBARRAY:
- A subsequence is a sequence that can be derived from another sequence by deleting some or no elements without changing the order of the remaining elements.
- A subarray is a contiguous part of an array.

PROBLEM STATEMENT:
Given an integer array nums, return all unique triplets [nums[i], nums[j], nums[k]] such that i != j, i != k, and j != k, and nums[i] + nums[j] + nums[k] == 0.
The solution set must not contain duplicate triplets.

LOGIC:
1. Sort the array to facilitate the two-pointer technique.
2. Iterate through the array with a fixed element (nums[i]).
3. Use two pointers (left and right) to find pairs that sum up to -nums[i].
4. Skip duplicates to avoid repeated triplets in the result.

EDGE CASES:
- If nums.length < 3, return an empty vector as there can't be any triplet.
- If all elements are zero, return a single triplet [0, 0, 0].

OPTIMALITY:
- Time complexity: O(n^2) due to the nested loops and two-pointer technique.
- Space complexity: O(1) extra space (excluding the output vector).
*/

#include <iostream>
#include <vector>
#include <algorithm>

std::vector<std::vector<int>> threeSum(std::vector<int>& nums) {
    std::vector<std::vector<int>> result;
    int n = nums.size();
    
    if (n < 3) return result; // Edge case: not enough elements to form a triplet
    
    std::sort(nums.begin(), nums.end()); // Sort the array
    
    for (int i = 0; i < n - 2; ++i) {
        if (i > 0 && nums[i] == nums[i - 1]) continue; // Skip duplicate elements
        
        int left = i + 1, right = n - 1;
        while (left < right) {
            int sum = nums[i] + nums[left] + nums[right];
            if (sum == 0) {
                result.push_back({nums[i], nums[left], nums[right]});
                
                // Skip duplicate elements
                while (left < right && nums[left] == nums[left + 1]) ++left;
                while (left < right && nums[right] == nums[right - 1]) --right;
                
                ++left; --right;
            } else if (sum < 0) {
                ++left;
            } else {
                --right;
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