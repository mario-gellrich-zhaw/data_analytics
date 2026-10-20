# Data Simulation, Boxplots & ANOVA

This exercise is designed to help you practice data simulation, visualization using
boxplots, and statistical analysis using ANOVA.

> [!IMPORTANT]
> In `smartphone_data_simulator.ipynb`, set the seed to your own birthday (`day, month = …`)
> so your simulated data — and therefore your results — are unique to you. Report the actual F-statistic, p-value, and your
> conclusion based on *your own* simulated numbers, not example values.

---

## Tasks

### Task 1 — Simulate smartphone data
Open `smartphone_data_simulator.ipynb`. It uses the [Faker](https://faker.readthedocs.io) library
(`pip install faker`) to simulate smartphone sales with brand, model, storage, shop city (Swiss locale `de_CH`),
date and payment method, and NumPy for the prices. It saves the data as `smartphone_data.csv`.
- **a)** Set your own birthday as seed and run the notebook. What does Faker do for you, and what has to come
  from your own domain knowledge (see `catalog`)?
- **b)** Make the data your own: add a fourth brand with its models and realistic prices to `catalog`, and change
  the market shares so the brands are not equally frequent (hint: `fake.random_element` with an `OrderedDict` of
  weights). Run the notebook again to overwrite `smartphone_data.csv`.

Use `smartphone_prices.ipynb` for Tasks 2 and 3; it reads `smartphone_data.csv`.

### Task 2 — Visualize prices with grouped boxplots
Create a graphic with grouped boxplots to show how prices vary per brand.

### Task 3 — Analysis of Variance (ANOVA)
Use Analysis of Variance (ANOVA) to investigate whether prices differ between groups.
- Define a statistical hypothesis and a suitable significance level alpha (α)
- Report the actual F-statistic and p-value you obtained, and state your conclusion
- Optional: compare the prices between storage sizes (`storage_gb`) instead of brands.
