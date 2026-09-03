#----------------------------------------------------
# Import the required libraries
#----------------------------------------------------
import joblib
import pandas as pd
from sklearn.metrics import accuracy_score
from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import train_test_split
from sklearn.neighbors import KNeighborsClassifier

#----------------------------------------------------
# Import data
#----------------------------------------------------
df = pd.read_csv("iris.csv")
print('\nData set')
print(df.head())

#----------------------------------------------------
# Create X-matrix and y-variable
#----------------------------------------------------

# Feature matrix
X = df.iloc[:, :-1].values
# print(X.shape)
print('\nFeature matrix')
print(X[:3])

# Target veriable
y = df.iloc[:, -1]
# print(y.shape)
print('\nTarget veriable')
print(y[:6])

# Label encoder
encoder = LabelEncoder()
y = encoder.fit_transform(y)
joblib.dump(encoder, "saved_models/02.iris_label_encoder.pkl")

#----------------------------------------------------
# Create train/test data
#----------------------------------------------------
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.33, random_state=42)

#----------------------------------------------------
# Train the model
#----------------------------------------------------

classifier = KNeighborsClassifier(n_neighbors=5, metric='minkowski', p=2)
classifier.fit(X_train, y_train)

#----------------------------------------------------
# Test the model
#----------------------------------------------------
y_pred = classifier.predict(X_test)
accuracy = accuracy_score(y_true=y_test, y_pred=y_pred)
print("\nAccuracy: % {:10.2f}".format(accuracy * 100))

#----------------------------------------------------
# Save the model
#----------------------------------------------------
joblib.dump(classifier, "saved_models/01.knn_with_iris_dataset.pkl")

#----------------------------------------------------
# Read the saved model and make predictions
#----------------------------------------------------
# Read the saved model
classifier_loaded = joblib.load("saved_models/01.knn_with_iris_dataset.pkl")
encoder_loaded = joblib.load("saved_models/02.iris_label_encoder.pkl")

# New data for predictions
X_manual_test = [[4.0, 4.0, 4.0, 4.0]]
print("\nX_manual_test", X_manual_test)

prediction_raw = classifier_loaded.predict(X_manual_test)
print("\nprediction_raw", prediction_raw)

prediction_real = encoder_loaded.inverse_transform(classifier.predict(X_manual_test))
print("\nReal prediction", prediction_real)
