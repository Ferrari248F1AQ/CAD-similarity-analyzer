# Methodology Traceability (Paper -> Implementation)

This document maps the criteria and workflow described in `PAPER/paper.tex` to concrete code paths.

## Core Similarity Criteria

Implemented in `solid_edge_similarity_v2.py`:

- `Author Match (AM)`:
  - raw criterion: `scores['author_match']` in `compute_raw_scores(...)`
  - binary-gated contribution in aggregation: `combine_scores(...)` multiplies `author_match` weight by AM score
- `Feature Count Similarity (FCS)`: `scores['feature_count_similarity']`
- `Feature Type Similarity (FTS)`: `scores['feature_type_similarity']`
- `Modeling Style Similarity (MS)`: `scores['style_similarity']`
- `Consecutive Pair Similarity (CP)`: `scores['bigram_similarity']`
- `Consecutive Triple Similarity (CT)`: `scores['trigram_similarity']`
- `LCS Similarity`:
  - standard/fuzzy blend in `compute_raw_scores(...)` using `lcs_fuzzy_*` settings
- `Feature Naming Similarity (FNS)`:
  - custom names only via `_is_default_feature_name(...)` + `_collect_custom_names(...)`
  - default system names are filtered out
- `2D Sketch Geometry Similarity (2DGS)`: `scores['geometry_2d_similarity']`
- `2D Sketch Constraint Similarity (2DCS)`: `scores['constraint_2d_similarity']`
- `Constraint/Geometry Ratio Similarity (CGR)`: `scores['constraint_ratio_similarity']`
- `Sketch Parametric Similarity (SPS)`:
  - `compute_sketch_parametric_similarity(...)`
  - added in `compute_raw_scores(...)` when available

## Aggregation and Fuzzy Coherence

- Weighted aggregation + criterion exclusion handling: `combine_scores(...)` in `solid_edge_similarity_v2.py`
- Optional coherence adjustment (`fuzzy_combination_*`): same function (`combine_scores(...)`)
- Frontend mirror for instant UI recombination:
  - `combineScoresClient(...)` in `webapp/static/js/app.js`

## Labeling and Training Set Construction

- Manual labels API:
  - set/delete/export endpoints in `webapp/app.py`
- Training sample build:
  - `_build_optimizer_training_samples(...)` in `webapp/app.py`
  - includes path-aware and legacy label resolution
  - includes labeled cross-session pairs
  - forces `author_match` exclusion when requested by optimizer flow

## Iterative Weight Optimization

Endpoint: `/api/paper_writing/weights_optimization_iterative` in `webapp/app.py`

- Positive class: `CONFIRMED_PLAGIARISM`
- Negative class: `NOT_PLAGIARISM`
- Objective: maximize contextual utility (implemented as minimizing negative utility)
- Violations:
  - positive below threshold
  - negative above threshold
  - computed in `_evaluate_training_samples(...)`
- Guardrail:
  - candidate is rejected when threshold violations worsen
  - baseline/candidate both returned in API result

## UI Consistency Controls

Frontend (`webapp/static/js/app.js`):

- Pair list recombination with current weights:
  - `syncPairsWithCurrentWeights(...)`
- Compare view recomputation:
  - `autoRecalculateComparison(...)`
- Analyze/Similar Pairs consistency:
  - `applyWeightsAndCompare(...)` now recomputes with current UI weights
- Optimization result actions:
  - apply/save/discard from `renderOptimizationResults(...)`
  - no implicit auto-apply on optimization run

## Export and Reporting

- LaTeX export endpoint:
  - `/api/paper_writing/export_latex` in `webapp/app.py`
- Frontend trigger:
  - `exportLatexTable(...)` in `webapp/static/js/app.js`

