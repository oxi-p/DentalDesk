import asyncio
from datetime import datetime
import os
import logging
import json

from app.whatsapp import send_message
from shared import db
from shared.models import Patient

from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_mcp_adapters.prompts import load_mcp_prompt

from langgraph.graph import MessagesState
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

server_params = StdioServerParameters(
    command="uv",
    args=["run", "dentaldesk-mcp", "--verbose"],
    env=None,
    cwd=os.getcwd(),
)


class State(MessagesState):
    """Extends the base MessagesState to include last interaction time and patient info."""
    last_interaction_time: datetime | None = None
    patient: Patient | None = None
    conversation_id: int | None = None


# Global async queue
message_queue: asyncio.Queue = asyncio.Queue()


async def create_graph(session):
    """
    Creates and returns the agent graph.
    The graph consists of nodes for the assistant, tool calls, and state updates.
    The assistant node uses a ChatOpenAI model with access to the provided tools.
    Each conversation is tracked by a unique thread_id in the checkpointer.
    Args:
        session: The MCP client session to load tools from.
    Returns:
        The compiled StateGraph agent.
    """
    logger.info("Creating agent graph...")

    tools = await load_mcp_tools(session)
    systemPrompt = await load_mcp_prompt(
        session, 
        "system_prompt"
    )

    llm = ChatOpenAI(model=os.getenv("OPENAI_MODEL_NAME", "gpt-4o"))
    llm_with_tools = llm.bind_tools(tools)

    # Graph
    builder = StateGraph(State)

    # Node: Assistant
    def assistant(state: State):
        """The main assistant node that generates responses using the LLM and tools."""
        logger.debug(f"Assistant node invoked with state: {state}")
        patient = state.get("patient")
        patient_context = ""
        if patient:
            patient_context = (f"\n\n--- Current Patient Information ---\n"
                             f"Name: {patient.name}\n"
                             f"Age: {patient.age or 'Not provided'}\n"
                             f"Gender: {patient.gender or 'Not provided'}\n"
                             f"Phone Number: {patient.phone_number}\n"
                             f"Current Conversation ID: {state.get('conversation_id')}\n"
                             f"---------------------------------")
        
        final_system_prompt = systemPrompt[0].content + patient_context
        sys_msg = SystemMessage(content=final_system_prompt)
        response = llm_with_tools.invoke([sys_msg] + state["messages"])
        return {"messages": [response]}

    # Node: Update Timestamp
    def update_timestamp_node(state: State) -> dict:
        """Nodes that just updates the timestamp in the state."""
        return {"last_interaction_time": datetime.now()}
    
    builder.add_node("assistant", assistant)
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("update_timestamp", update_timestamp_node)

    builder.add_edge(START, "assistant")
    builder.add_conditional_edges(
        "assistant",
        tools_condition,
        {
            "tools": "tools", 
            "__end__": "update_timestamp"  # If no tools are called, update timestamp before ending
        }
    )
    builder.add_edge("tools", "assistant")
    builder.add_edge("update_timestamp", END) # After updating, end the graph

    memory = InMemorySaver()
    agent = builder.compile(checkpointer=memory)

    return agent


async def enqueue_message(patient_phone: str, message: str):
    """
    Async function to enqueue incoming messages for processing by the agent.
    Finds or creates a patient and a conversation, then adds the message to the queue.
    The patient is identified by their phone number.
    Args:
        patient_phone: The phone number of the patient sending the message.
        message: The content of the message sent by the patient.
    returns: 
        None
    """
    logger.debug(f"Enqueueing message from {patient_phone}")

    # Find a patient record
    patient = db.get_patient_by_phone(patient_phone)
    if not patient:
        # For a new patient, create a basic record. The agent can gather more details.
        patient = db.create_patient(Patient(name="New Patient", phone_number=patient_phone))

    # Find an open conversation for the patient or create a new one
    conversation = db.get_open_conversation(patient.id)
    if not conversation:
        conversation = db.create_conversation(patient.id)
    
    logger.info(f"Enqueuing message for patient {patient.id} in conversation {conversation.id}")

    await message_queue.put({
        "conversation_id": conversation.id,
        "patient": patient,
        "message": message,
        "timestamp": datetime.now(),
    })


