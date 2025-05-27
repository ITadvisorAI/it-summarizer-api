import os
import json
import time
import zipfile
import smtplib
import threading
import traceback
import requests
from email.message import EmailMessage
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# === Setup Google Drive ===
drive_service = None
try:
    creds_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if creds_json:
        creds = service_account.Credentials.from_service_account_info(
            json.loads(creds_json),
            scopes=["https://www.googleapis.com/auth/drive"]
        )
        drive_service = build("drive", "v3", credentials=creds)
except Exception as e:
    print("❌ Drive auth failed:", e)
    traceback.print_exc()

# === Upload to Google Drive ===
def upload_to_drive(path, session_id):
    try:
        query = f"name='{session_id}' and mimeType='application/vnd.google-apps.folder'"
        folder_result = drive_service.files().list(q=query, fields="files(id)").execute()
        folder_id = folder_result["files"][0]["id"] if folder_result["files"] else None

        if not folder_id:
            folder = drive_service.files().create(body={
                "name": session_id,
                "mimeType": "application/vnd.google-apps.folder"
            }, fields="id").execute()
            folder_id = folder["id"]

        file_meta = {"name": os.path.basename(path), "parents": [folder_id]}
        media = MediaFileUpload(path, resumable=True)
        uploaded = drive_service.files().create(body=file_meta, media_body=media, fields="id").execute()
        return f"https://drive.google.com/file/d/{uploaded['id']}/view"
    except Exception as e:
        print("❌ Upload failed:", e)
        return None

# === Email the ZIP ===
def send_zip_email(to_email, zip_path, session_id):
    try:
        msg = EmailMessage()
        msg["Subject"] = f"Your IT Modernization Reports – {session_id}"
        msg["From"] = os.getenv("SMTP_FROM_EMAIL")
        msg["To"] = to_email
        msg.set_content(f"""Dear user,

Attached is the ZIP archive of your IT modernization reports for session: {session_id}.

Thank you,
Transformation Advisor
""")

        with open(zip_path, "rb") as f:
            msg.add_attachment(f.read(), maintype="application", subtype="zip", filename=os.path.basename(zip_path))

        with smtplib.SMTP_SSL(os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT"))) as smtp:
            smtp.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASS"))
            smtp.send_message(msg)
            print("📧 Email sent to", to_email)
    except Exception as e:
        print("❌ Email failed:", e)
        traceback.print_exc()

# === Deduplicate by file_type ===
def deduplicate(files):
    seen = {}
    for f in files:
        key = f["file_type"]
        if key not in seen:
            seen[key] = f
    return list(seen.values())

# === Create ZIP archive ===
def create_zip(files, folder_path, session_id):
    zip_name = f"{session_id}_final_reports.zip"
    zip_path = os.path.join(folder_path, zip_name)

    with zipfile.ZipFile(zip_path, "w") as zipf:
        for f in files:
            local_path = os.path.join(folder_path, f["file_name"])
            try:
                r = requests.get(f["file_url"], timeout=30)
                with open(local_path, "wb") as out:
                    out.write(r.content)
                zipf.write(local_path, arcname=f["file_name"])
            except Exception as e:
                print(f"⚠️ Failed to download {f['file_name']}:", e)
    return zip_path

# === Notify GPT1 ===
def notify_gpt1(session_id):
    try:
        requests.post("https://it-advisor-api.onrender.com/receive_status", json={
            "session_id": session_id,
            "status": "final_reports_delivered",
            "message": "All reports delivered via email. Awaiting confirmation."
        })
    except Exception as e:
        print("⚠️ Failed to notify GPT1:", e)

# === Cleanup routine ===
def cleanup_after_2_hours(session_id, folder_path, email):
    time.sleep(7200)  # wait 2 hours
    try:
        query = f"name='{session_id}' and mimeType='application/vnd.google-apps.folder'"
        result = drive_service.files().list(q=query, fields="files(id)").execute()
        if result["files"]:
            folder_id = result["files"][0]["id"]
            drive_service.files().delete(fileId=folder_id).execute()
            print(f"🗑️ Deleted session folder {session_id} from Drive.")

        # Send deletion email
        msg = EmailMessage()
        msg["Subject"] = f"[Session Expired] Reports Deleted – {session_id}"
        msg["From"] = os.getenv("SMTP_FROM_EMAIL")
        msg["To"] = email
        msg.set_content(f"""Dear user,

No confirmation was received for session {session_id}.
All generated reports have now been deleted from our system.

If you wish to revisit your reports, please re-upload the source files.

Regards,
Transformation Advisor
""")
        with smtplib.SMTP_SSL(os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT"))) as smtp:
            smtp.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASS"))
            smtp.send_message(msg)

        # Notify GPT1 of deletion
        requests.post("https://it-advisor-api.onrender.com/receive_status", json={
            "session_id": session_id,
            "status": "deleted",
            "message": "No confirmation received. Session folder deleted."
        })

    except Exception as e:
        print("❌ Cleanup failed:", e)
        traceback.print_exc()

# === Main processing logic ===
def process_summarizer(session_id, email, files, folder_path):
    try:
        os.makedirs(folder_path, exist_ok=True)
        deduped = deduplicate(files)
        zip_path = create_zip(deduped, folder_path, session_id)
        upload_to_drive(zip_path, session_id)
        send_zip_email(email, zip_path, session_id)
        notify_gpt1(session_id)
        threading.Thread(target=cleanup_after_2_hours, args=(session_id, folder_path, email), daemon=True).start()
    except Exception as e:
        print("🔥 Summarizer failed:", e)
        traceback.print_exc()
