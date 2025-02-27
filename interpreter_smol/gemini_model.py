"""
gemini_model.py - Google Generative AI (Gemini) model adapter for SmolaGents
using the new Google GenAI SDK (google-genai v1.0)
"""

import os
import uuid
import logging
from typing import Any, Dict, List, Optional, Union

from smolagents.models import Model, ChatMessage, ChatMessageToolCall, ChatMessageToolCallDefinition, MessageRole

# Configure logging
logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    logger.warning("Google GenAI package not installed. Install with: pip install google-genai")


class GeminiModel(Model):
    """
    Adapter for Google's Generative AI models (Gemini) to work with SmolaGents.
    
    This model adapter allows using Google's Gemini models with the SmolaGents framework,
    providing proper handling of tool calls, message formatting, and response parsing.
    Uses the new google-genai v1.0 SDK.
    
    Args:
        model_id (str, optional): The Gemini model to use. Defaults to "gemini-2.0-flash".
        api_key (str, optional): API key for Gemini. If not provided, will look for GOOGLE_API_KEY environment variable.
        temperature (float, optional): Controls randomness in generation. Defaults to 0.7.
        max_tokens (int, optional): Maximum tokens to generate. Defaults to 8192.
        **kwargs: Additional arguments passed to the base Model class.
    
    Example:
        ```python
        from gemini_model import GeminiModel
        from smolagents import CodeAgent
        
        model = GeminiModel(model_id="gemini-2.0-flash")
        agent = CodeAgent(tools=[], model=model)
        agent.run("Write a function to calculate prime numbers")
        ```
    """
    
    def __init__(
        self,
        model_id: str = "gemini-2.0-flash",
        api_key: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        **kwargs
    ):
        if not GEMINI_AVAILABLE:
            raise ImportError("Google GenAI package not installed. Install with: pip install google-genai")
        
        super().__init__(**kwargs)
        self.model_id = model_id
        self.temperature = temperature
        self.max_tokens = max_tokens
        
        # Initialize the Gemini API client
        if api_key is None:
            api_key = os.getenv("GOOGLE_API_KEY")

        if not api_key:
            raise ValueError("API key is required. Set GOOGLE_API_KEY environment variable or pass api_key.")
            
        self.client = genai.Client(api_key=api_key)
        self.last_input_token_count = 0
        self.last_output_token_count = 0
    
    def __call__(
        self,
        messages: List[Dict[str, str]],
        stop_sequences: Optional[List[str]] = None,
        grammar: Optional[str] = None,
        tools_to_call_from: Optional[List] = None,
        **kwargs
    ) -> ChatMessage:
        """
        Process the input messages and return the model's response.
        
        Args:
            messages (List[Dict[str, str]]): List of messages to send to the model.
            stop_sequences (Optional[List[str]], optional): Sequences to stop generation. Defaults to None.
            grammar (Optional[str], optional): Grammar specification for the output. Defaults to None.
            tools_to_call_from (Optional[List], optional): Tools the model can use. Defaults to None.
            **kwargs: Additional parameters to pass to the model.
            
        Returns:
            ChatMessage: The model's response as a ChatMessage object.
        """
        # Check if streaming is requested
        stream = kwargs.get("stream", False)
        
        # Convert messages to Gemini's format
        gemini_messages = self._convert_messages_to_gemini_format(messages)
        
        # Set up config with optional parameters
        config = types.GenerateContentConfig(
            temperature=kwargs.get("temperature", self.temperature),
            top_p=kwargs.get("top_p", 0.95),
            top_k=kwargs.get("top_k", 40),
            max_output_tokens=kwargs.get("max_tokens", self.max_tokens),
            stop_sequences=stop_sequences
        )
        
        # Handle tools if provided
        tool_config = self._prepare_tool_config(tools_to_call_from) if tools_to_call_from else None
        
        try:
            if stream:
                # Use streaming API
                return self._handle_streaming(
                    gemini_messages=gemini_messages,
                    config=config,
                    tool_config=tool_config,
                    tools_to_call_from=tools_to_call_from
                )
            
            # Standard non-streaming generation
            if tool_config:
                # Generate response with tools
                response = self.client.models.generate_content(
                    model=self.model_id,
                    contents=gemini_messages,
                    config=config,
                    tools=tool_config,
                )
            else:
                # Standard generation without tools
                response = self.client.models.generate_content(
                    model=self.model_id,
                    contents=gemini_messages,
                    config=config,
                )
            
            # Process and return response
            return self._process_gemini_response(response, tools_to_call_from)
        
        except Exception as e:
            logger.error(f"Error generating content: {str(e)}")
            # Return empty response in case of error
            return ChatMessage(
                role="assistant",
                content=f"Error generating content: {str(e)}",
                tool_calls=None,
                raw=None
            )
    
    def _convert_messages_to_gemini_format(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert SmolaGents messages to Gemini's format."""
        gemini_messages = []
        
        for message in messages:
            # Map SmolaGents roles to Gemini roles
            role = "user" if message["role"] == MessageRole.USER else "model"
            
            if isinstance(message["content"], list):
                # Handle multimodal content
                parts = []
                for content_item in message["content"]:
                    if content_item["type"] == "text":
                        parts.append({"text": content_item["text"]})
                    elif content_item["type"] == "image_url":
                        url = content_item["image_url"]["url"]
                        if url.startswith("data:image"):
                            # Handle base64 encoded images
                            parts.append({
                                "inline_data": {
                                    "mime_type": "image/jpeg",
                                    "data": url.split(",")[1]
                                }
                            })
                        else:
                            parts.append({"image_url": url})
                
                gemini_messages.append({"role": role, "parts": parts})
            else:
                # Simple text content
                gemini_messages.append({
                    "role": role,
                    "parts": [{"text": message["content"]}]
                })
        
        return gemini_messages
    
    def _prepare_tool_config(self, tools_to_call_from):
        """Prepare tool configurations for Gemini using the new SDK format."""
        tools = []
        
        for tool in tools_to_call_from:
            function_declaration = types.FunctionDeclaration(
                name=tool.name,
                description=tool.description,
                parameters=types.Schema(
                    type="OBJECT",
                    properties={},
                    required=[]
                )
            )
            
            # Add parameters
            for param_name, param_details in tool.inputs.items():
                param_type = param_details["type"]
                if param_type == "any":
                    param_type = "string"  # Gemini doesn't support 'any' type
                
                function_declaration.parameters.properties[param_name] = {
                    "type": param_type,
                    "description": param_details.get("description", "")
                }
                
                # Add required parameters
                if not param_details.get("nullable", False):
                    function_declaration.parameters.required.append(param_name)
            
            tools.append(types.Tool(function_declarations=[function_declaration]))
        
        return tools
    
    def _handle_streaming(self, gemini_messages, config, tool_config, tools_to_call_from):
        """Handle streaming responses from Gemini."""
        try:
            # Use the streaming API
            stream_response = self.client.models.generate_content_stream(
                model=self.model_id,
                contents=gemini_messages,
                config=config,
                tools=tool_config,
            )
            
            # Accumulate the complete response
            content = ""
            tool_calls = None
            raw_chunks = []
            
            for chunk in stream_response:
                raw_chunks.append(chunk)
                
                # Extract text content from chunk
                if hasattr(chunk, "text"):
                    content += chunk.text
                elif hasattr(chunk, "candidates") and chunk.candidates:
                    for candidate in chunk.candidates:
                        if hasattr(candidate, "content") and candidate.content:
                            if hasattr(candidate.content, "parts"):
                                for part in candidate.content.parts:
                                    if hasattr(part, "text") and part.text:
                                        content += part.text
                
                # Check for function calls in chunk
                if tools_to_call_from and hasattr(chunk, "candidates") and chunk.candidates:
                    for candidate in chunk.candidates:
                        if hasattr(candidate, "content") and candidate.content:
                            if hasattr(candidate.content, "parts"):
                                for part in candidate.content.parts:
                                    if hasattr(part, "function_call"):
                                        if tool_calls is None:
                                            tool_calls = []
                                        
                                        # Create a tool call from the function call
                                        function_call = part.function_call
                                        tool_calls.append(
                                            ChatMessageToolCall(
                                                id=str(uuid.uuid4()),
                                                type="function",
                                                function=ChatMessageToolCallDefinition(
                                                    name=function_call.name,
                                                    arguments=function_call.args,
                                                )
                                            )
                                        )
            
            # Create a combined response from the stream
            return ChatMessage(
                role="assistant",
                content=content if not tool_calls else None,
                tool_calls=tool_calls,
                raw=raw_chunks
            )
            
        except Exception as e:
            logger.error(f"Error in streaming: {str(e)}")
            return ChatMessage(
                role="assistant",
                content=f"Error in streaming: {str(e)}",
                tool_calls=None,
                raw=None
            )
    
    def _process_gemini_response(self, response, tools_to_call_from):
        """Process Gemini response into ChatMessage format using the new SDK structure."""
        # Extract token counts if available
        if hasattr(response, "usage_metadata"):
            self.last_input_token_count = getattr(response.usage_metadata, "prompt_token_count", 0)
            self.last_output_token_count = getattr(response.usage_metadata, "candidates_token_count", 0)
        
        # Initialize content and tool_calls
        content = ""
        tool_calls = None
        
        # Extract text content
        if hasattr(response, "text"):
            content = response.text
        elif hasattr(response, "candidates") and response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, "content") and candidate.content:
                if hasattr(candidate.content, "parts"):
                    for part in candidate.content.parts:
                        if hasattr(part, "text") and part.text:
                            content += part.text
        
        # Check for function calls
        if tools_to_call_from and hasattr(response, "candidates") and response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, "content") and candidate.content:
                if hasattr(candidate.content, "parts"):
                    for part in candidate.content.parts:
                        if hasattr(part, "function_call"):
                            if tool_calls is None:
                                tool_calls = []
                            
                            # Create a tool call from the function call
                            function_call = part.function_call
                            tool_calls.append(
                                ChatMessageToolCall(
                                    id=str(uuid.uuid4()),
                                    type="function",
                                    function=ChatMessageToolCallDefinition(
                                        name=function_call.name,
                                        arguments=function_call.args,
                                    )
                                )
                            )
        
        # Create and return a ChatMessage
        return ChatMessage(
            role="assistant",
            content=content if not tool_calls else None,
            tool_calls=tool_calls,
            raw=response
        )

    def to_dict(self) -> Dict:
        """Convert model to dictionary representation."""
        return {
            **super().to_dict(),
            "model_id": self.model_id,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GeminiModel":
        """Create a model instance from a dictionary."""
        return cls(
            model_id=data.get("model_id", "gemini-2.0-flash"),
            temperature=data.get("temperature", 0.7),
            max_tokens=data.get("max_tokens", 8192),
        )