async def consume_messages(agent):
    """
    Continuously consumes messages from the queue and processes them with the agent.
    Args:
        agent: The StateGraph agent to process messages.
    """
    logger.info("Starting message consumer...")

    while True:
        logger.debug("Waiting for new message in queue...")
        task = await message_queue.get()
        conversation_id = task.get("conversation_id")
        try:
            patient = task["patient"]
            message = task["message"]
            patient_phone_for_reply = patient.phone_number

            config = {"configurable": {"thread_id": str(conversation_id)}}

            # If agent has no state for this conversation, load it from the database.
            # This handles cases where the agent restarts and loses its in-memory state.
            # current_state is of type StateSnapshot
            current_state = await agent.aget_state(config)
            logger.debug(f"Current state for conversation {conversation_id}: {current_state}")

            if current_state is None or not current_state.values.get("messages"):
                logger.info(f"No agent state found for conversation {conversation_id}. Checking DB for history...")
                history = db.get_messages(conversation_id)
                history_messages = []
                if history:
                    for msg in history:
                        # Convert DB messages to appropriate Message types
                        if msg.sender == 'user':
                            history_messages.append(HumanMessage(content=msg.message))
                        elif msg.sender == 'agent':
                            history_messages.append(AIMessage(content=msg.message))
                        elif msg.sender == 'agent_tool_call':
                            # Rebuild AIMessage with tool_calls from JSON
                            tool_calls = json.loads(msg.message)
                            history_messages.append(AIMessage(content="", tool_calls=tool_calls))
                        elif msg.sender == 'tool':
                            # Rebuild ToolMessage from JSON
                            tool_data = json.loads(msg.message)
                            history_messages.append(ToolMessage(content=tool_data['content'], tool_call_id=tool_data['tool_call_id']))
                
                update_payload = {
                    "messages": history_messages,
                    "patient": patient,
                    "conversation_id": conversation_id,
                }
                logger.info(f"Updating state for conversation {conversation_id} with {len(history_messages)} messages and patient info.")
                await agent.aupdate_state(config, update_payload, START)

            # Add the new user message to the database
            db.add_message(conversation_id=conversation_id, sender="user", message=message)

            # Invoke the agent with the new message
            response = await agent.ainvoke({"messages": [HumanMessage(content=message)]}, config)

            # Find the index of the last HumanMessage to identify messages from this turn.
            last_human_message_index = -1
            for i, msg in reversed(list(enumerate(response["messages"]))):
                if isinstance(msg, HumanMessage):
                    last_human_message_index = i
                    break

            # Process and save all messages generated by the agent in the current turn.
            if last_human_message_index != -1:
                messages_this_turn = response["messages"][last_human_message_index + 1:]
                for msg in messages_this_turn:
                    if isinstance(msg, AIMessage):
                        if msg.tool_calls:
                            # Persist the agent's decision to call a tool by saving the tool_calls list as JSON
                            db.add_message(
                                conversation_id=conversation_id,
                                sender="agent_tool_call",
                                message=json.dumps(msg.tool_calls)
                            )
                        elif msg.content:
                            # This is the final text reply for the user
                            db.add_message(conversation_id=conversation_id, sender="agent", message=msg.content)
                            send_message(patient_phone_for_reply, msg.content)
                    elif isinstance(msg, ToolMessage):
                        # Persist the tool result by saving its content and ID as JSON
                        tool_data = {"content": msg.content, "tool_call_id": msg.tool_call_id}
                        db.add_message(
                            conversation_id=conversation_id,
                            sender="tool",
                            message=json.dumps(tool_data)
                        )
            else:
                # Fallback for cases where no new agent messages were generated after the user's.
                reply = response["messages"][-1].content
                db.add_message(conversation_id=conversation_id, sender="agent", message=reply)
                send_message(patient_phone_for_reply, reply)

        except Exception as e:
            logger.error(f"[Agent Error] Failed to process message for conversation {conversation_id}: {e}", exc_info=True)

        finally:
            message_queue.task_done()


