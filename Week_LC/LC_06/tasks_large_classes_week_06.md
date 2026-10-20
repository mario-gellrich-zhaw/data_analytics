# Data Simulation, Boxplots & ANOVA

This exercise is designed to help you practice data simulation, visualization using
boxplots, and statistical analysis using ANOVA.

> [!IMPORTANT]
> Use a random seed derived from your own birthday (e.g. `np.random.seed(day + month)`
> where day/month come from your birth date) so your simulated data — and therefore
> your results — are unique to you. Report the actual F-statistic, p-value, and your
> conclusion based on *your own* simulated numbers, not example values.

---

## Tasks

### Task 1 — Simulate smartphone data
Simulate data (dataframe with three columns) with smartphone brands, type and prices.
The data must contain three different smartphone brands and realistic prices per brand.

### Task 2 — Visualize prices with grouped boxplots
Create a graphic with grouped boxplots to show how prices vary per brand.

### Task 3 — Analysis of Variance (ANOVA)
Use Analysis of Variance (ANOVA) to investigate whether prices differ between groups.
- Define a statistical hypothesis and a suitable significance level alpha (α)
- Report the actual F-statistic and p-value you obtained, and state your conclusion

### Task 4 — Simulate richer data with the Faker library
Open `smartphone_simulator.ipynb`. It uses [Faker](https://faker.readthedocs.io) (`pip install faker`) to
simulate smartphone sales with brand, model, storage, shop city (Swiss locale `de_CH`), date and payment method,
and NumPy for the prices.
- **a)** Run the notebook with your own birthday as seed. Compare the code with your solution of Task 1: what does
  Faker do for you, and what still has to come from your own domain knowledge?
- **b)** Add a fourth brand with its models and prices to `catalog`, and change the market shares so the brands
  are not equally frequent (hint: `fake.random_element` with an `OrderedDict` of weights).
- **c)** Repeat the boxplot and the ANOVA. Then compare the prices between storage sizes instead of brands.
