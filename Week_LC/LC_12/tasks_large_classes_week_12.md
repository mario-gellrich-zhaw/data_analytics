This exercise is designed to guide you through the process of creating a simple web application 
using Python and Flask, while also introducing you to the concept of retrieving and visualizing 
financial sentiment data. The focus is on building a functional web app that dynamically displays 
the Fear & Greed Index, a popular financial market indicator.

> Take a screenshot of your running app showing today's actual Fear & Greed value and
> gauge chart — the value changes daily and the app must actually run locally to
> produce it, so a generic AI answer cannot substitute for this.

Tasks: 

1.) Create a web application with Python and flask showing the Fear & Greed Index
    (https://edition.cnn.com/markets/fear-and-greed) of the current day.

2.) The data can be obtained using the Python library 'fear-and-greed'

    pip install fear-and-greed
	
	import fear_and_greed
	
    data = fear_and_greed.get()
    data.value
	
3.) The web application must contain a gauge chart with the Fear & Greed Index.
    Report today's actual index value shown in your app.