async def conversation_cleanup_task(agent):
    """
    Periodically checks for timed-out conversations and closes them.
    A conversation is considered timed out if there has been no interaction for a specified interval.
    This function runs indefinitely as a background task.
    Args:
        agent: The StateGraph agent whose checkpointer is used to track conversations.
        interval_minutes: The interval in minutes to check for timed-out conversations.
    """
    logger.info("Starting conversation cleanup task...")
    interval_minutes=int(os.getenv("CONVERSATION_TIMEOUT_MINUTES", 30))

    while True:
        await asyncio.sleep(interval_minutes * 60)
        logger.info("Running conversation cleanup task...")

        checkpointer = agent.checkpointer
        if checkpointer is None:
            logger.warning("Agent has no checkpointer. Skipping cleanup.")
            continue

        # create a set of thread_ids to avoid modifying the dict while iterating
        all_thread_ids = set()
        try:
            for item in checkpointer.list(None):
                config = item.config
                thread_id = config["configurable"]["thread_id"]
                all_thread_ids.add(thread_id)
                
        except Exception as e:
            logger.error(f"Error listing threads from checkpointer: {e}", exc_info=True)

        if not all_thread_ids:
            logger.info("No active conversation threads found in checkpointer.")
            continue

        try:
            logger.debug(f"Current active threads in checkpointer: {all_thread_ids}")
            for thread_id in all_thread_ids:
                config = {"configurable": {"thread_id": thread_id}}
                state = await agent.aget_state(config)
                logger.debug(f"Checking thread {thread_id} with state: {state}")
                if state and state.values and state.values.get("last_interaction_time"):
                    last_interaction = state.values["last_interaction_time"]
                    if isinstance(last_interaction, str):
                        last_interaction = datetime.fromisoformat(last_interaction)

                    if (datetime.now() - last_interaction).total_seconds() > (interval_minutes * 60):
                        logger.info(f"Conversation thread {thread_id} has timed out.")
                        # Check DB status before closing to avoid race conditions
                        conversation = db.get_conversation(int(thread_id))
                        if conversation and conversation.status == 'open':
                            logger.info(f"Closing conversation {thread_id} in DB with reason 'timed_out'.")
                            db.close_conversation(int(thread_id), reason="timed_out")
                        else:
                            logger.info(f"Conversation {thread_id} already closed in DB, skipping DB update.")

                        # delete the thread from the checkpointer
                        checkpointer.delete_thread(thread_id)
                        logger.info(f"Removed thread {thread_id} from agent checkpointer.")

                # Also clean up threads that have no state or interaction time, but are lingering
                elif not state or not state.values:
                    checkpointer.delete_thread(thread_id)
                    logger.info(f"Found lingering empty thread {thread_id}. Cleaning up.")

        except Exception as e:
            logger.error(f"Error during cleanup for thread {thread_id}: {e}", exc_info=True)


async def main():
    # Ensure the database is initialized before starting the agent
    logger.info("Checking and initializing database...")
    db.init_db(seed=True)

    # start up the MCP server locally and run our agent
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # Initialize the connection
            await session.initialize()
            tools = await load_mcp_tools(session)
            logger.debug(f"Loaded MCP tools: {[tool.name for tool in tools]}")

            agent = await create_graph(session)
    
            # Start the background cleanup task
            asyncio.create_task(conversation_cleanup_task(agent))

            # Start consuming queue
            await consume_messages(agent)


if __name__ == "__main__":
    asyncio.run(main())
