# CAD Similarity Analyzer (Solid Edge Similarity)

This project provides a web application to analyze pairwise similarity of CAD models, label suspected plagiarism cases, optimize criterion weights from manual labels, and export paper-ready LaTeX tables.

The current codebase has been aligned for publication-oriented use:
- consistent score pipeline between `Similar Pairs` and `Analyze` views
- manual control of optimization outcomes (apply/save/discard from result panel)
- improved startup and weight-update responsiveness
- English user-facing UI/API messages

See installation instructions in [docs/INSTALL.md](docs/INSTALL.md).

## Quick Start

1. Install dependencies (see [docs/INSTALL.md](docs/INSTALL.md)).
2. Start server:
   ```powershell
   py -3 run_webapp.py --host 127.0.0.1 --port 5000
   ```
3. Open `http://127.0.0.1:5000`.
4. Run `Analyze` on a dataset root.
5. Review `Similar Pairs`, open `Analyze`, and assign manual labels in Paper Writing Mode.
6. Run iterative weight optimization from `Weights Optimization`.
7. Decide the final solution from optimization actions:
   - `Apply Candidate in UI`
   - `Apply + Save Candidate`
   - `Discard Candidate`
8. Export tables from Paper Writing Mode.

## Reproducibility Workflow

For paper consistency, use this order:
1. Freeze global weights and threshold.
2. Run analysis on the target dataset root.
3. Label pairs (`CONFIRMED_PLAGIARISM` / `NOT_PLAGIARISM` / `UNDECIDED`).
4. Optimize weights on labeled data (author criterion forced out).
5. Apply or save only the selected solution.
6. Export LaTeX outputs after finalizing weights and threshold.

Methodology-to-code traceability is documented in [docs/METHODOLOGY_TRACEABILITY.md](docs/METHODOLOGY_TRACEABILITY.md).

## Project Layout

- `webapp/app.py`: Flask backend, analysis API, labels API, iterative optimizer, exports.
- `webapp/static/js/app.js`: frontend state, score recombination, UI flows.
- `webapp/templates/index.html`: main interface.
- `solid_edge_similarity_v2.py`: feature extraction and scoring core.
- `PAPER/paper.tex`: manuscript.

## Notes

- `author_match` is binary-gated and excluded from optimizer training by design.
- `feature_names_similarity` is computed only on non-default/custom feature names.
- LCS fuzzy and fuzzy-coherence options are configurable and represented in both backend and frontend scoring logic.

