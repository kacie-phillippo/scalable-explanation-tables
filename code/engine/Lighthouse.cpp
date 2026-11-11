#include "Lighthouse.h"
#include "duf.hpp"
#include <fstream>
#include <sstream>
#include <numeric>
#include <cstdint>

static uint32_t readId(istream& stream) {
    unsigned char bytes[4];
    if (!stream.read(reinterpret_cast<char*>(bytes), 4)) throw runtime_error("Truncated tidset file");
    return uint32_t(bytes[0]) | (uint32_t(bytes[1]) << 8) | (uint32_t(bytes[2]) << 16) | (uint32_t(bytes[3]) << 24);
}

// Score every remaining candidate using normalized observed and estimated
// outcomes. Remove selected conditions before the next greedy iteration.
bool Lighthouse::selectNext(numm& totalInformationGain) {
    ++counters["greedy_iterations_attempted"];
    loadedPatternCandidates.erase(remove_if(loadedPatternCandidates.begin(), loadedPatternCandidates.end(),
        [&](const Candidate& candidate) {
            for (auto& selected : patterns) {
                auto copy = candidate.conditions;
                if (patternsEqualNew(selected, copy)) return true;
            }
            return false;
        }), loadedPatternCandidates.end());
    vector<numm> observed, estimated;
    ++counters["outcome_estimate_extraction_scans"];
    for (const auto& tuple : tuplesVectorNew) {
        observed.push_back(tuple.measure);
        estimated.push_back(tuple.estimate);
    }
    counters["normalization_sum_scans"] += 2;
    numm totalObserved = accumulate(observed.begin(), observed.end(), 0.0);
    numm totalEstimated = accumulate(estimated.begin(), estimated.end(), 0.0);
    if (totalObserved == 0) return false;
    ++counters["normalization_division_scans"];
    for (size_t i = 0; i < observed.size(); ++i) {
        observed[i] /= totalObserved;
        estimated[i] /= totalEstimated;
    }
    vector<numm> best;
    size_t bestIndex = 0;
    numm bestGain = 0;
    for (size_t index = 0; index < loadedPatternCandidates.size(); ++index) {
        ++counters["candidate_evaluations"];
        const auto& entry = loadedPatternCandidates[index];
        auto candidate = entry.conditions;
        numm numerator = 0, denominator = 0;
        for (indd tid : entry.tids) {
            numerator += observed[tid];
            denominator += estimated[tid];
        }
        if (numerator <= 0 || denominator <= 0) continue;
        numm gain = 0;
        // Sum per record to preserve floating-point order; do not factor the log out.
        for (indd tid : entry.tids) {
            gain += observed[tid] * log(numerator / denominator);
        }
        candidate[2 * D + 1] = entry.support;
        if (isfinite(gain) && gain > 0 && (gain > bestGain || (gain == bestGain && candidate > best))) {
            bestGain = gain;
            best = candidate;
            bestIndex = index;
        }
    }
    if (best.empty()) return false;
    selectedCandidateIds.push_back(loadedPatternCandidates[bestIndex].sourceIndex);
    selectedGains.push_back(bestGain);
    addRuleNewDebug(best, loadedPatternCandidates[bestIndex].tids, loadedPatternCandidates[bestIndex].support);
    loadedPatternCandidates.erase(remove_if(loadedPatternCandidates.begin(), loadedPatternCandidates.end(),
        [&](Candidate& candidate) { return patternsEqualNew(candidate.conditions, best); }), loadedPatternCandidates.end());
    iterativeScalingNewRCT();
    totalInformationGain += bestGain;
    return true;
}

std::string formatNumber(double number) {
    std::ostringstream oss;
    oss << std::fixed << std::setprecision(3) << number;
    return oss.str();
}

