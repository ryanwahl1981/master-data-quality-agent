"""
Connection test: sends one tiny message to Claude and prints the reply.
Costs a fraction of a cent. The API key is read automatically from the
ANTHROPIC_API_KEY setting on your computer, so it is NOT written in this file.
"""
import anthropic

client = anthropic.Anthropic()

reply = client.messages.create(
    model="claude-haiku-4-5-20251001",  # the smallest, cheapest Claude model
    max_tokens=50,
    messages=[{"role": "user", "content": "Reply with exactly: API connection works."}],
)

print(reply.content[0].text)
