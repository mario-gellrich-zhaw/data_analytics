This exercise is designed to teach you key concepts and techniques in predictive modeling, 
specifically focusing on multiple linear regression and random forest regression. You'll 
learn how to preprocess data, build and evaluate regression models, and interpret the results. 

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
    
	Evaluate the model using suitable goodness-of-fit measures.
	Check model residuals graphically.

3.) Fit a random forest regression model with:
    'Price_num' = target variable
	'Mileage_num' = explanatory variable
	'HP_num' = explanatory variable
    'Fuel_Type' =  explanatory variable

	Evaluate the model using suitable goodness-of-fit measures.
	Check model residuals graphically.
	Evaluate feature importance.