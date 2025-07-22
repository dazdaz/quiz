# Deployment Instructions for Google Cloud Run

## 1. Setup Google Cloud Project:

* Create a Google Cloud project.
* Enable the Google Docs API in the API Library

```
 gcloud services enable docs.googleapis.com drive.googleapis.com
```

* Create a service account in IAM & Admin > Service accounts

```
PROJECT=myproject
SERVICE_ACCOUNT_ID=quiz-reader
DISPLAY_NAME="read gdocs"

gcloud iam service-accounts create ${SERVICE_ACCOUNT_ID} \
    --display-name="${DISPLAY_NAME}" \
    --project=${PROJECT}

gcloud iam service-accounts keys create credentials.json \
  --iam-account quiz-reader@${PROJECT}.iam.gserviceaccount.com
```

* Grant the service account "Viewer" access to your Google Doc by sharing the doc with the service account's email (found in the JSON key).

## 2. Prepare Files:

* Save the provided code as `app.py`.
* Create `requirements.txt` with the following content:

    ```
    flask
    google-api-python-client
    google-auth
    ```

```
  uv venv
  source .venv/bin/activate
  uv pip install -r requirements.txt
```

## 3. Deploy to Cloud Run:

* Install Google Cloud CLI if not already.
* Run the following command:

    ```bash
    gcloud run deploy quiz-app --source . --service-account "quiz-app-sa@[YOUR_PROJECT_ID].iam.gserviceaccount.com" --region us-central1 --no-invoker-iam-check
    ```

    **Note:** Include `credentials.json` in your directory (but be careful with secrets; better to use Secret Manager in production).

* Cloud Run will build and deploy the container. Access the provided URL.

## 4. Usage:

* Visit the deployed URL, enter a Google Doc ID with the quiz content formatted as specified, and take the quiz.

---

This implements the PRD requirements. For production, improve security (e.g., use Cloud Secret Manager for credentials) and add error handling/UI polish.

