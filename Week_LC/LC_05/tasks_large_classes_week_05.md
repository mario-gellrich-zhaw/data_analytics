# Geographic Information Systems (GIS) & 3D Visualization

This exercise is focused on developing skills in geographic information systems (GIS)
and 3D visualization using Google Earth Pro and external tools like OpenRouteService.

---

## Tasks

### Task 1 — Install Google Earth Pro Desktop on your computer

### Task 2 — Open the cube.kml example from Moodle in Google Earth Pro
> Materials for exercises → Exercises in small classes → Geodata → cube.kml

### Task 3 — Create a route using openrouteservice
Create a route (with start address ... destination address) using [openrouteservice](https://maps.openrouteservice.org/#/place/@6.1962890625,53.291489065300226,5).

- **a)** Save the route as a `.kml` file
- **b)** Open the route in Google Earth Pro
- **c)** Format the route in Google Earth Pro (color, thickness, height above ground)
- **d)** Create a tour and drive the route virtually (camera angle 80 degrees, speed 400 km/h)

### Task 4 — Textured 3D building model
Open `MackyBldg.kmz` in Google Earth Pro. It contains a 3D model of Macky Auditorium (University of Colorado,
Boulder) that follows the exact shape of the building, with photographs of the facades as textures. Example from
[Google's KML documentation on models](https://developers.google.com/kml/documentation/models).

- **a)** Fly around the building and compare the model with the 3D buildings and aerial imagery of Google Earth.
- **b)** Compare the model with `cube.kml`: what is the difference between an extruded `Polygon` and a `Model`?
- **c)** Unzip the `.kmz` (rename it to `.zip` if needed) and look at its contents:
  - `doc.kml`: how are the position (`Location`), `Orientation` and `Scale` of the model defined?
  - `files/CU Macky.dae`: the 3D geometry in COLLADA format. Find where the texture images are referenced.
  - `files/*.jpg`: the facade photographs used as textures.
- **d)** In `doc.kml`, change `heading` and `Scale`, zip the files again as `.kmz` (with `doc.kml` at the top level)
  and open it again. What changes?
- **e)** Optional: replace one of the texture images with your own photo and look at the result.
