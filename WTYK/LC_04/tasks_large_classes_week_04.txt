This exercise is designed to help you learn how to retrieve and analyze data using Python. It covers 
key concepts in data retrieval, non-graphical Exploratory Data Analysis (EDA), and graphical EDA. 

Tasks:

1.) Get data for the stock TSLA (Tesla) from Yahoo Finance (daily data from 2022-01-01 to today)
    --> Hint: you must install a new library 'yfinance'. For this, open a Terminal and write:
	    
        pip install yfinance
		
	--> To get the data and as a starting point, use the Python code:
	
		# Python libraries
		import pandas as pd
		import yfinance as yf
		from datetime import datetime
		import matplotlib.pyplot as plt

		# Get the data
		today = datetime.now().strftime("%Y-%m-%d")
		df = yf.download('TSLA', start = "2022-01-01", end = today, progress=False)
		df
        	
2.) Apply non-graphical Exploratory Data Analysis (EDA) methods to answer the following questions:
 a) What are the values for min, max. mean and median of the close price?
 b) What is the 10% Quantile and 90% quantile of close price?

3.) Apply the following graphical Exploratory Data Analysis (EDA):
 a) plot a line chart of the close price
 b) plot a histogram of the close price
 c) plot a boxplot of the close price