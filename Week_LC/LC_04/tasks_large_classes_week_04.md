# Stock Market Data & Exploratory Data Analysis (EDA)

This exercise is designed to help you learn how to retrieve and analyze data using Python. It covers
key concepts in data retrieval, non-graphical Exploratory Data Analysis (EDA), and graphical EDA.

> [!IMPORTANT]
> Everyone in class is pointed at TSLA below, so a single AI-generated answer
> copied by the whole class would look identical. To avoid that, Tasks 2 and 3
> must be repeated for a *second* stock ticker of your own choice (any company
> you're personally interested in) — report the actual numbers you compute for
> both tickers, not example values.

---

## Tasks

### Task 1 — Get data for the stock TSLA (Tesla) from Yahoo Finance
Daily data from 2022-01-01 to today.

Install the `yfinance` library in a terminal:

```bash
pip install yfinance
```

Then, as a starting point, use the following Python code:

```python
# Python libraries
import pandas as pd
import yfinance as yf
from datetime import datetime
import matplotlib.pyplot as plt

# Get the data
today = datetime.now().strftime("%Y-%m-%d")
df = yf.download('TSLA', start="2022-01-01", end=today, progress=False)
df
```

### Task 2 — Non-graphical Exploratory Data Analysis (EDA)
Answer the following questions for TSLA *and* for the second ticker of your choice:
- **a)** What are the values for min, max, mean and median of the close price?
- **b)** What is the 10% Quantile and 90% quantile of close price?

### Task 3 — Graphical Exploratory Data Analysis (EDA)
For TSLA *and* your second ticker:
- **a)** Plot a line chart of the close price
- **b)** Plot a histogram of the close price
- **c)** Plot a boxplot of the close price
