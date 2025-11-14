import ollama

response = ollama.chat(
    model="gemma3:1b",
    messages=[
        {"role": "user", "content": "Hello, how are you?"}
    ]
)

print(response.message.content)