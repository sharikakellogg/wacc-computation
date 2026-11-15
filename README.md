# Cost of Capital, Two Currencies

A Streamlit app that compares one project's cost of equity, cost of debt and WACC in U.S. dollars and in the currency
of a selected country. All figures are shown to two decimals.

## What it does

- Fetches industry betas and the BBB spread from Aswath Damodaran, statutory corporate tax rates from the OECD, and
  10-year government yields from Trading Economics.
- Shows every input with its source, as-of date and retrieval time, and has a **Refresh data** button.
- Lets you pick an industry, a target country, the equity risk premium and the target debt share.
- Lets you override the unlevered beta, U.S. and country tax rates, USD BBB spread, both 10-year yields, and the
  country BBB spread. With the country spread override off, the app shows its automatic value.
- Has a switch for the project tax rate: United States or the selected country.
- Shows USD and local-currency results side by side, an all-industry comparison, a step-by-step calculation, and CSV
  downloads.

Defaults on opening: Software (System & Application), United States, U.S. tax rate, 6.50% equity risk premium, 30%
debt share.

## Formulas

| Item | Formula |
| --- | --- |
| D/E | D/V / (1 - D/V) |
| Levered beta | unlevered beta x (1 + (1 - t) x D/E), debt beta of zero |
| Cost of equity, USD | U.S. 10-year yield + levered beta x ERP |
| Cost of debt, USD | U.S. 10-year yield + USD BBB spread |
| Rate in local currency | (1 + USD rate) x (1 + local 10-year yield) / (1 + U.S. 10-year yield) - 1 |
| Local BBB spread (auto) | converted USD cost of debt - local 10-year yield |
| WACC | E/V x Ke + D/V x Kd x (1 - t) |

The same tax rate t, debt share and ERP are used in both currencies. These are standard textbook choices. If your course
uses different formulas (for example an inflation differential instead of a yield differential), edit `convert_rate()`
and `cost_of_capital()` in `app.py`. Nothing else depends on them.

Calculations use unrounded inputs. Displays and CSV exports are rounded to two decimals, so a hand calculation from
displayed figures can differ by 0.01.

## Data sources

| Input | Source | What the app reads |
| --- | --- | --- |
| Unlevered beta | Damodaran, `betas.xls` (U.S. industries) | "Unlevered beta" column. Falls back to the `Betas.html` page. |
| USD BBB spread | Damodaran, ratings page | Baa2/BBB row for large non-financial firms |
| Corporate tax rate | OECD Corporate Tax Statistics, combined statutory rate | OECD data service first, the report page second |
| 10-year yields | Trading Economics | The `/bonds` table first, the country page second |

Notes and assumptions:

- The beta used is "Unlevered beta", not "Unlevered beta corrected for cash".
- The app queries the OECD Corporate Income Tax dataset through its current SDMX endpoint, selecting the combined
  statutory corporate income tax rate for general government (S13). It keeps the latest observation for each country
  and falls back to the report page if the data service is unavailable. The latest year available is shown in the
  sources table.
- Trading Economics publishes dates without a year in its table (for example "Nov/13"). The app shows the date as
  published.
- Websites change and may block automated requests. If a source fails, the app says which one, and you can enter the
  value under **Manual overrides**.
- The country list is a fixed list of about 60 economies, each with a matching Trading Economics page.

## Run locally

Python 3.12 is recommended.

```bash
python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy

1. On github.com, create a new public repository, for example `cost-of-capital`.
2. Choose **Add file > Upload files**, upload `app.py`, `requirements.txt` and `README.md` to the repository root,
   and commit.
3. Go to https://share.streamlit.io and sign in with GitHub.
4. Choose **Create app**, select the repository, branch `main` and main file `app.py`.
5. Under **Advanced settings**, choose Python 3.12, then **Deploy**.
6. When the app loads, check the Inputs and sources tab: every row should read Auto with a date.

## Verify the calculation by hand

1. Open the Inputs and sources tab and note beta, tax rate, yields and the BBB spread.
2. Open Calculation steps. Recompute levered beta, Ke, Kd and WACC in a spreadsheet.
3. For a foreign country, recompute each local rate with the yield-differential formula.
4. With United States selected, the two currency columns must be identical.
5. Switch the tax jurisdiction and confirm both columns change.