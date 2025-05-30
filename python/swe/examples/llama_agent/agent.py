import logging
from typing import Any, List

from composio_llamaindex import App, ComposioToolSet, WorkspaceType
from llama_index.core.base.llms.types import ChatMessage, MessageRole
from llama_index.core.llms.function_calling import FunctionCallingLLM
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.core.tools import ToolOutput, ToolSelection
from llama_index.core.tools.types import BaseTool
from llama_index.core.workflow import Event, StartEvent, StopEvent, Workflow, step
from llama_index.llms.openai import OpenAI
from prompts import BACKSTORY, GOAL, ROLE


# Set up basic configuration
logging.basicConfig(level=logging.INFO)

# Enable INFO logging for LlamaIndex
logging.getLogger("llama_index").setLevel(logging.DEBUG)

# Enable DEBUG logging for agent/tool calls
logging.getLogger("llama_index.agent").setLevel(logging.DEBUG)


class InputEvent(Event):
    input: list[ChatMessage]


class ToolCallEvent(Event):
    tool_calls: list[ToolSelection]


class FunctionOutputEvent(Event):
    output: ToolOutput


class FunctionCallingAgent(Workflow):
    def __init__(
        self,
        *args: Any,
        llm: FunctionCallingLLM | None = None,
        tools: List[BaseTool] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.tools = tools or []

        self.llm = llm or OpenAI(model="gpt-4-turbo")
        assert self.llm.metadata.is_function_calling_model

        self.memory = ChatMemoryBuffer.from_defaults(llm=llm)
        self.sources = []

        # Add system message to memory
        system_msg = ChatMessage(
            role=MessageRole.SYSTEM,
            content=f"Your role is {ROLE}\n Your backstory: {BACKSTORY}\n Your goal is: {GOAL}",
        )
        self.memory.put(system_msg)

    @step()
    async def prepare_chat_history(self, ev: StartEvent) -> InputEvent:
        # clear sources
        self.sources = []

        # get user input
        user_input = ev.get("input")
        user_msg = ChatMessage(role=MessageRole.USER, content=user_input)
        self.memory.put(user_msg)

        # get chat history
        chat_history = self.memory.get()
        return InputEvent(input=chat_history)

    @step()
    async def handle_llm_input(self, ev: InputEvent) -> ToolCallEvent | StopEvent:
        chat_history = ev.input
        for tool in self.tools:
            if len(tool.metadata.description) > 1024:
                print(
                    f"Tool {tool.metadata.name} description is too long: {len(tool.metadata.description)}"
                )
        response = await self.llm.achat_with_tools(
            self.tools, chat_history=chat_history
        )
        self.memory.put(response.message)

        tool_calls = self.llm.get_tool_calls_from_response(
            response, error_on_no_tool_call=False
        )

        if not tool_calls:
            return StopEvent(result={"response": response, "sources": [*self.sources]})
        else:
            return ToolCallEvent(tool_calls=tool_calls)

    @step()
    async def handle_tool_calls(self, ev: ToolCallEvent) -> InputEvent:
        tool_calls = ev.tool_calls
        tools_by_name = {tool.metadata.get_name(): tool for tool in self.tools}

        tool_msgs = []

        # call tools -- safely!
        for tool_call in tool_calls:
            tool = tools_by_name.get(tool_call.tool_name)
            additional_kwargs = {
                "tool_call_id": tool_call.tool_id,
                "name": tool.metadata.get_name(),
            }
            if not tool:
                tool_msgs.append(
                    ChatMessage(
                        role=MessageRole.TOOL,
                        content=f"Tool {tool_call.tool_name} does not exist",
                        additional_kwargs=additional_kwargs,
                    )
                )
                continue

            try:
                tool_output = tool(**tool_call.tool_kwargs)
                self.sources.append(tool_output)
                tool_msgs.append(
                    ChatMessage(
                        role=MessageRole.TOOL,
                        content=tool_output.content,
                        additional_kwargs=additional_kwargs,
                    )
                )
            except Exception as e:
                tool_msgs.append(
                    ChatMessage(
                        role=MessageRole.TOOL,
                        content=f"Encountered error in tool call: {e}",
                        additional_kwargs=additional_kwargs,
                    )
                )

        for msg in tool_msgs:
            self.memory.put(msg)

        chat_history = self.memory.get()
        return InputEvent(input=chat_history)


composio_toolset = ComposioToolSet(
    workspace_config=WorkspaceType.Docker(
        image="composio/composio:latest",
    )
)
tools = composio_toolset.get_tools(apps=[App.FILETOOL, App.SHELLTOOL])

launcher = FunctionCallingAgent(
    llm=OpenAI(model="gpt-4-turbo"), tools=list(tools), timeout=120, verbose=True
)
