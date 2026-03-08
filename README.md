# ⛳ Golf Practice Report Generator

A Streamlit app that converts TrackMan, Foresight Quad, or FlightScope launch monitor data into a formatted Excel practice report card — with optional Google Drive upload.

---

## Features

- **Multi-device support**: TrackMan (CSV), Foresight Quad (CSV), FlightScope (XLSX)
- **Auto-detects clubs** from the uploaded file — user sets level (1–12) and target type per club
- **Live Excel formulas**: the Distance (yd) column is highlighted yellow and editable post-download — all dependent columns recalculate automatically
- **Tour Targets benchmarks** scaled by player level (1–12) using the same lookup tables as the original Google Sheets system
- **Optional Google Drive upload** — one click to save the report directly to a Drive folder

---

## Local Development

```bash
# Clone and install
git clone <your-repo-url>
cd golf-report-app
pip install -r requirements.txt

# Run
streamlit run app.py
```

---

## Deploy to Streamlit Community Cloud (Free)

1. Push this folder to a **public or private GitHub repo**
2. Go to [share.streamlit.io](https://share.streamlit.io) → New app
3. Select your repo, branch `main`, and set **Main file path** to `app.py`
4. Click **Deploy**

---

## Google Drive Setup (Optional)

The app can upload generated reports directly to Google Drive using a **Service Account**.

### Step 1: Create a Google Cloud Project & Service Account

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (or use an existing one)
3. Enable the **Google Drive API**
4. Go to **IAM & Admin → Service Accounts → Create Service Account**
5. Give it a name (e.g. `golf-report-uploader`)
6. Click **Create and Continue** → skip role assignment → **Done**
7. Click the service account → **Keys** tab → **Add Key → Create new key → JSON**
8. Download the JSON file

### Step 2: Share a Drive Folder with the Service Account

1. Open Google Drive
2. Right-click the folder where reports should be saved → **Share**
3. Paste the service account email (found in the JSON, looks like `name@project.iam.gserviceaccount.com`)
4. Give it **Editor** access → **Send**

### Step 3: Add Secrets to Streamlit

In Streamlit Community Cloud → your app → **Settings → Secrets**, paste:

```toml
[google_service_account]
type = "service_account"
project_id = "your-project-id"
private_key_id = "key-id-from-json"
private_key = """-----BEGIN RSA PRIVATE KEY-----
...paste full key here...
-----END RSA PRIVATE KEY-----"""
client_email = "your-service-account@project.iam.gserviceaccount.com"
client_id = "123456789"
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
```

For **local development**, create `.streamlit/secrets.toml` with the same content (this file is gitignored by default — never commit it).

---

## Project Structure

```
golf-report-app/
├── app.py                  # Streamlit UI
├── parsers.py              # TrackMan / Foresight / FlightScope CSV → common schema
├── calculations.py         # Proximity, distance control, targets, goals
├── report_builder.py       # openpyxl Excel generation
├── drive_upload.py         # Google Drive API upload
├── data/
│   └── tour_targets.py     # Tour Targets & Levels Multipliers lookup tables
├── requirements.txt
└── .streamlit/
    └── config.toml         # Theme config
```

---

## Launch Monitor Column Mapping

| Field | TrackMan | Foresight Quad | FlightScope |
|---|---|---|---|
| Ball Speed | Ball Speed (mph) | Ball Speed | Ball (mph) |
| Club Speed | Club Speed (mph) | Club Speed | Club (mph) |
| Carry | Carry (yds) | Carry | Carry (yds) |
| Total | Total Distance (yds) | Total | Total (yds) |
| Offline | Offline (yds L-/R+) | Offline | Lateral (yds) |
| Launch Angle | Launch Angle (deg) | Launch Angle | Launch V (°) |
| Total Spin | Total Spin (rpm) | Total Spin | Spin (rpm) |
| Club Path | Club Path (deg out-in-/in-out+) | Club Path | Club Path (°) |
| Face to Target | Face to Target (deg closed-/open+) | Face to Path | FTT (°) |
| AOA | Angle of Attack (deg) | Angle of Attack | AOA (°) |

---

## Report Logic

- **Proximity target** = Tour proximity (ft) × level proximity multiplier
- **Distance control range** = Tour range (yd) × level proximity multiplier  
- **Target rate** = min(1.0, Tour target rate × level rate multiplier)
- **Goal Met** if actual % ≥ target %
- **Approaching Goal** if actual % ≥ target % × 0.7
- **Goal in Progress** otherwise
