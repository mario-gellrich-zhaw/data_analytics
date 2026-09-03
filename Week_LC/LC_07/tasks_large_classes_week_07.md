# Chi-Squared Test & Correlation Analysis

The purpose of this exercise is to develop and practice statistical analysis skills, specifically in the context
of hypothesis testing using the Chi-squared test and correlation analysis using the Pearson correlation
coefficient. The tasks are designed to familiarize you with essential concepts in categorical and numerical
data analysis, as well as the use of Python for performing these analyses.

> [!IMPORTANT]
> Report the actual test statistics you compute (chi-squared statistic, p-value,
> Pearson r, p-value) — not example numbers. For Task 2, simulate your data with
> a random seed derived from your own birthday so your correlation results are
> unique to you.

---

## Tasks

### Task 1 — Chi-squared test of independence
The following contingency table shows the number of men and women buying different types of pets:

| | dog | cat | bird |
|---|---|---|---|
| **men** | 207 | 282 | 241 |
| **women** | 234 | 242 | 232 |

- **a)** Define a null- and alternative hypothesis for a Chi-squared test.
- **b)** Define a significance-level alpha for the Chi-squared test.
- **c)** Perform a Chi-squared test with Python to test whether the two categorical variables (gender & type of pets) are independent.
- **d)** Interpret the test results, referencing the actual chi-squared statistic and p-value you computed.

### Task 2 — Correlation analysis
- **a)** Simulate numerical data (use a seed based on your birthday, e.g. `np.random.seed(day * month)`)
- **b)** Calculate a Pearson correlation coefficient (r)
- **c)** Provide a p-value for the Pearson correlation coefficient (r)
