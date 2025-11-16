import os
from typing import Optional
import re
import json
import ollama
from dotenv import load_dotenv
import matplotlib.pyplot as plt
from collections import Counter
import google.auth.credentials
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.transport.requests import Request
from redis import Redis
from prompts import CATEGORY_PROMPT, PRIORITY_PROMPT, RESPONSE_PROMPT


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

    #Expected values for the prompt type
    expected_map = {
        "category": ["Work", "School", "Shopping", "Personal", "Spam"],
        "priority": ["Urgent", "Important", "Normal", "Low"],
        "response": ["Yes", "No"]
    }


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
            {"role": "user", "content": prompt}
        ]
    )

    #extract the text and parse
    response_text = response.message.content.strip()
    parsed_response = parse_llm_response(response_text, expected_map[prompt_type])


    return parsed_response
 

def parse_llm_response(response_text: str, expected_values: list[str]) -> str:
    """Parse plain text LLM response and extract the expected value"""
    response_text = response_text.strip()
    
    # Try exact match first (case-insensitive)
    for value in expected_values:
        if value.lower() in response_text.lower():
            return value
    
    # Fallback: return first word capitalized
    first_word = response_text.split()[0] if response_text else expected_values[-1]
    
    # Try fuzzy match on first word
    for value in expected_values:
        if first_word.lower() == value.lower():
            return value
    
    # Default to last option (most conservative)
    return expected_values[-1]


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


def aggregate_results(analysis_data: dict) -> dict:
    """Count occurrences of each category/priority/response"""
    return {
        'category': dict(Counter(analysis_data['category'])),
        'priority': dict(Counter(analysis_data['priority'])),
        'response': dict(Counter(analysis_data['response'])),
        'total': analysis_data['number_of_emails']
    }


def process_emails(num_emails: int) -> list[dict]:
    """process the emails and return the analysis"""
    analysis_dic = {'number_of_emails': num_emails, 'category': [], 'priority': [], 'response': []}
    creds = authenticate_gmail()
    service = build("gmail", "v1", credentials=creds)
    emails = fetch_gmail(service, num_emails)
    for email in emails:
        analysis = analyze_email(email)
        analysis_dic['category'].append(analysis['category'])
        analysis_dic['priority'].append(analysis['priority'])
        analysis_dic['response'].append(analysis['response'])
    return analysis_dic

def create_visualization(data: dict):
    """Create multiple visualizations"""
    fig, axes = plt.subplots(2, 2, figsize=(12, 10))
    
    # 1. Category Pie Chart
    if data['category']:
        axes[0, 0].pie(data['category'].values(), labels=data['category'].keys(), autopct='%1.1f%%')
        axes[0, 0].set_title('Email Categories')
    
    # 2. Priority Bar Chart
    if data['priority']:
        axes[0, 1].barh(list(data['priority'].keys()), list(data['priority'].values()))
        axes[0, 1].set_xlabel('Count')
        axes[0, 1].set_title('Priority Distribution')
    
    # 3. Response Needed Pie Chart
    if data['response']:
        axes[1, 0].pie(data['response'].values(), labels=data['response'].keys(), autopct='%1.1f%%')
        axes[1, 0].set_title('Response Needed')
    
    # 4. Summary Stats
    axes[1, 1].axis('off')
    summary_text = f"Total Emails: {data['total']}\n\n"
    summary_text += "Top Category:\n" + max(data['category'].items(), key=lambda x: x[1])[0] + "\n\n"
    summary_text += "Most Common Priority:\n" + max(data['priority'].items(), key=lambda x: x[1])[0]
    axes[1, 1].text(0.1, 0.5, summary_text, fontsize=14, verticalalignment='center')
    
    plt.tight_layout()
    plt.show()



def main():
    """main function"""
    num_emails = int(os.getenv("EMAIL_COUNT", "10"))

    print(f"Processing {num_emails} emails...")
    analysis_data = process_emails(num_emails)

    print("\nAggregating results...")
    aggregated = aggregate_results(analysis_data)

    print("\n=== Results ===")
    print(f"Total emails analyzed: {aggregated['total']}")
    print(f"Categories: {aggregated['category']}")
    print(f"Priorities: {aggregated['priority']}")
    print(f"Response needed: {aggregated['response']}")

    print("\nGenerating visualizations...")
    create_visualization(aggregated)




if __name__ == "__main__":
    main()