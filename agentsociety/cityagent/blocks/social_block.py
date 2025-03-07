# Due to the current limitations of the simulator's support, only NoneBlock, MessageBlock, and FindPersonBlock are available in the Dispatcher.

import json
import logging
from typing import Any, Optional

from agentsociety.environment import Simulator
from agentsociety.llm import LLM
from agentsociety.memory import Memory
from agentsociety.workflow import Block, FormatPrompt

from .dispatcher import BlockDispatcher
from .utils import TIME_ESTIMATE_PROMPT, clean_json_response

logger = logging.getLogger("agentsociety")


class MessagePromptManager:
    """
    Manages the creation of message prompts by dynamically formatting templates with agent-specific data.
    """

    def __init__(self):
        pass

    async def get_prompt(
        self, memory, step: dict[str, Any], target: str, template: str
    ) -> str:
        """Generates a formatted prompt for message creation.

        Args:
            memory: Agent's memory to retrieve status data.
            step: Current workflow step containing intention and context.
            target: ID of the target agent for communication.
            template: Raw template string to be formatted.

        Returns:
            Formatted prompt string with placeholders replaced by agent-specific data.
        """
        # Retrieve data
        relationships = await memory.status.get("relationships") or {}
        chat_histories = await memory.status.get("chat_histories") or {}

        # Build discussion topic constraints
        discussion_constraint = ""
        topics = await memory.status.get("attitude")
        topics = topics.keys()
        if topics:
            topics = ", ".join(f'"{topic}"' for topic in topics)
            discussion_constraint = (
                f"Limit your discussion to the following topics: {topics}."
            )

        # Format prompt
        format_prompt = FormatPrompt(template)
        format_prompt.format(
            gender=await memory.status.get("gender") or "",
            education=await memory.status.get("education") or "",
            personality=await memory.status.get("personality") or "",
            occupation=await memory.status.get("occupation") or "",
            relationship_score=relationships.get(target, 50),
            intention=step.get("intention", ""),
            emotion_types=await memory.status.get("emotion_types"),
            thought=await memory.status.get("thought"),
            chat_history=(
                chat_histories.get(target, "")
                if isinstance(chat_histories, dict)
                else ""
            ),
            discussion_constraint=discussion_constraint,
        )

        return format_prompt.to_dialog()  # type:ignore


class SocialNoneBlock(Block):
    """
    NoneBlock
    """

    def __init__(self, llm: LLM, memory: Memory):
        super().__init__("NoneBlock", llm=llm, memory=memory)
        self.description = "Handle all other cases"
        self.guidance_prompt = FormatPrompt(template=TIME_ESTIMATE_PROMPT)

    async def forward(self, step, context):  # type:ignore
        """Executes default behavior when no specific block matches the intention.

        Args:
            step: Current workflow step with 'intention' and other metadata.
            context: Additional execution context (e.g., agent's plan).

        Returns:
            A result dictionary indicating success/failure, time consumed, and execution details.
        """
        self.guidance_prompt.format(
            plan=context["plan"],
            intention=step["intention"],
            emotion_types=await self.memory.status.get("emotion_types"),
        )
        result = await self.llm.atext_request(
            self.guidance_prompt.to_dialog(), response_format={"type": "json_object"}
        )
        result = clean_json_response(result)  # type:ignore
        try:
            result = json.loads(result)
            node_id = await self.memory.stream.add_social(
                description=f"I {step['intention']}"
            )
            return {
                "success": True,
                "evaluation": f'Finished {step["intention"]}',
                "consumed_time": result["time"],
                "node_id": node_id,
            }
        except Exception as e:
            logger.warning(
                f"Error occurred while parsing the evaluation response: {e}, original result: {result}"
            )
            node_id = await self.memory.stream.add_social(
                description=f"I failed to execute {step['intention']}"
            )
            return {
                "success": False,
                "evaluation": f'Failed to execute {step["intention"]}',
                "consumed_time": 5,
                "node_id": node_id,
            }


