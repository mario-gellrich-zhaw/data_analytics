# Chi-Squared Test & Correlation Analysis

The purpose of this exercise is to practise hypothesis testing with the Chi-squared test and correlation analysis
with Spearman's rank correlation, using real data: a short survey on how you and your classmates commute to ZHAW.
All answers are collected in one central data set, which we analyse together with Python.

> [!IMPORTANT]
> Report the actual test statistics you compute from the class data (chi-squared statistic, p-value,
> Spearman's rho, p-value), not example numbers.

---

## Tasks

### Task 1 — Take part in the survey
- **a)** Start the survey app in your Codespace:
  ```bash
  cd Week_LC/LC_07/zhaw_commute_survey
  python app.py
  ```
  The app prints its address. Open it in the browser:
  `https://<your-codespace-name>-5000.app.github.dev/`
  (also listed in the **Ports** tab of VS Code; outside Codespaces: `http://127.0.0.1:5000/`).
- **b)** Fill in the questionnaire (once per person, about 3 minutes).
- **c)** Look at the class results under `/results` and explore the raw data with SQL under `/data`.

### Task 2 — Chi-squared test of independence
Use the notebook `zhaw_commute_survey/analysis/class_survey_analysis.ipynb`. It reads the class data directly from the central data set.

- **a)** Define a null- and alternative hypothesis for the question: *Is the living situation associated with the means of transport?*
- **b)** Define a significance-level alpha for the Chi-squared test.
- **c)** Perform a Chi-squared test with Python and check whether all expected frequencies are at least 5.
- **d)** If they are not, merge categories into a 2 × 2 table and repeat the test.
- **e)** Interpret the test results, referencing the actual chi-squared statistic and p-value you computed.

### Task 3 — Correlation analysis (Pearson & Spearman)
- **a)** Create a scatter plot of the distance and the door-to-door travel time.
- **b)** Pearson: calculate Pearson's correlation coefficient (r) with its p-value for *distance vs. travel time* (two numerical variables).
- **c)** Spearman: calculate Spearman's rank correlation coefficient (rho) with its p-value for the two Likert items (1-5) *satisfied vs. stressful*. First state the direction you expect.
- **d)** Also calculate Spearman's rho for *distance vs. travel time* and compare it with Pearson's r from b). Why is Pearson not suitable for the Likert items in c)?
- **e)** Interpret the results, referencing the actual r, rho and p-values you computed.
