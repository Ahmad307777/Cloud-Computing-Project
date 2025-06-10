from flask import Flask, render_template, request, redirect, url_for
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
import os
import json
import datetime
import uuid

app = Flask(__name__)

# Azure Blob Storage configuration
# Set AZURE_STORAGE_CONNECTION_STRING in your environment for production security
connect_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "DefaultEndpointsProtocol=https;AccountName=podcaststorage124;AccountKey=U45dnD2IEuN2UrmDhsitzl1QS8W/biPBXY0kHyOjuV1mVx41Mda6ZcMFc4RIgDVtQhL5TgxlBUiv+AStgOCT9g==;EndpointSuffix=core.windows.net")
container_name = "podcasts"
blob_service_client = BlobServiceClient.from_connection_string(connect_str)
container_client = blob_service_client.get_container_client(container_name)

def generate_sas_url(blob_name, expiry_hours=1):
    sas_token = generate_blob_sas(
        account_name=blob_service_client.account_name,
        container_name=container_name,
        blob_name=blob_name,
        account_key=blob_service_client.credential.account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.datetime.utcnow() + datetime.timedelta(hours=expiry_hours)
    )
    url = f"https://{blob_service_client.account_name}.blob.core.windows.net/{container_name}/{blob_name}?{sas_token}"
    return url

@app.route('/')
def index():
    podcasts = [
        {'title': 'Podcast 1', 'description': 'Description 1'},
        {'title': 'Podcast 2', 'description': 'Description 2'},
        {'title': 'Podcast 3', 'description': 'Description 3'},
    ]
    return render_template('index.html', podcasts=podcasts)


@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        title = request.form['title']
        description = request.form['description']
        file = request.files['file']
        if file:
            filename = file.filename
            # Upload audio file
            blob_client = container_client.get_blob_client(filename)
            from azure.storage.blob import ContentSettings
            blob_client.upload_blob(
                file,
                overwrite=True,
                content_settings=ContentSettings(content_type='audio/mpeg')
            )
            # Upload metadata JSON
            meta = {'title': title, 'description': description}
            meta_blob = container_client.get_blob_client(f'metadata/{filename}.json')
            meta_blob.upload_blob(json.dumps(meta), overwrite=True)
            return redirect(url_for('list_podcasts'))
    return render_template('upload.html')

@app.route('/podcasts')
def list_podcasts():
    return render_template('podcasts.html')

@app.route('/podcasts/listen')
def listen_podcasts():
    blob_list = container_client.list_blobs()
    podcasts = []
    for blob in blob_list:
        if '/' not in blob.name:  # Skip metadata, feedback, logs folders
            meta_blob = container_client.get_blob_client(f'metadata/{blob.name}.json')
            try:
                meta_data = json.loads(meta_blob.download_blob().readall())
            except Exception:
                meta_data = {"title": blob.name, "description": ""}
            podcasts.append({
                "filename": blob.name,
                "title": meta_data.get("title", blob.name),
                "description": meta_data.get("description", "")
            })
    return render_template('listen.html', podcasts=podcasts)

@app.route('/podcast/<filename>', methods=['GET'])
def podcast_detail(filename):
    meta_blob = container_client.get_blob_client(f'metadata/{filename}.json')
    try:
        meta_data = json.loads(meta_blob.download_blob().readall())
    except Exception:
        meta_data = {"title": filename, "description": ""}
    # Get feedbacks for this podcast
    feedback_blobs = container_client.walk_blobs(name_starts_with=f"feedback/{filename}_")
    feedback_list = []
    ratings = []
    for blob in feedback_blobs:
        blob_client = container_client.get_blob_client(blob.name)
        content = blob_client.download_blob().readall().decode('utf-8')
        try:
            fb_obj = json.loads(content)
            feedback_list.append(fb_obj)
            if 'rating' in fb_obj and isinstance(fb_obj['rating'], int):
                ratings.append(fb_obj['rating'])
        except Exception:
            feedback_list.append({'feedback': content, 'rating': None})
    avg_rating = round(sum(ratings)/len(ratings), 2) if ratings else None
    # Get listen count
    listen_count = get_listen_count(filename)
    return render_template('podcast_detail.html', filename=filename, title=meta_data.get('title', filename), description=meta_data.get('description', ''), feedbacks=feedback_list, avg_rating=avg_rating, listen_count=listen_count)