class FindPersonBlock(Block):
    """
    Block for selecting an appropriate agent to socialize with based on relationship strength and context.
    """

    def __init__(self, llm: LLM, memory: Memory, simulator: Simulator):
        super().__init__("FindPersonBlock", llm=llm, memory=memory, simulator=simulator)
        self.description = "Find a suitable person to socialize with"

        self.prompt = """
        Based on the following information, help me select the most suitable friend to interact with:

        1. Your Profile:
           - Gender: {gender}
           - Education: {education}
           - Personality: {personality}
           - Occupation: {occupation}

        2. Your Current Intention: {intention}

        3. Your Current Emotion: {emotion_types}

        4. Your Current Thought: {thought}

        5. Your Friends List (shown as index-to-relationship pairs):
           {friend_info}
           Note: For each friend, the relationship strength (0-100) indicates how close we are

        Please analyze and select:
        1. The most appropriate friend based on relationship strength and my current intention
        2. Whether we should meet online or offline

        Requirements:
        - You must respond in this exact format: [mode, friend_index]
        - mode must be either 'online' or 'offline'
        - friend_index must be an integer representing the friend's position in the list (starting from 0)
        
        Example valid outputs:
        ['online', 0]  - means meet the first friend online
        ['offline', 2] - means meet the third friend offline
        """

    async def forward(  # type:ignore
        self, step: dict[str, Any], context: Optional[dict] = None
    ) -> dict[str, Any]:
        """Identifies a target agent and interaction mode (online/offline).

        Args:
            step: Workflow step containing intention and context.
            context: Additional execution context (may store selected target).

        Returns:
            Result dict with target agent, interaction mode, and execution status.
        """
        try:
            # Get friends list and relationship strength
            friends = await self.memory.status.get("friends") or []
            relationships = await self.memory.status.get("relationships") or {}

            if not friends:
                node_id = await self.memory.stream.add_social(
                    description=f"I can't find any friends to contact with."
                )
                return {
                    "success": False,
                    "evaluation": "No friends found in social network",
                    "consumed_time": 5,
                    "node_id": node_id,
                }

            # Create a list of friends with all information
            friend_info = []
            index_to_id = {}

            for i, friend_id in enumerate(friends):
                relationship_strength = relationships.get(friend_id, 0)
                friend_info.append(
                    {"index": i, "relationship_strength": relationship_strength}
                )
                index_to_id[i] = friend_id

            # Format friend information for easier reading
            formatted_friend_info = {
                i: {"relationship_strength": info["relationship_strength"]}
                for i, info in enumerate(friend_info)
            }

            # Format the prompt
            formatted_prompt = FormatPrompt(self.prompt)
            formatted_prompt.format(
                gender=str(await self.memory.status.get("gender")),
                education=str(await self.memory.status.get("education")),
                personality=str(await self.memory.status.get("personality")),
                occupation=str(await self.memory.status.get("occupation")),
                intention=str(step.get("intention", "socialize")),
                emotion_types=str(await self.memory.status.get("emotion_types")),
                thought=str(await self.memory.status.get("thought")),
                friend_info=str(formatted_friend_info),
            )

            # Get LLM response
            response = await self.llm.atext_request(
                formatted_prompt.to_dialog(), timeout=300
            )

            try:
                # Parse the response
                mode, friend_index = eval(response)  # type:ignore

                # Validate the response format
                if not isinstance(mode, str) or mode not in ["online", "offline"]:
                    raise ValueError("Invalid mode")
                if (
                    not isinstance(friend_index, int)
                    or friend_index not in index_to_id
                ):
                    raise ValueError("Invalid friend index")

                # Convert index to ID
                target = index_to_id[friend_index]
                context["target"] = target  # type:ignore
            except Exception as e:
                # If parsing fails, select the friend with the strongest relationship as the default option
                target = (
                    max(relationships.items(), key=lambda x: x[1])[0]
                    if relationships
                    else friends[0]
                )
                mode = "online"

            node_id = await self.memory.stream.add_social(
                description=f"I selected the friend {target} for {mode} interaction"
            )
            return {
                "success": True,
                "evaluation": f"Selected friend {target} for {mode} interaction",
                "consumed_time": 15,
                "mode": mode,
                "target": target,
                "node_id": node_id,
            }

        except Exception as e:
            node_id = await self.memory.stream.add_social(
                description=f"I can't find any friends to socialize with."
            )
            return {
                "success": False,
                "evaluation": f"Error in finding person: {str(e)}",
                "consumed_time": 5,
                "node_id": node_id,
            }


