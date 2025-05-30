import typing as t
from composio_openai import ComposioToolSet, action, Action
from openai import OpenAI
import base64

from dotenv import load_dotenv
load_dotenv()

openai_client = OpenAI()
toolset = ComposioToolSet(entity_id="abhishek")


@action(toolname="gmail")
def create_draft(
    thread_id: str,
    message_body: str,
    execute_request: t.Callable,
) -> dict:
    """
    Create a draft reply to a specific Gmail thread

    :param thread_id: The ID of the thread to which the reply is to be drafted
    :param message_body: The content of the draft reply
    :return draft: The created draft details
    """
    request_body = {
        "message": {
            "threadId": thread_id,
            "raw": base64.urlsafe_b64encode(
                f"Content-type: text/plain; charset=UTF-8\n\n{message_body}".encode(
                    "utf-8"
                )
            ).decode("utf-8"),
        }
    }

    response = execute_request(f"/gmail/v1/users/me/drafts", "post", request_body, None)
    return response.get("data", {})


tools = toolset.get_tools(actions=[create_draft])

task = "Reply to the thread with ID: 193b052522a739c9, saying 'Sure'"

response = openai_client.chat.completions.create(
model="gpt-4o-mini",
tools=tools,
messages=
    [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": task},
    ],
)

result = toolset.handle_tool_calls(response)
print(result)