@app.route('/podcast/<filename>/feedback', methods=['POST'])
def podcast_feedback(filename):
    feedback_text = request.form['feedback']
    rating = int(request.form.get('rating', 0))
    unique_id = str(uuid.uuid4())
    feedback_obj = {'feedback': feedback_text, 'rating': rating}
    blob_client = container_client.get_blob_client(f'feedback/{filename}_{unique_id}.txt')
    blob_client.upload_blob(json.dumps(feedback_obj), overwrite=True)
    return redirect(url_for('podcast_detail', filename=filename))

@app.route('/stream/<filename>')
def stream(filename):
    # Log podcast access (append if exists, otherwise create)
    log_blob = container_client.get_blob_client(f'logs/{filename}_views.txt')
    try:
        # Try reading existing log and appending new timestamp
        existing_log = log_blob.download_blob().readall().decode()
        new_log = f"{existing_log}{datetime.datetime.utcnow()}\n"
        log_blob.upload_blob(new_log, overwrite=True)
    except Exception:
        # If it doesn't exist, create new
        log_blob.upload_blob(f"{datetime.datetime.utcnow()}\n", overwrite=True)

    # Redirect to streamable SAS URL
    sas_url = generate_sas_url(filename)
    return redirect(sas_url)


@app.route('/feedback', methods=['GET', 'POST'])
def feedback():
    if request.method == 'POST':
        feedback_text = request.form['feedback']
        rating = int(request.form.get('rating', 0))
        unique_id = str(uuid.uuid4())
        feedback_obj = {'feedback': feedback_text, 'rating': rating}
        blob_client = container_client.get_blob_client(f'feedback/{unique_id}.txt')
        blob_client.upload_blob(json.dumps(feedback_obj), overwrite=True)
        return redirect(url_for('index'))
    return render_template('feedback.html')

@app.route('/feedbacks')
def view_feedbacks():
    feedback_blobs = container_client.walk_blobs(name_starts_with="feedback/")
    feedback_list = []
    for blob in feedback_blobs:
        blob_client = container_client.get_blob_client(blob.name)
        content = blob_client.download_blob().readall().decode('utf-8')
        try:
            fb_obj = json.loads(content)
            feedback_list.append(fb_obj)
        except Exception:
            feedback_list.append({'feedback': content, 'rating': None})
    return render_template('view_feedbacks.html', feedbacks=feedback_list)

def get_listen_count(filename):
    log_blob = container_client.get_blob_client(f'logs/{filename}_views.txt')
    try:
        log_data = log_blob.download_blob().readall().decode('utf-8')
        return len([line for line in log_data.split('\n') if line.strip()])
    except Exception:
        return 0

@app.route('/analytics')
def analytics():
    blob_list = container_client.list_blobs()
    stats = []
    for blob in blob_list:
        if '/' not in blob.name:  # Only podcast files
            meta_blob = container_client.get_blob_client(f'metadata/{blob.name}.json')
            try:
                meta_data = json.loads(meta_blob.download_blob().readall())
                title = meta_data.get('title', blob.name)
            except Exception:
                title = blob.name
            listen_count = get_listen_count(blob.name)
            stats.append({'filename': blob.name, 'title': title, 'listen_count': listen_count})
    stats = sorted(stats, key=lambda x: x['listen_count'], reverse=True)
    return render_template('analytics.html', analytics=stats)

if __name__ == '__main__':
    app.run(debug=True)
