# Import required libraries
import io
import numpy as np

from flask import Blueprint
from flask import render_template
from flask import abort
from flask import current_app
from flask import make_response
    
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
from matplotlib.figure import Figure

client = Blueprint('client', __name__, template_folder='templates', static_url_path='/static')

# Flask route
@client.route('/<int:observations>', methods=['GET'])
def home(observations):
    title = current_app.config['TITLE']
    plot = plot_observations(observations)
    return render_template('index.html', title=title, plot=plot)

# Function to generate the histogram
def plot_observations(observations):
    """Generate a histogram with a varying number of randomly generated data
       Parameter: number of oberservations 
       Returns: Histogram as SVG-Graphic 
    """
    # Randomly generated data for plotting
    data = np.random
    data = np.random.normal(loc=150, scale=5, size=observations)

    # Histogram
    fig = Figure()
    FigureCanvas(fig)
    ax = fig.add_subplot(111)
    ax.hist(data, bins = 50, color='greenyellow')
    ax.set_xlabel('Horsepower of cars', fontsize=15)
    ax.set_ylabel('Frequency (number of cars)', fontsize=15)
    ax.set_title(f'There are {observations} used cars in this histogram!')
    ax.grid(True)

    img = io.StringIO()
    fig.savefig(img, format='svg')
    
    # Clip off the xml headers from the image
    svg_img = '<svg' + img.getvalue().split('<svg')[1]
    
    return svg_img
