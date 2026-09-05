# AI Receptionist Deployment & Configuration Guide

This guide provides step-by-step instructions for configuring credentials, running offline verification tests, and deploying the decoupled AI Receptionist webhook to Google Cloud Run or Vertex AI Agent Engine.

---

## 🛠️ Step 1: Google Service Account & Sheets Setup

The receptionist logging and WISMO lookup tools use Google Service Account credentials to read and write to Google Sheets.

### 1. Create a Service Account
1. Go to the **GCP Console** -> **IAM & Admin** -> **Service Accounts**.
2. Click **Create Service Account**, name it (e.g., `sheets-receptionist-sa`), and click **Create**.
3. Do not assign broad project roles; Sheets access is granted at the individual document level. Click **Done**.

### 2. Generate a Service Account Key
1. Click on the created service account, go to the **Keys** tab, and click **Add Key** -> **Create new key**.
2. Select **JSON** and download the file.
3. Rename the file to `credentials.json` and place it in your project root (excluded by `.gitignore`).
4. In your `.env` file, set `GOOGLE_APPLICATION_CREDENTIALS=credentials.json`.

### 3. Share the Google Sheet
1. Create a new Google Sheet for Lead Logging and/or Order Lookups.
2. Click **Share** (top-right) and add the service account email (e.g., `sheets-receptionist-sa@your-project.iam.gserviceaccount.com`) with **Editor** permissions for the leads sheet and **Viewer** permissions for the order lookup sheet.
3. Copy the **Spreadsheet ID** from the URL:
   `https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit`
4. Add the IDs to your `.env` file (`SPREADSHEET_ID` and `WISMO_SPREADSHEET_ID`).

---

## 🧪 Step 2: Local Verification & Test Execution

Run the complete 38-test offline unit test suite before deploying:

```bash
# Run unit tests with code coverage
pytest tests/unit/ -v --cov=app --cov-report=term-missing

# Run automated pre-push validation script
./scripts/pre_push_check.sh
```

---

## 🐳 Step 3: Deploy to Google Cloud Run

Deploy the container to Google Cloud Run to provide a secure HTTPS webhook endpoint for telephony integration.

### 1. Build and Submit Container Image
```bash
# Set your active GCP project
gcloud config set project YOUR_PROJECT_ID

# Build container image with Cloud Build
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/ai-receptionist:latest
```

### 2. Deploy Service to Cloud Run
```bash
gcloud run deploy ai-receptionist \
  --image gcr.io/YOUR_PROJECT_ID/ai-receptionist:latest \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars="GOOGLE_GENAI_USE_VERTEXAI=True,GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID,SPREADSHEET_ID=YOUR_SPREADSHEET_ID,WISMO_SPREADSHEET_ID=YOUR_WISMO_SHEET_ID"
```

Once deployment completes, copy the secure service URL (e.g., `https://ai-receptionist-xxxxxx.run.app`).
