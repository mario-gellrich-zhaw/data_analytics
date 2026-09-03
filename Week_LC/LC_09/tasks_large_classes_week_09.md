# Classification Models: Decision Trees & Random Forests

This exercise is designed to provide a comprehensive introduction to classification modeling,
data simulation, and model evaluation using decision trees and random forests. The tasks will
help you understand how to generate synthetic data, fit and visualize classification models,
and evaluate their performance using cross-validation. Additionally, you will learn how to
interpret and visualize feature importance, which is crucial for understanding the contributions
of different features to the model's predictions.

> [!IMPORTANT]
> Using AI to help generate the synthetic data (Task 1) is fine and expected. But use
> a random seed derived from your own birthday so your dataset is unique to you, and
> report the actual cross-validation scores, tree vs. forest accuracy, and top feature
> you observe for *your* data — a generic AI answer cannot know these without running
> your exact data.

---

## Tasks

### Task 1 — Generate synthetic pet data
Generate a data frame with 4000 pets with:
- A nominal target variable with 4 classes: `dog`, `cat`, `fish`, `bird`
- Five features: age, color, weight, height, eats meat (yes/no)
- Ask ChatGPT for help to generate the data, but set a random seed based on your
  own birthday (e.g. `np.random.seed(day + month)`) so the data is unique to you

### Task 2 — Train/test split
Create train and test data (80% train, 20% test).

### Task 3 — Classification tree
Fit a classification tree model and visualize the model.

### Task 4 — Cross-validation
Use cross-validation to evaluate the model performance. Report the actual scores you got.

### Task 5 — Random forest classifier
Fit a random forest classification model. Report its actual accuracy and compare it
to the tree model's accuracy, in your own words.

### Task 6 — Feature importance
Show the feature importance in a bar chart. State which feature ranked highest for your data.
