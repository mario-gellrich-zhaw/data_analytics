# String Processing, Regex & Pivot Tables

This exercise is designed to build and enhance your skills in data manipulation, string processing,
regular expressions (regex), and pivot tables using the pandas and numpy libraries in Python.

> [!IMPORTANT]
> Several sub-tasks below ask you to report the *actual* values you computed
> (not example numbers). These can only be answered correctly by running the
> code yourself on the real CSV — pasting a generic AI-generated answer will
> not give you the right numbers, since the regex has to match this file's
> actual text.

---

## Tasks

### Task 1 — Working with strings
- **a)** Read the cars_autoscout24.csv from this week (extended version) to a pandas data frame.
- **b)** Create a new variable `Str_len` containing the length of each string in the variable `Description`.
- **c)** Create a new variable `Description_upper` from the variable `Description` containing only uppercase letters.
- **d)** Remove all leading and trailing empty spaces in `Description_upper`.

### Task 2 — Regular expressions (regex)
- **a)** Extract the price as numerical value (see last week) and store it in a new variable `Price_numeric`.
- **b)** Extract the original price (germ.: Neupreis) from `Description_upper` and store it in a variable `Price_original`.
- **c)** Create a new binary variable `Occasion` with a value of `1` if car type (germ.: Fahrzeugart) is Occasion and a value of `0` otherwise.

### Task 3 — Working with pivot tables
- **a)** Create a subset of the data frame with all missing values removed.
- **b)** Create a pivot table with:
  - `Occasion` as index variable,
  - `Price_numeric` and `Price_original` as values
  - `np.mean` (i.e. mean from the numpy library) as the aggregation function.
- **c)** Report the actual mean `Price_numeric` and `Price_original` values your
  pivot table shows for Occasion = 0 and Occasion = 1.
