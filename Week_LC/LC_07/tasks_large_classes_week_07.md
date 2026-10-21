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
  and open port 5000 in the browser.
- **b)** Fill in the questionnaire (once per person, about 3 minutes).
- **c)** Look at the class results under `/results` and explore the raw data with SQL under `/data`.

### Task 2 — Chi-squared test of independence
Use the notebook `zhaw_commute_survey/analysis/class_survey_analysis.ipynb`. It reads the class data directly from the central data set.

- **a)** Define a null- and alternative hypothesis for the question: *Is the living situation associated with the means of transport?*
- **b)** Define a significance-level alpha for the Chi-squared test.
- **c)** Perform a Chi-squared test with Python and check whether all expected frequencies are at least 5.
- **d)** If they are not, merge categories into a 2 × 2 table and repeat the test.
- **e)** Interpret the test results, referencing the actual chi-squared statistic and p-value you computed.

### Task 3 — Correlation analysis (Spearman)
- **a)** Create a scatter plot of the distance and the door-to-door travel time.
- **b)** Calculate Spearman's rank correlation coefficient (rho) with its p-value for *travel time vs. satisfaction* and for one other pair of your choice.
- **c)** Explain why Spearman is more suitable than Pearson for this data, and compare both coefficients for one pair.
- **d)** Interpret the results, referencing the actual rho and p-values you computed.