class MessageBlock(Block):
    """Generate and send messages"""

    def __init__(self, agent, llm: LLM, memory: Memory, simulator: Simulator):
        super().__init__("MessageBlock", llm=llm, memory=memory, simulator=simulator)
        self.agent = agent
        self.description = "Send a message to someone"
        self.find_person_block = FindPersonBlock(llm, memory, simulator)

        # configurable fields
        self.default_message_template = """
        As a {gender} {occupation} with {education} education and {personality} personality,
        generate a message for a friend (relationship strength: {relationship_score}/100)
        about {intention}.

        Your current emotion: {emotion_types}
        Your current thought: {thought}
        
        Previous chat history:
        {chat_history}
        
        Generate a natural and contextually appropriate message.
        Keep it under 100 characters.
        The message should reflect my personality and background.
        {discussion_constraint}
        """

        self.prompt_manager = MessagePromptManager()

    def _serialize_message(self, message: str, propagation_count: int) -> str:
        """Serializes a message into a JSON string for transmission.

        Args:
            message: Plain text message content.
            propagation_count: Number of times the message can be forwarded.

        Returns:
            JSON string with message and metadata.
        """
        try:
            return json.dumps(
                {"content": message, "propagation_count": propagation_count},
                ensure_ascii=False,
            )
        except Exception as e:
            logger.warning(f"Error serializing message: {e}")
            return message

    async def forward(  # type:ignore
        self, step: dict[str, Any], context: Optional[dict] = None
    ) -> dict[str, Any]:
        """Generates a message, sends it to the target, and updates chat history.

        Args:
            step: Workflow step containing message intention.
            context: Execution context (may contain pre-selected target).

        Returns:
            Result dict with message content, target, and execution status.
        """
        try:
            # Get target from context or find one
            target = context.get("target") if context else None
            if not target:
                result = await self.find_person_block.forward(step, context)
                if not result["success"]:
                    return {
                        "success": False,
                        "evaluation": "Could not find target for message",
                        "consumed_time": 5,
                        "node_id": result["node_id"],
                    }
                target = result["target"]

            # Get formatted prompt using prompt manager
            formatted_prompt = await self.prompt_manager.get_prompt(
                self.memory, step, target, self.default_message_template
            )

            # Generate message
            message = await self.llm.atext_request(formatted_prompt, timeout=300)
            if not message:
                message = "Hello! How are you?"

            # Update chat history with proper format
            chat_histories = await self.memory.status.get("chat_histories") or {}
            if not isinstance(chat_histories, dict):
                chat_histories = {}
            if target not in chat_histories:
                chat_histories[target] = ""
            elif len(chat_histories[target]) > 0:
                chat_histories[target] += ", "
            chat_histories[target] += f"me: {message}"

            await self.memory.status.update("chat_histories", chat_histories)

            # Send message
            serialized_message = self._serialize_message(message, 1)
            await self.agent.send_message_to_agent(target, serialized_message)
            node_id = await self.memory.stream.add_social(
                description=f"I sent a message to {target}: {message}"
            )
            return {
                "success": True,
                "evaluation": f"Sent message to {target}: {message}",
                "consumed_time": 10,
                "message": message,
                "target": target,
                "node_id": node_id,
            }

        except Exception as e:
            node_id = await self.memory.stream.add_social(
                description=f"I can't send a message to {target}"  # type:ignore
            )
            return {
                "success": False,
                "evaluation": f"Error in sending message: {str(e)}",
                "consumed_time": 5,
                "node_id": node_id,
            }


class SocialBlock(Block):
    """
    Orchestrates social interactions by dispatching to appropriate sub-blocks.
    """

    find_person_block: FindPersonBlock
    message_block: MessageBlock
    noneblock: SocialNoneBlock

    def __init__(self, agent, llm: LLM, memory: Memory, simulator: Simulator):
        super().__init__("SocialBlock", llm=llm, memory=memory, simulator=simulator)
        self.find_person_block = FindPersonBlock(llm, memory, simulator)
        self.message_block = MessageBlock(agent, llm, memory, simulator)
        self.noneblock = SocialNoneBlock(llm, memory)
        self.dispatcher = BlockDispatcher(llm)

        self.trigger_time = 0
        self.token_consumption = 0

        self.dispatcher.register_blocks(
            [self.find_person_block, self.message_block, self.noneblock]
        )

    async def forward(  # type:ignore
        self, step: dict[str, Any], context: Optional[dict] = None
    ) -> dict[str, Any]:
        """Main entry point for social interactions. Dispatches to sub-blocks based on context.

        Args:
            step: Workflow step containing intention and metadata.
            context: Additional execution context.

        Returns:
            Result dict from the executed sub-block.
        """
        try:
            self.trigger_time += 1
            consumption_start = (
                self.llm.prompt_tokens_used + self.llm.completion_tokens_used
            )

            # Select the appropriate sub-block using dispatcher
            selected_block = await self.dispatcher.dispatch(step)

            # Execute the selected sub-block and get the result
            result = await selected_block.forward(step, context)  # type:ignore

            return result

        except Exception as e:
            return {
                "success": False,
                "evaluation": "Failed to complete social interaction with default behavior",
                "consumed_time": 15,
                "node_id": None,
            }