// The Python adapter supplies validated, dense integer feature IDs. Raw
// text, numeric ranges and arbitrary CSV quoting never enter this bridge.
Lighthouse::Lighthouse(string filename, indd dimensions, string candidatesFilename, string tidsetsFilename)
{
    D = dimensions;
    totalN = 0;
    attributeNames.resize(D + 1);
    keyToAttribute.resize(D);
    ifstream data(filename);
    if (!data) throw runtime_error("Could not open encoded data");
    string line, cell;
    getline(data, line);
    stringstream header(line);
    for (int d = 0; d <= D; ++d) getline(header, attributeNames[d], ',');
    while (getline(data, line)) {
        stringstream stream(line);
        vector<numm> tuple;
        for (int d = 0; d < D; ++d) {
            if (!getline(stream, cell, ',')) throw runtime_error("Malformed encoded data row");
            int value = stoi(cell);
            tuple.push_back(value);
            keyToAttribute[d][value] = value;
        }
        if (!getline(stream, cell, ',')) throw runtime_error("Missing numeric outcome");
        tuples_new.push_back(tuple);
        tuplesVectorNew.push_back({boost::dynamic_bitset<>(0), stod(cell), 1});
        ++totalN;
    }
    if (!totalN) throw runtime_error("Encoded data has no rows");
    ifstream tidsets(tidsetsFilename, ios::binary);
    char magic[5];
    if (!tidsets.read(magic, 5) || string(magic, 5) != "TIDS1") throw runtime_error("Invalid tidset file");
    if (readId(tidsets) != static_cast<uint32_t>(totalN)) throw runtime_error("Tidset dataset row count mismatch");
    uint32_t candidateCount = readId(tidsets);
    ifstream candidates(candidatesFilename);
    if (!candidates) throw runtime_error("Could not open mined candidates");
    getline(candidates, line);
    while (getline(candidates, line)) {
        stringstream stream(line);
        vector<numm> pattern(2 * D + 3, 0);
        for (int c = 0; c < 3; ++c) getline(stream, cell, ',');
        int support = stoi(cell);
        uint32_t tidCount = readId(tidsets);
        if (support <= 0 || tidCount != static_cast<uint32_t>(support) || tidCount > static_cast<uint32_t>(totalN))
            throw runtime_error("Tidset/support count mismatch");
        vector<indd> tids;
        tids.reserve(tidCount);
        for (uint32_t i = 0; i < tidCount; ++i) {
            uint32_t tid = readId(tidsets);
            if (tid >= static_cast<uint32_t>(totalN) || (!tids.empty() && tid <= static_cast<uint32_t>(tids.back())))
                throw runtime_error("Tidset IDs must be sorted, unique and in dataset bounds");
            tids.push_back(static_cast<indd>(tid));
        }
        for (int d = 0; d < D; ++d) {
            if (!getline(stream, cell, ',')) throw runtime_error("Malformed candidate row");
            pattern[D + d] = keyToAttribute[d].size() - 1;
            if (cell == "*") continue;
            size_t equals = cell.find('=');
            if (equals == string::npos) throw runtime_error("Malformed candidate condition");
            stringstream members(cell.substr(equals + 1));
            string member;
            vector<int> ids;
            while (getline(members, member, '|')) {
                int id = stoi(member);
                if (!keyToAttribute[d].count(id)) throw runtime_error("Unknown candidate member");
                ids.push_back(id);
            }
            if (ids.empty()) throw runtime_error("Empty candidate condition");
            sort(ids.begin(), ids.end());
            ids.erase(unique(ids.begin(), ids.end()), ids.end());
            for (size_t i = 1; i < ids.size(); ++i)
                if (ids[i] != ids[i-1] + 1) throw runtime_error("Noncontiguous candidate condition");
            pattern[d] = ids.front();
            pattern[D+d] = ids.back();
        }
        pattern[2 * D + 1] = support;
        loadedPatternCandidates.push_back({pattern, move(tids), support, loadedPatternCandidates.size()});
    }
    if (loadedPatternCandidates.size() != candidateCount || tidsets.peek() != char_traits<char>::eof())
        throw runtime_error("Tidset candidate count or length mismatch");
    vector<numm> baseline(2 * D + 3, 0);
    for (int d = 0; d < D; ++d) baseline[D+d] = keyToAttribute[d].size() - 1;
    baseline[2*D+1] = totalN;
    vector<indd> baselineTids(totalN);
    iota(baselineTids.begin(), baselineTids.end(), 0);
    addRuleNewDebug(baseline, baselineTids, totalN);
    iterativeScalingNewRCT();
}

Lighthouse::~Lighthouse()
{
}

// Equality depends on feature bounds, excluding support and reserved slots.
bool Lighthouse::patternsEqualNew(vector<numm>& pattern1, vector<numm>& pattern2)
{
	for (indd i = 0; i < D; ++i) {
		if (pattern1[i] != pattern2[i] || pattern1[D+i] != pattern2[D+i]) { return false; }
	}
	
	return true;
}

void Lighthouse::addRuleNewDebug(vector<numm>& rule, const vector<indd>& tids, indd support)
{

	numm tuplesMatchR = support;

	numm expectedMeasureAverage = 0;

    ++counters["membership_initialization_scans"];
    for (auto& tuple : tuplesVectorNew) tuple.bitArray.push_back(0);
    for (indd tid : tids) {
        tuplesVectorNew[tid].bitArray[patterns.size()] = 1;
        expectedMeasureAverage += tuplesVectorNew[tid].measure;
    }

	RCT_New.clear();

    // group_by indexes every tuple, then copies each tuple into its group.
    ++counters["region_grouping_index_scans"];
    ++counters["region_grouping_copy_scans"];
	auto groups = group_by(tuplesVectorNew, &tupleT::bitArray);
    // The disjoint groups collectively visit N tuples in each of these passes.
    ++counters["region_local_copy_scans"];
    ++counters["region_aggregation_scans"];

	for (auto iter = groups.begin(); iter != groups.end(); ++iter) {
		boost::dynamic_bitset<> bitArrayTemp = get<0>(iter->first);
		vector<tupleT> tuplesVectorTempGroup = iter->second;
		numm sumMeasure = 0;
		numm sumEstimate = 0;
		for_each(tuplesVectorTempGroup .begin(), tuplesVectorTempGroup.end(), [&](tupleT i) {
			sumMeasure += i.measure;
			sumEstimate += i.estimate;
		});

		vector<numm> rctBaseTemp(3);
		rctBaseTemp[0] = tuplesVectorTempGroup.size();
		rctBaseTemp[1] = sumMeasure;
		rctBaseTemp[2] = sumEstimate;
		RCT_New.emplace(bitArrayTemp, rctBaseTemp);

	}

	expectedMeasureAverage = expectedMeasureAverage/tuplesMatchR;
	ruleMeasures.push_back(expectedMeasureAverage);

	patterns.push_back(rule);
	lambdasNew.push_back(1);

}

