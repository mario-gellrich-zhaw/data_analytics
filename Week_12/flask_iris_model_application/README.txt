#------------------------------------------------------------------------------------
# This is a simple iris flower classification model deployment project as flask app
#------------------------------------------------------------------------------------

#------------------------------------------------------------------------------------
# Activate your conda environment
#------------------------------------------------------------------------------------

conda activate daenv

#------------------------------------------------------------------------------------
# Navigate to your folder 'flask_iris_model_example'
#------------------------------------------------------------------------------------

# e.g.:
cd U:/Lektionen/DA_HS2022/KK/Week_12/flask_iris_model_example

#------------------------------------------------------------------------------------
# Run the knn model to create and save a machine learning model
#------------------------------------------------------------------------------------

python model.py

#------------------------------------------------------------------------------------
# Run the flask application
#------------------------------------------------------------------------------------

python app.py

#------------------------------------------------------------------------------------
# Access the web aplication in a web browser by using the following url
#------------------------------------------------------------------------------------

http://localhost:8080

# Now, inlude some values to predict the corresponding Iris species
# Remember that the values refer to the:
# sepal.length 
# sepal.width
# petal.length
# petal.width
# in cm of the iris flowers. 
# For example, you can use: 5, 4, 2, 1



