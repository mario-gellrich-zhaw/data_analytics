# Run this app with `python app.py` and
# visit http://127.0.0.1:8050/ in your web browser.

from dash import Dash, dcc, html
import plotly.express as px
import pandas as pd

app = Dash(__name__)

colors = {
    'background': '#000000',
    'text': '#FFCC00'
}

# Assume you have a "long-form" data frame
# See https://plotly.com/python/px-arguments/ for more options
df = pd.DataFrame({
    "Fruit": ["Apples", "Oranges", "Bananas", "Apples", "Oranges", "Bananas"],
    "Amount": [4, 1, 2, 2, 4, 5],
    "City": ["Berlin", "Berlin", "Berlin", "Zürich", "Zürich", "Zürich"]
})

fig = px.bar(df, 
             x="Fruit", 
             y="Amount", 
             color="City", 
             barmode="stack",
             color_discrete_map={
                     'Berlin': '#99cc00',
                      'Zürich': '#ff9900'})

fig.update_layout(
    plot_bgcolor=colors['background'],
    paper_bgcolor=colors['background'],
    font_color=colors['text']
)

app.layout = html.Div(style={'backgroundColor': colors['background']}, children=[
    html.H1(
        children='Fruit store',
        style={
            'textAlign': 'center',
            'color': colors['text']
        }
    ),
    
    dcc.Graph(
        id='example-graph-2',
        figure=fig
    )
])

if __name__ == '__main__':
    app.run_server(debug=True)
    
    
    
    
    
    