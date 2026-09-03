from flask import Flask, render_template
import fear_and_greed
import plotly.graph_objs as go

app = Flask(__name__)

# Data
data = fear_and_greed.get()
value_example = data.value

# Function to create a gauge chart with numbers
def create_simple_gauge_chart(value, max_value=100):
    fig = go.Figure()

    fig.add_trace(go.Indicator(
        mode="number+gauge",
        value=value,
        title={'text': ""},
        domain={'x': [0, 1], 'y': [0, 1]},
        gauge={'axis': {'range': [None, max_value]},
               'bar': {'color': "#ADFF2F"},
               'bgcolor': "#121212",
               'steps': [
                   {'range': [0, max_value], 'color': "#121212"}],
               'threshold': {
                   'line': {'color': "#d63513", 'width': 8},
                   'thickness': 0.75,
                   'value': value}}
    ))

    fig.update_layout(
        paper_bgcolor="#121212",
        font={'color': "#ADFF2F"},
        margin=dict(l=10, r=10, t=10, b=10)
    )
    return fig

# Flask route to display the chart
@app.route('/')
def index():
    fig = create_simple_gauge_chart(value_example)
    chart = fig.to_html(full_html=False)
    return render_template('chart.html', chart=chart)

if __name__ == '__main__':
    app.run(debug=True)