// Update region estimates until all selected-pattern means meet the tolerance.
void Lighthouse::iterativeScalingNewRCT()
{

	int rules_size = patterns.size();

	vector<numm> diffArray(rules_size); // Entries are zero-initialized

	vector<numm> ruleMeasureEstimates(rules_size); // Entries are zero-initialized

	numm epsilon = 0.01;

	bool divergent = true;
	int iterations = 0;
	while (divergent) {
        ++counters["iterative_scaling_iterations"];
        if (++iterations > 100000) throw runtime_error("Iterative scaling did not converge");

		for (indd p = 0; p < rules_size; ++p) {


			numm sumSumEstimates = 0;
			numm sumCount = 0;

    			for (auto i = RCT_New.begin(); i != RCT_New.end(); i++) {
				if (i->first[p] == 1) {
					sumCount += i->second[0];
					sumSumEstimates += i->second[2];
				}
			}

			ruleMeasureEstimates[p] = sumSumEstimates/sumCount;
			diffArray[p] = abs(ruleMeasures[p] - ruleMeasureEstimates[p]) / max(abs(ruleMeasures[p]), 1e-12);

		}
		numm nextDiff = *std::max_element(diffArray.begin(), diffArray.end());

		if (nextDiff > epsilon) {
			indd indexNextDiff = find(diffArray.begin(), diffArray.end(), nextDiff) - diffArray.begin();

			numm lambdaOld = lambdasNew[indexNextDiff];
			lambdasNew[indexNextDiff] = lambdasNew[indexNextDiff] * (ruleMeasures[indexNextDiff] / ruleMeasureEstimates[indexNextDiff]);

    			for (auto i = RCT_New.begin(); i != RCT_New.end(); i++) {
				if (i->first[indexNextDiff] == 1) {
					i->second[2] = i->second[2] * (lambdasNew[indexNextDiff] / lambdaOld );
				}
			}

		} else {

            ++counters["estimate_materialization_scans"];
			for (indd tupleIndex = 0; tupleIndex < tuples_new.size(); ++tupleIndex){

				numm t_m = 1;

				for (indd i = 0; i < rules_size; ++i) {

					if (tuplesVectorNew[tupleIndex].bitArray[i] == 1) {
						t_m *= lambdasNew[i];
					}
				}
				tuplesVectorNew[tupleIndex].estimate = t_m;

			}
			
			divergent = false;
			break;
		}
	}
}

// Write inclusive feature bounds, three-decimal means, and supports using semicolons.
void Lighthouse::printTable(string fileName)
{
    ofstream file(fileName);
    if (!file) throw runtime_error("Could not write explanation table");
    for (int d = 0; d <= D; ++d) file << attributeNames[d] << ";";
    file << "support\n";
    for (size_t p = 0; p < patterns.size(); ++p) {
        for (int d = 0; d < D; ++d) {
            int first = patterns[p][d], last = patterns[p][D+d];
            if (first == 0 && last == static_cast<int>(keyToAttribute[d].size()) - 1) file << "*";
            else if (first == last) file << first;
            else file << first << "," << last;
            file << ";";
        }
        file << formatNumber(ruleMeasures[p]) << ";" << static_cast<int>(patterns[p][2*D+1]) << "\n";
    }
}

// Export counters and selection-time gains at full double precision.
void Lighthouse::printMetrics(string fileName) {
    ofstream file(fileName);
    if (!file) throw runtime_error("Could not write engine metrics");
    file << setprecision(17) << "{\"counters\":{";
    bool first = true;
    for (const auto& entry : counters) {
        if (!first) file << ',';
        first = false;
        file << '"' << entry.first << "\":" << entry.second;
    }
    file << "},\"selected_rows\":[";
    for (size_t i = 0; i < selectedCandidateIds.size(); ++i) {
        if (i) file << ',';
        file << "{\"candidate_index\":" << selectedCandidateIds[i]
             << ",\"information_gain\":" << selectedGains[i] << '}';
    }
    file << "]}\n";
    if (!file) throw runtime_error("Could not finish engine metrics");
}
