CATEGORY_PROMPT = """Analyze this email and categorize it.

From: {sender}
Subject: {subject}
Preview: {body_preview}

Choose ONE category: Work, School, Shopping, Personal, Spam

Category:"""

PRIORITY_PROMPT = """Rate the priority of this email.

From: {sender}
Subject: {subject}
Preview: {body_preview}

Choose ONE priority: Urgent, Important, Normal, Low

Priority:"""

RESPONSE_PROMPT = """Does this email need a response?

From: {sender}
Subject: {subject}
Preview: {body_preview}

Rules:
- Say "Yes" if from work, school, or a friend
- Say "No" if it's marketing, ads, newsletters, automated messages, or no-reply emails

Answer with only: Yes or No

Response needed:"""