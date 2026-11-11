#include "Lighthouse.h"
#include <fstream>
#include <sstream>
#include <stdexcept>

int main(int argc, char** argv) {
    try {
        if (argc != 7 && argc != 8) throw runtime_error("Expected data, candidates, feature count, maximum table rows including baseline, output, tidsets, optional metrics output");
        int count = stoi(argv[3]);
        const int maximumRows = stoi(argv[4]);
        if (maximumRows < 1) throw runtime_error("Maximum table rows must be at least 1 (the baseline)");
        Lighthouse table(argv[1], count, argv[2], argv[6]);
        numm gain = 0;
        int selected = 0;
        while (selected < maximumRows - 1 && table.selectNext(gain)) ++selected;
        table.printTable(argv[5]);
        if (argc == 8) table.printMetrics(argv[7]);
        cout << "Selected patterns: " << selected << "\nTotal information gain: " << gain << "\n";
    } catch (const exception& error) {
        cerr << error.what() << "\n";
        return 1;
    }
}
