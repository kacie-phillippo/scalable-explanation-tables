# Scalable and Hierarchy-Aware Explanation Tables

This repository contains the MINE_FWUIS implementation and prepared datasets for the paper *Scalable and Hierarchy-Aware Explanation Tables*, submitted to the 43rd IEEE International Conference on Data Engineering (ICDE), Research track.

Explanation tables describe differences in an outcome through a compact collection of patterns and their subgroup statistics. A pattern specifies a value or the wildcard symbol `*` for each dimension attribute; a wildcard matches all values of its attribute. Each pattern is associated with its aggregate value and support (how many records it covers).

By mapping attribute-value patterns to itemsets, we apply Apriori-style pruning to explanation table assembly by ruling out low-support patterns early. In addition to constants and stars, we introduce attribute hierarchies, leading to more concise explanation tables.

## Contents

- `code/` contains the Python pipeline and C++ explanation engine.
- `datasets/` contains the four prepared datasets, their hierarchy files, and the paper's illustrative example.
- `datasets/experiments.json` lists the prepared experiment inputs and settings.

This package includes MINE_FWUIS only. The Photos inputs are prepared concept features; raw images and model training are outside this package.

## Requirements

Requires Python 3.10 or later, Pillow, and a C++ compiler with Boost headers. On Windows, GCC and Boost can be installed through [MSYS2](https://www.msys2.org/). Ensure `g++` is available on PATH, or set `CXX` to its executable path. Install the Python dependency with `python -m pip install -r code/requirements.txt`. The C++ engine compiles automatically on the first run.

## Run the code

Run from the folder containing this README. Choose your own threshold and maximum table size; `0.1` and `5` below are example values.

```sh
python code/run_pipeline.py datasets/example/error.csv --hierarchy datasets/example/hierarchies.csv --minwus 0.1 --rows 5
```

| Argument | Meaning |
|---|---|
| `dataset` | Input CSV; the final column is the numeric outcome. |
| `--hierarchy` | Hierarchy CSV. Use a header-only file for flat mode. |
| `--minwus` | Your chosen minimum weighted utility support threshold, greater than 0 and at most 1. |
| `--rows` | Maximum total table rows, including the baseline row; at least 1. |
| `--presence-only-binary` | Treat binary zeros as absent. Add this flag when running Photos. |
| `--output-dir` | Optional output folder; defaults to `outputs/`. |

Each run saves `explanations.csv`, `explanations.png`, and `report.json` in a numbered folder under `outputs/reports/`. The explanation CSV uses semicolons. The table can contain fewer rows than requested.

## Datasets

**Flight.** Flight contains departures from Los Angeles International Airport (LAX) during May 2026. Each record contains seven dimension attributes: day of week, operating airline, destination airport, destination city, scheduled departure time, air time, and flight distance. The outcome indicates whether departure was delayed by at least 15 minutes.

**Photos.** Photos contains images from the coffee shop and restaurant classes of the indoor Places dataset. We use POEM with a ResNet-18 classifier to extract 25 binary dimension attributes indicating the presence of semantic concepts such as chairs, tables, food, and windows. The outcome is one when the prediction of the classifier agrees with the true class label of the image and zero otherwise.

**COMPAS.** COMPAS contains recidivism records from Broward County, Florida. The dimension attributes are sex, race, age, custody status, and marital status. The provided prediction is the recidivism score computed by the COMPAS model, with seven or more defining a positive prediction. The outcome indicates whether the prediction agrees with the ground truth provided in the dataset.

**Synthetic.** Finally, the synthetic dataset is used for scalability experiments by varying the number of dimension attributes. Each attribute has 32 equally frequent values, and exactly 50% of records have a positive outcome. One dimension attribute has outcome rates of 0%, 25%, 50%, 75%, and 100% across its values; the remaining attributes have outcome rate 50% for every value.

Use the following input pairs with the command above:

| Dataset | Data CSV | Hierarchy CSV |
|---|---|---|
| Flight | `datasets/flight/data/flight.csv` | `datasets/flight/hierarchies/flight.csv` |
| Photos | `datasets/photos/data/photos.csv` | `datasets/photos/hierarchies/photos.csv` |
| COMPAS | `datasets/compas/data/compas.csv` | `datasets/compas/hierarchies/hierarchical/compas.csv` |
| Synthetic | `datasets/synthetic/data/protocol_a04_n4096.csv` | `datasets/synthetic/hierarchies/protocol_a04_depth4.csv` |

The additional files are versions of these four datasets prepared for the paper's scalability experiments. They vary the number of records, the number of attributes, or the hierarchy depth. For example, `flight_40.csv` contains a 40% record subset, `flight_attr_3.csv` contains three explanatory attributes, and `protocol_a04_n8192.csv` contains four attributes and 8,192 records. Matching hierarchy files define the groups available for each input. See `datasets/experiments.json` for the input pairings.

For the illustrative example, use `datasets/example/error.csv` as shown above.

## Using your own data

Supply a CSV with a header, at least one explanatory feature, and a final numeric outcome column. Outcomes must be finite and nonnegative. Missing values are rejected; use an explicit text category for missing feature values. Numeric features are not automatically binned.

The hierarchy CSV uses this header:

```csv
feature,node,parent,members,lower,upper,closed
```

Each row defines a named node for a feature. Use a JSON array in `members` for categories, or `lower`, `upper`, and `closed` for numeric ranges. A node can instead inherit the union of its children. Leave `parent` empty for a top-level node. Node names must be unique within a feature, parent links must be acyclic, and supplied hierarchies must cover observed values. Features without hierarchy definitions remain flat. See the supplied hierarchy files for examples; a header-only file requests an entirely flat run.

## Citations

The dataset sources and methods used in this package are described in the following references:

- **Flight:** Bureau of Transportation Statistics. [On-Time: Marketing Carrier On-Time Performance](https://transtats.bts.gov/Fields.asp?gnoyr_VQ=FGK). United States Department of Transportation, May 2026. The supplied inputs use departures from LAX.
- **Photos:** B. Zhou, A. Lapedriza, A. Khosla, A. Oliva, and A. Torralba. [Places: A 10 Million Image Database for Scene Recognition](https://doi.org/10.1109/TPAMI.2017.2723009). *IEEE Transactions on Pattern Analysis and Machine Intelligence*, 40(6):1452–1464, 2018. The supplied concept features were extracted using POEM: V. Dadvar, L. Golab, and D. Srivastava. [POEM: Pattern-Oriented Explanations of Convolutional Neural Networks](https://doi.org/10.14778/3611479.3611518). *Proceedings of the VLDB Endowment*, 16(11):3192–3200, 2023.
- **COMPAS:** ProPublica. [COMPAS Analysis: Data and Analysis for Machine Bias](https://github.com/propublica/compas-analysis), 2016.
- **Synthetic:** Generated for this study, *Scalable and Hierarchy-Aware Explanation Tables*, by Kacie Phillippo, Parke Godfrey, Lukasz Golab, and Jarek Szlichta. It has no external dataset citation; its construction is described under Datasets above.
- **Explanation Tables:** K. El Gebaly, P. Agrawal, L. Golab, F. Korn, and D. Srivastava. [Interpretable and Informative Explanations of Outcomes](https://www.vldb.org/pvldb/vol8/p61-elgebaly.pdf). *Proceedings of the VLDB Endowment*, 8(1):61–72, 2014. See also K. El Gebaly, G. Feng, L. Golab, F. Korn, and D. Srivastava. Explanation Tables. *IEEE Data Engineering Bulletin*, 41(3):43–51, 2018.
- **MINE_FWUIS:** H. Nguyen, T. Le, M. Nguyen, P. Fournier-Viger, V. S. Tseng, and B. Vo. [Mining Frequent Weighted Utility Itemsets in Hierarchical Quantitative Databases](https://doi.org/10.1016/j.knosys.2021.107709). *Knowledge-Based Systems*, 237:107709, 2022.
