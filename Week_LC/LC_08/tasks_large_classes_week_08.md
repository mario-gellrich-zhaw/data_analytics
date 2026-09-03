This exercise is designed to teach you key concepts and techniques in predictive modeling, 
specifically focusing on multiple linear regression and random forest regression. You'll 
learn how to preprocess data, build and evaluate regression models, and interpret the results. 

> Report the actual goodness-of-fit values (e.g. R², RMSE) you obtain for each model,
> and describe what your actual residual plots look like in your own words — these
> depend on your specific train/test split and cannot be guessed by an AI without
> running the code against this dataset.

Tasks:

1.) Import the car data set 'autoscout24_data_prepared.csv'.
    Create a subset of cars with fuel type 'Benzin' and 'Diesel'.
    Perform one-hot encoding of the categorical variable 'Fuel_Type'.
	Create train and test samples (80% train, 20% test).

2.) Fit a multiple linear regression model with:
    'Price_num' = target variable
	'Mileage_num' = explanatory variable
	'HP_num' = explanatory variable
    'Fuel_Type' =  explanatory variable
    
	Evaluate the model using suitable goodness-of-fit measures. Report the actual values.
	Check model residuals graphically and describe what you observe.

3.) Fit a random forest regression model with:
    'Price_num' = target variable
	'Mileage_num' = explanatory variable
	'HP_num' = explanatory variable
    'Fuel_Type' =  explanatory variable

	Evaluate the model using suitable goodness-of-fit measures. Report the actual values.
	Check model residuals graphically and describe what you observe.
	Evaluate feature importance and report which feature came out on top for your run.
