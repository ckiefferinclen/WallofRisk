# CRE Leading Indicators Dashboard

Automated Streamlit dashboard for macro, industrial, office, and CRE risk indicators. Public series load automatically from FRED. RCA/MSCI and other proprietary series can load through a generic API adapter or normalized CSV export.

## Local test

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

## Deploy to GitHub and Streamlit Community Cloud

1. Create a new GitHub repository.
2. Upload all files and folders in this package, preserving `.github`, `.streamlit`, `config`, `connectors`, and `data`.
3. In Streamlit Community Cloud, choose **Create app**, select the repository, branch, and `app.py`, then deploy.
4. If MSCI confirms API access, add the secret settings from `.streamlit/secrets.toml.example` in the Streamlit app's **Secrets** settings. Never commit `secrets.toml`.
5. If using RCA exports instead, replace `data/rca_export.csv` with a normalized authorized export and push the change.

## RCA/MSCI integration

The public MSCI developer catalog identifies a Real Estate Performance & Risk Data API, but it does not publicly document an RCA transaction/distress endpoint or response schema. Therefore `connectors/rca.py` deliberately supports:

1. A configurable API endpoint map in Streamlit secrets.
2. Automatic fallback to `data/rca_export.csv`.

Required export columns:

```text
series,date,value
industrial_cap_rate,2026-06-30,6.48
office_cap_rate,2026-06-30,8.25
cre_transaction_volume,2026-06-30,95.2
```

Supported RCA series keys:

- `commercial_mortgage_rate`
- `industrial_cap_rate`
- `office_cap_rate`
- `cre_transaction_volume`
- `cre_distress`
- `office_distress`
- `office_transaction_volume`
- `cre_loan_maturities`

## Configuration

Edit `config/metrics.yml` to revise thresholds, weights, series IDs, or units. Thresholds are starting assumptions and should be approved before use in formal investment decisions.

## Important notes

- The dashboard displays observation dates, not merely refresh dates.
- A dashboard refresh cannot make a monthly or quarterly source more current than its latest publication.
- Keep licensed data in a private GitHub repository and restrict Streamlit viewers as required by your agreement.
- Verify redistribution and internal-display rights with MSCI before displaying RCA data.
