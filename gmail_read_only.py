import os
import sys
from typing import Optional, Dict, Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Read-only Gmail scope
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def get_credentials() -> Credentials:
    """
    Load user credentials from token.json if it exists,
    otherwise run the OAuth flow using credentials.json.
    """
    creds: Optional[Credentials] = None

    # Optional: allow overriding the credentials file via env var
    credentials_file = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")

    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    # If there are no (valid) creds, do the OAuth flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Refresh existing token
            creds.refresh(Request())
        else:
            # First time: run local OAuth consent in browser
            if not os.path.exists(credentials_file):
                raise FileNotFoundError(
                    f"Could not find {credentials_file}. "
                    "Download it from Google Cloud Console and keep it out of git."
                )

            flow = InstalledAppFlow.from_client_secrets_file(
                credentials_file, SCOPES
            )
            creds = flow.run_local_server(port=0)

        # Save updated or new token
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    return creds


def get_first_inbox_message(service) -> Optional[Dict[str, Any]]:
    """
    Fetch the first message in the user's INBOX (if any),
    and return the full message resource.
    """
    result = (
        service.users()
        .messages()
        .list(userId="me", labelIds=["INBOX"], maxResults=1)
        .execute()
    )

    messages = result.get("messages", [])
    if not messages:
        return None

    msg_id = messages[0]["id"]

    # Use format="metadata" to avoid pulling full body, just headers
    msg = (
        service.users()
        .messages()
        .get(
            userId="me",
            id=msg_id,
            format="metadata",
            metadataHeaders=["From", "Subject"],
        )
        .execute()
    )
    return msg


def extract_header(headers, name: str) -> str:
    """
    Utility to pull a header (e.g. Subject, From) from the message payload.
    """
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def main():
    try:
        creds = get_credentials()
        service = build("gmail", "v1", credentials=creds)

        message = get_first_inbox_message(service)

        if not message:
            print("Inbox is empty.")
            return

        headers = message.get("payload", {}).get("headers", [])
        subject = extract_header(headers, "Subject")
        sender = extract_header(headers, "From")
        snippet = message.get("snippet", "")



        print("=== First INBOX message ===")
        print(f"From   : {sender}")
        print(f"Subject: {subject}")
        print(f"Snippet: {snippet}")

    except HttpError as error:
        print(f"An error occurred: {error}", file=sys.stderr)


if __name__ == "__main__":
    main()