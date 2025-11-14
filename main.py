import os
from typing import Optional
import re
import json
import ollama
import google.auth.credentials
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.transport.requests import Request
from redis import Redis
from prompts import CATEGORY_PROMPT, PRIORITY_PROMPT, RESPONSE_PROMPT
from dotenv import load_dotenv

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
redis_client = Redis(host=os.getenv("REDIS_HOST"), port=os.getenv("REDIS_PORT"), db=os.getenv("REDIS_DB"))


def authenticate_gmail() -> Credentials:
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
        with open("token.json", "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return creds

def fetch_gmail(service, count=50) -> list[dict]:
    """Fetch the latest emails from the user's inbox."""
    
    cache_expiration = int(os.getenv("CACHE_TTL", "86400"))  # Default 24 hours

    result = (
        service.users()
        .messages()
        .list(userId="me", labelIds=["INBOX"], maxResults=count)
        .execute()
    )

    messages = result.get("messages", [])
    if not messages:
        return None

    for message in messages:
        cache_key = generate_cache_key(message["id"], "email_data")
        cached_data = redis_client.get(cache_key)
        
        if cached_data:
            # Deserialize from JSON
            email_data = json.loads(cached_data)
            print(f"✓ Cache hit: Email data for {message['id']}")
        else:
            # Cache miss - fetch from Gmail
            print(f"✗ Cache miss: Fetching email {message['id']}")
            msg = service.users().messages().get(userId="me", id=message["id"]).execute()
            headers = msg.get("payload", {}).get("headers", [])
            #Extract headers
            header_dict = {}
            for header in headers:
                header_name = header.get("name")
                if header_name in ("From", "Subject"):
                    header_dict[header_name] = header.get("value", "")
            
            snippet = msg.get("snippet", "")
            snippet = re.sub(r'[\u200b-\u200f\u202a-\u202e]', '', snippet).strip()

            email_data = {
                "id": message["id"],
                "from": header_dict.get("From", ""),
                "subject": header_dict.get("Subject", ""),
                "snippet": snippet
            }

            # Serialize to JSON before storing
            redis_client.setex(cache_key, cache_expiration, json.dumps(email_data))

        yield email_data

   

def generate_cache_key(email_id: str, prompt_type: str) -> str:
    """generate a cache key for the prompt"""
    return f"{email_id}_{prompt_type}"

def call_llm_with_cache(email_id: str, prompt_type: str, email_data: dict) -> str:
    "call the llm with the cache if it exists, otherwise call the llm without the cache"
    model = os.getenv("OLLAMA_MODEL")
    #TODO: first implement with a simple llm call

    #fetch prompt from prompts.py
    if prompt_type == "category":
        prompt = CATEGORY_PROMPT.format(sender=email_data["from"], subject=email_data["subject"], body_preview=email_data["snippet"])
    elif prompt_type == "priority":
        prompt = PRIORITY_PROMPT.format(sender=email_data["from"], subject=email_data["subject"], body_preview=email_data["snippet"])
    elif prompt_type == "response":
        prompt = RESPONSE_PROMPT.format(sender=email_data["from"], subject=email_data["subject"], body_preview=email_data["snippet"])
    else:
        raise ValueError(f"Invalid prompt type: {prompt_type}")

        
    response = ollama.chat(
        model=model, 
        messages=[
            {"role": "system", "content": "Only respond in JSON format. e.g {'category': 'Work'}. Do not include any other text or comments."},
            {"role": "user", "content": prompt}
        ]
    )
    return response
 

def parse_llm_response(response: str, expected_fields: list[str]) -> dict:
    pass

def analyze_email(email_data: dict) -> dict:
    """analyze the email based on the 3 categories: category, priority, response"""
    cache_expiration = int(os.getenv("CACHE_TTL", "86400"))  # Default 24 hours
    analysis = {}
    for prompt_type in ["category", "priority", "response"]:
        cache_key = generate_cache_key(email_data["id"], prompt_type)
        cached_response = redis_client.get(cache_key)
        
        if cached_response:
            # Deserialize from JSON
            analysis[prompt_type] = json.loads(cached_response)
            print(f"✓ Cache hit: {prompt_type} for {email_data['id']}")
        else:
            print(f"✗ Cache miss: Calling LLM for {prompt_type} on {email_data['id']}")
            response = call_llm_with_cache(email_data["id"], prompt_type, email_data)
            # Serialize to JSON before storing
            redis_client.setex(cache_key, cache_expiration, json.dumps(response))
            analysis[prompt_type] = response
    return analysis

def process_emails(emails):
    pass

def create_visualization(data: list[dict]):
    pass

def main():
    creds = authenticate_gmail()
    service = build("gmail", "v1", credentials=creds)
    emails = fetch_gmail(service, 1)
    for email in emails:
        print(email)
        response = call_llm_with_cache(email["id"], "category", email)
        print(response)

if __name__ == "__main__":
    main()