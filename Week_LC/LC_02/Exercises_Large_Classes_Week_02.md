# Data Manipulation and Analysis Exercise

This exercise is designed to help you practice essential data manipulation and analysis skills using pandas, a powerful data analysis library in Python. Each step of the exercise targets specific tasks commonly encountered when working with real-world data.

> [!IMPORTANT]
> Several sub-tasks below ask you to report the *actual* values or observations
> from your own run (not example numbers). These can only be answered correctly
> by running the code yourself and looking at the real output — pasting a
> generic AI-generated answer will not give you the right numbers.

---

## Tasks

### Task 1 — Read BigmacPrice.csv to a pandas data frame

### Task 2 — Read ChickenData.xlsx to a pandas data frame
- Create a dictionary from the data frame with breed (keys) and eggs_per_year (values)

### Task 3 — Check separator and encoding of Cars_autoscout24.csv
- Read Cars_autoscout24.csv to a pandas data frame (if `encoding='utf-8'`
  gives an error, try `encoding='latin1'` instead)
- Convert Prices to numeric, store it as Prices_numeric
- Plot a histogram from Prices_numeric
- Report the min, max, and mean of the `Prices_numeric` you actually computed

### Task 4 — Personalized subset analysis
- Take the month of your birthday (i.e. 1-12). If it is even, filter the data
  frame to `Fuel_Type == 'Benzin'`; if it is odd, filter to `Fuel_Type == 'Diesel'`
- Recompute `Prices_numeric`, the histogram, and the mean price for your
  filtered subset only
- Report your last digit, which fuel type it maps to, and the mean price you
  obtained for your subset
