# Regression Analysis: Linear & Random Forest

This exercise is designed to teach you key concepts and techniques in predictive modeling,
specifically focusing on multiple linear regression and random forest regression. You'll
learn how to preprocess data, build and evaluate regression models, and interpret the results.

> [!IMPORTANT]
> Report the actual goodness-of-fit values (e.g. R², RMSE) you obtain for each model,
> and describe what your actual residual plots look like in your own words — these
> depend on your specific train/test split and cannot be guessed by an AI without
> running the code against this dataset.

---

## Tasks

### Task 1 — Prepare the data
- Import the car data set `autoscout24_data_prepared.csv`
- Create a subset of cars with fuel type `Benzin` and `Diesel`
- Perform one-hot encoding of the categorical variable `Fuel_Type`
- Create train and test samples (80% train, 20% test)

### Task 2 — Multiple linear regression
Fit a multiple linear regression model with:

| Role | Variable |
|---|---|
| Target | `Price_num` |
| Explanatory | `Mileage_num` |
| Explanatory | `HP_num` |
| Explanatory | `Fuel_Type` |

- Evaluate the model using suitable goodness-of-fit measures. Report the actual values.
- Check model residuals graphically and describe what you observe.

### Task 3 — Random forest regression
Fit a random forest regression model with the same target and explanatory variables as above:

| Role | Variable |
|---|---|
| Target | `Price_num` |
| Explanatory | `Mileage_num` |
| Explanatory | `HP_num` |
| Explanatory | `Fuel_Type` |

- Evaluate the model using suitable goodness-of-fit measures. Report the actual values.
- Check model residuals graphically and describe what you observe.
- Evaluate feature importance and report which feature came out on top for your run.
