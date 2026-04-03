# Installation

## Prerequisites

- Windows 10/11 (recommended for Solid Edge COM integration)
- Python 3.10+ (`py -3`)
- Optional but recommended for production runs:
  - Solid Edge installed (for `.par/.psm/.asm` extraction)
  - `pywin32` (COM bridge)

## 1. Create Virtual Environment

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

## 2. Install Dependencies

Use the focused webapp dependency list:

```powershell
pip install -r requirements-webapp.txt
```

If you need full multi-project dependencies, use your global requirements workflow separately.

## 3. Run the Web App

```powershell
py -3 run_webapp.py --host 127.0.0.1 --port 5000
```

Open `http://127.0.0.1:5000`.

## 4. Optional: Enable Development Mode

```powershell
py -3 run_webapp.py --debug
```

## Troubleshooting

- `SciPy not available`: install/upgrade `scipy`.
- `pywin32` errors or missing COM:
  - `pip install pywin32`
  - ensure Solid Edge is installed and licensed.
- Slow first run:
  - expected on large datasets due signature extraction and pairwise scoring.
- Label DB path:
  - stored under `%USERPROFILE%\.cache\cad_similarity_analyzer\plagiarism_labels.json`.

