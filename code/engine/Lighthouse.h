#ifndef LIGHTHOUSE_H
#define LIGHTHOUSE_H
#include <algorithm>
#include <cmath>
#include <iomanip>
#include <iostream>
#include <map>
#include <stdexcept>
#include <unordered_map>
#include <vector>
#include <boost/dynamic_bitset.hpp>
#include <boost/container_hash/hash.hpp>

using namespace std;
using indd = int;
using numm = double;

class Lighthouse {
    struct tupleT {
        boost::dynamic_bitset<> bitArray;
        numm measure;
        numm estimate;
    };
    vector<tupleT> tuplesVectorNew;
    unordered_map<boost::dynamic_bitset<>, vector<numm>> RCT_New;
    vector<vector<numm>> tuples_new;
    vector<string> attributeNames;
    vector<unordered_map<indd, numm>> keyToAttribute;
    indd D, totalN;
    vector<numm> lambdasNew, ruleMeasures;
    struct Candidate {
        vector<numm> conditions;
        vector<indd> tids;
        indd support;
        size_t sourceIndex;
    };
    vector<vector<numm>> patterns;
    vector<Candidate> loadedPatternCandidates;
    map<string, unsigned long long> counters;
    vector<size_t> selectedCandidateIds;
    vector<numm> selectedGains;
    bool patternsEqualNew(vector<numm>& first, vector<numm>& second);
    void addRuleNewDebug(vector<numm>& rule, const vector<indd>& tids, indd support);
    void iterativeScalingNewRCT();
public:
    Lighthouse(string filename, indd dimensions, string candidatesFilename, string tidsetsFilename);
    ~Lighthouse();
    bool selectNext(numm& totalInformationGain);
    void printTable(string fileName);
    void printMetrics(string fileName);
};
#endif
