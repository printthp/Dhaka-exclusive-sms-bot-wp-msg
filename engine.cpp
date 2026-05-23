#include <iostream>
extern "C" {
    // এটি অ্যাসেম্বলি এবং সি++ সমন্বিত হাই-পারফরম্যান্স ফাংশন
    int process_logic(int data) {
        int result;
        // অ্যাসেম্বলি নির্দেশ: ডাটাকে সরাসরি প্রসেসর রেজিস্টারে গুন করা
        __asm__ ("imul %1, %1" : "=r" (result) : "0" (data));
        return result;
    }
}
