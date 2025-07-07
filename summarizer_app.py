import os
import json
import logging
import shutil
from flask import Flask, request, jsonify
from process_summarizer import process_summarizer  # assumes this returns the path to the ZIP file
from drive_utils import upload_to_drive

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BASE_DIR = "temp_sessions"
os.makedirs(BASE_DIR, exist_ok=True)


@app.route("/", methods=["GET"])
def health_check():
    return "✅ Summarizer GPT is live", 200


@app.route("/start_summarizer", methods=["POST"])
def start_summarizer():
    try:
        data = request.get_json(force=True)
        logging.info("📦 Incoming Summarizer request:\n%s", json.dumps(data, indent=2))

        session_id = data.get("session_id")
        email = data.get("email")
        files = data.get("files", [])
        if not session_id or not email or not files:
            logging.error("❌ Missing required fields in summarizer payload")
            return jsonify({"error": "Missing required fields: session_id, email, files"}), 400

        # Prepare session folder
        folder_name = session_id if session_id.startswith("Temp_") else f"Temp_{session_id}"
        folder_path = os.path.join(BASE_DIR, folder_name)
        os.makedirs(folder_path, exist_ok=True)

        # Process summarizer synchronously: create ZIP and send email
        zip_path = process_summarizer(session_id, email, files, folder_path)
        if not os.path.isfile(zip_path):
            raise FileNotFoundError(f"ZIP file not found at {zip_path}")

        # Upload ZIP for download link
        zip_filename = os.path.basename(zip_path)
        zip_url = upload_to_drive(zip_path, session_id, data.get("folder_id", ""))

        # Delete temp folder
        shutil.rmtree(folder_path)
        logging.info(f"🗑️ Deleted temp folder: {folder_path}")

        # Build response message
        message = (
            f"✅ Your reports have been emailed to {email}. "
            f"You can also download them here: {zip_url}. "
            "The temporary session folder has been deleted. "
            "If you have any questions about the generated reports, please re-upload the documents for reference."
        )

        return jsonify({"message": message, "zip_url": zip_url}), 200

    except Exception as e:
        logging.exception("🔥 Summarizer processing failed")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "16000"))
    logging.info(f"🌐 Summarizer API listening on port {port}")
    app.run(host="0.0.0.0", port=port)
