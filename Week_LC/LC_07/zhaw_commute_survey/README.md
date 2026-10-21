# ZHAW Commute Survey

A short anonymous questionnaire (about 3 minutes) on how students get to ZHAW. Students run the app in their own Codespace and fill it in. All answers go to **one central Google Sheet**, and the class data is then analysed with a **chi-squared test** and **Spearman correlations**.

## The questionnaire

| Column | Question | Type |
|---|---|---|
| `campus` | Where do you mainly study? | Winterthur / Zürich / Wädenswil |
| `transport` | Main means of transport (longest part of the trip) | Train / Tram or bus / Car or motorbike / Bike or e-bike / On foot |
| `door_to_door_min` | Door to door, one way | minutes |
| `distance_km` | Distance, one way | km |
| `days_per_week` | Days per week at ZHAW | days |
| `monthly_cost_chf` | Money spent per month to come to ZHAW | CHF |
| `travel_pass` | Public transport pass | GA travelcard / Regional pass (e.g. ZVV) / Half Fare travelcard only / No pass |
| `satisfied` | I am satisfied with my commute to ZHAW. | Likert 1-5 |
| `stressful` | My commute is stressful. | Likert 1-5 |
| `productive` | I can use my travel time productively. | Likert 1-5 |
| `gender` | What is your gender? | Female / Male |
| `age` | How old are you? | years |
| `living` | Where do you live? | Own flat / Shared apartment / Other |

The questions are defined in `questions.py`. If you change a question key or an option, also update `TEXT_COLUMNS` / `NUMBER_COLUMNS` / `COLUMNS` in `google_apps_script/Code.gs` and redeploy the script.

## Where the data is stored

All responses are rows of the tab `responses` in a Google Sheet owned by the lecturer. A small Apps Script attached to the sheet (`google_apps_script/Code.gs`) is published as a web app with access for **anyone**. Its link is the only thing the app and the students need: no Google account and no sharing rights.

| Request | Result |
|---|---|
| `POST <link>` with the answers as JSON | appends one row (done by the app) |
| `GET <link>` | all responses as JSON (used by the app) |
| `GET <link>?format=csv` | all responses as CSV (used by the notebooks, e.g. `pd.read_csv(link + '?format=csv')`) |

The link is set in `CENTRAL_SHEET_URL` in `app.py` (or the environment variable `SHEET_URL`). If it is empty, the app stores the responses in the local file `responses.sqlite` instead.

Anyone who has the link can add and read responses. That is fine for anonymous class data, but do not add questions that collect personal data.

## Setup on the Google side (lecturer, once)

1. Create a Google Sheet with a private Google account (e.g. *ZHAW commute survey*).
2. In the sheet, go to **Extensions → Apps Script**. Replace the sample code with `google_apps_script/Code.gs` and save.
3. Select `doGet` in the function dropdown and click **Run**. Allow the permissions; on *"Google hasn't verified this app"*, click **Advanced → Go to … (unsafe)**, because it is your own script. A tab `responses` with the column headers appears in the sheet.
4. **Deploy → New deployment → Web app**, with *Execute as:* **Me** and *Who has access:* **Anyone**. Copy the web app link (ends with `/exec`).
5. Put the link into `CENTRAL_SHEET_URL` in `app.py` and into `SURVEY_URL` in the analysis notebooks.

Later changes to the script: use **Deploy → Manage deployments → ✏️ → Version: New version → Deploy**. This keeps the link. ("New deployment" would create a new link.)
To close the survey, set `SURVEY_OPEN = false` in the script and deploy a new version.
Do not rename the `responses` tab or change its header row; adding other tabs (charts, pivot tables) is fine.

## For students: run the app in Codespaces

```bash
cd Week_LC/LC_07/zhaw_commute_survey
python app.py
```

The app only needs Flask, which is already installed in the course Codespace.

Codespaces offers to open port 5000 in the browser. Then:

| Page | |
|---|---|
| `/` | Fill in the questionnaire (once per person) |
| `/results` | Charts of all responses so far |
| `/data` | Read-only SQL console on the table `responses`, e.g. to build a contingency table with `GROUP BY` |
| `/export.sqlite`, `/export.csv` | All responses as a SQLite database file or as CSV |

The SQL console runs each query on an in-memory SQLite copy of the responses. Only `SELECT` statements are allowed, queries stop after 2 seconds and show at most 500 rows.

## Analysis (`analysis/`)

| Notebook | |
|---|---|
| `simulate_responses.ipynb` | Simulates 200 responses in the same format (`zhaw_commute_responses.csv`) to practise before the real data exists |
| `zhaw_commute_analysis.ipynb` | Exercise: chi-squared test (living situation vs. transport, collapsed Likert item vs. transport, expected frequencies, Cramér's V) and Spearman correlation (distance, travel time, costs, satisfaction; Spearman vs. Pearson) |
| `solution_zhaw_commute_analysis.ipynb` | Solution |

The notebooks read the class data directly from the sheet link; set `use_class_data = False` to use the simulated data instead.

## Tests

```bash
python -m pytest tests
```
