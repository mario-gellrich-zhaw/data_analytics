# Import required libraries
from flask import Flask
from flask import render_template

# Define 'app' object
app = Flask(__name__)

# Simple route 
@app.route('/')
def index():
    return '<h1 style="color:red"> Hello, how are you?</h1>'

# 
@app.route('/hello/<name>')
def hello(name=None):
    return render_template('index.html', name=name)

if __name__ == '__main__':
    app.run(port=5000,debug=True)