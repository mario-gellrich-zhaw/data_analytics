import os
from flask import Flask, request, render_template, send_from_directory

from PIL import Image
from PIL.ExifTags import TAGS

app = Flask(__name__)

# Define the upload folder and allowed extensions
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'jpeg', 'jpg'}

# Configure the app to use the upload folder
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Function to check if a file has an allowed extension
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Function to extract and format image metadata
def extract_metadata(image_path):
    image = Image.open(image_path)
    metadata = {}

    if hasattr(image, '_getexif'):
        exif_data = image._getexif()
        if exif_data is not None:
            for tag, value in exif_data.items():
                tag_name = TAGS.get(tag, tag)
                metadata[tag_name] = value

    # Get image size (width x height)
    width, height = image.size
    metadata["Image Size"] = f"{width} x {height} pixels"

    return metadata

# Route to the main page
@app.route('/')
def index():
    return render_template('upload.html')

# Route to handle file upload and display metadata
@app.route('/upload', methods=['POST'])
def upload_file():
    # Check if a file was submitted
    if 'file' not in request.files:
        return "No file part"

    file = request.files['file']

    # Check if the file has a valid extension
    if file and allowed_file(file.filename):
        # Save the file to the upload folder
        filename = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        file.save(filename)
        
        # Extract metadata from the uploaded image
        metadata = extract_metadata(filename)
        
        # Display the uploaded image and metadata
        return f"<h2>Uploaded Image:</h2><img src='/uploads/{file.filename}' alt='uploaded_image'><h2>Metadata:</h2><pre>{metadata}</pre>"
    else:
        return "Invalid file format. Please upload a .jpeg file."

# Route to serve uploaded files
@app.route('/uploads/<filename>')
def serve_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    app.run(debug=True)
