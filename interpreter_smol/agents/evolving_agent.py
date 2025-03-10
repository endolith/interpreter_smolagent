"""
Self-evolving agent system that can deploy other agents.
"""

import os
import sys
import json
import time
import importlib
from typing import Dict, List, Optional, Any, Union

import yaml
from pathlib import Path
from smolagents import CodeAgent, tool
from smolagents.default_tools import DuckDuckGoSearchTool, VisitWebpageTool
from interpreter_smol.tools import EnhancedPythonInterpreter

class EvolvingAgentSystem:
    """
    A self-evolving agent system that can create and deploy other agents.
    """
    
    def __init__(
        self,
        model_type: str = "gemini",
        model_id: Optional[str] = None,
        api_key: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        verbose: bool = True,
        workspace_dir: str = "./agent_workspace"
    ):
        """Initialize the evolving agent system."""
        from interpreter_smol.core.interpreter import Interpreter
        
        # Create the workspace directory if it doesn't exist
        if not os.path.exists(workspace_dir):
            os.makedirs(workspace_dir)
        
        self.workspace_dir = workspace_dir
        self.verbose = verbose
        
        # Load custom system prompt
        custom_system_prompt = self._load_custom_system_prompt()
        
        # Initialize the base interpreter
        self.interpreter = Interpreter(
            model=model_type,
            model_id=model_id,
            api_key=api_key,
            tools=["enhanced_python", "web_search", "visit_webpage"],
            temperature=temperature,
            max_tokens=max_tokens,
            verbose=verbose
        )
        
        # Set custom prompt templates if available
        if custom_system_prompt and hasattr(self.interpreter.agent, 'system_prompt'):
            # Update all prompt templates
            self.interpreter.agent.system_prompt = custom_system_prompt.get("system_prompt", "")
            if hasattr(self.interpreter.agent, 'prompt_templates'):
                self.interpreter.agent.prompt_templates = custom_system_prompt
            if verbose:
                print("Using custom prompt templates for evolving agent system.")
        
        # Initialize the agent registry
        self.agent_registry = {}
        self._load_existing_agents()
        
        # Add agent management tools
        self._add_agent_management_tools()
    
    def _load_existing_agents(self):
        """Load any existing agents from the workspace directory."""
        registry_path = os.path.join(self.workspace_dir, "agent_registry.json")
        if os.path.exists(registry_path):
            try:
                with open(registry_path, 'r') as f:
                    self.agent_registry = json.load(f)
                    if self.verbose:
                        print(f"Loaded {len(self.agent_registry)} existing agents from registry.")
            except Exception as e:
                print(f"Error loading agent registry: {e}")
    
    def _save_agent_registry(self):
        """Save the agent registry to disk."""
        registry_path = os.path.join(self.workspace_dir, "agent_registry.json")
        try:
            with open(registry_path, 'w') as f:
                json.dump(self.agent_registry, f, indent=2)
        except Exception as e:
            print(f"Error saving agent registry: {e}")
    
    def _load_custom_system_prompt(self):
        """Load the custom prompts for the evolving agent system."""
        prompt_path = Path(__file__).parent.parent / "prompts" / "evolving_agent.yaml"
        if prompt_path.exists():
            try:
                with open(prompt_path, 'r') as f:
                    # Load entire YAML file as prompt templates
                    prompt_templates = yaml.safe_load(f)
                    
                    # Convert the loaded data into prompt templates dict
                    # This matches the structure expected by SmolaGents
                    templates = {
                        "system_prompt": prompt_templates.get("system_prompt", ""),
                        "planning": prompt_templates.get("planning", {}),
                        "managed_agent": prompt_templates.get("managed_agent", {}),
                        "final_answer": prompt_templates.get("final_answer", {})
                    }
                    return templates
            except Exception as e:
                print(f"Error loading custom prompts: {e}")
        return None
    
    def _add_agent_management_tools(self):
        """Add tools for agent management to the interpreter."""
        
        @tool
        def create_agent(name: str, description: str, tools: List[str], code: str) -> str:
            """
            Create a new agent with specified configuration and save it to the workspace.

            Args:
                name: The name of the agent.
                description: a description of what the agent does.
                tools: List of tool names the agent should have access to.
                code: Python code that defines the agent's custom tools and behavior.

            Returns:
                str: Success message or error.

            NOTE: Typically, your code should define something like:

                def run(task, tools):
                    py_tool = tools["python_interpreter"]
                    return py_tool(code=f"print('Agent running task: {task}')")

            so the agent can reference python_interpreter from the provided `tools` dict.
            """
            try:
                # Create agent directory
                agent_dir = os.path.join(self.workspace_dir, name)
                if not os.path.exists(agent_dir):
                    os.makedirs(agent_dir)

                # Save the agent code
                code_path = os.path.join(agent_dir, f"{name}_agent.py")
                with open(code_path, 'w') as f:
                    f.write(code)

                # Register the agent
                self.agent_registry[name] = {
                    "name": name,
                    "description": description,
                    "tools": tools,
                    "code_path": code_path,
                    "created_at": str(time.time())
                }

                # Save the registry
                self._save_agent_registry()

                return f"Agent '{name}' created successfully. Code saved to {code_path}"
            except Exception as e:
                return f"Error creating agent: {str(e)}"

        @tool
        def list_agents() -> str:
            """
            list agents in the registry.
            """
            return json.dumps(self.agent_registry, indent=2)

        @tool
        def run_agent(name: str, task: str) -> str:
            """
            Run a specific agent on a given task.

            Args:
                name: The name of the agent to run.
                task: The task to run the agent on.

            Returns:
                str: The result from the agent.
            """
            if name not in self.agent_registry:
                return f"Error: Agent '{name}' not found in registry."

            try:
                # Import the agent module
                agent_info = self.agent_registry[name]
                sys.path.append(os.path.dirname(agent_info["code_path"]))
                module_name = os.path.basename(agent_info["code_path"]).replace(".py", "")

                # Use importlib to load the module
                spec = importlib.util.spec_from_file_location(module_name, agent_info["code_path"])
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # Prepare the python_interpreter tool
                # This is where we actually create an interpreter instance to pass to the agent.
                python_tool = EnhancedPythonInterpreter()

                # Run the agent
                if hasattr(module, "run"):
                    # The agent code can define run(task, tools)
                    # We'll pass the python_interpreter in a dict
                    # If the agent code only has run(task) (one param),
                    # it will fail. So we recommend the agent code is run(task, tools).
                    result = module.run(task, {"python_interpreter": python_tool})
                    return f"Agent '{name}' result: {result}"
                else:
                    return f"Error: Agent '{name}' does not have a 'run' function."
            except Exception as e:
                return f"Error running agent '{name}': {str(e)}"

        @tool
        def delete_agent(name: str) -> str:
            """
            Delete an agent from the registry.

            Args:
                name: The name of the agent to delete.

            Returns:
                str: Success message or error.
            """
            if name not in self.agent_registry:
                return f"Error: Agent '{name}' not found in registry."

            try:
                # Remove the agent directory
                agent_dir = os.path.dirname(self.agent_registry[name]["code_path"])
                if os.path.exists(agent_dir):
                    import shutil
                    shutil.rmtree(agent_dir)

                # Remove from registry
                del self.agent_registry[name]
                self._save_agent_registry()

                return f"Agent '{name}' deleted successfully."
            except Exception as e:
                return f"Error deleting agent '{name}': {str(e)}"
                
        # Add tools to the agent
        tools = [
            create_agent,
            list_agents,
            run_agent,
            delete_agent,
        ]
        
        try:
            if hasattr(self.interpreter.agent, 'tools'):
                if isinstance(self.interpreter.agent.tools, list):
                    self.interpreter.agent.tools.extend(tools)
                elif isinstance(self.interpreter.agent.tools, dict):
                    self.interpreter.agent.tools.update({
                        tool.name: tool for tool in tools
                    })
                else:
                    self.interpreter.agent.tools = tools
                if self.verbose:
                    print(f"Added {len(tools)} agent management tools.")
            else:
                print("Warning: Agent does not have tools attribute.")
        except Exception as e:
            print(f"Warning: Could not add agent management tools: {e}")
            
    def run(self, prompt: str) -> str:
        """Run a prompt through the interpreter."""
        return self.interpreter.run(prompt)

def main():
    """Command line interface for the evolving agent system."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Evolving Agent System - Create and deploy agents dynamically")
    parser.add_argument("prompt", nargs="?", help="The prompt to run")
    parser.add_argument("--model", "-m", default="gemini", 
                        choices=["gemini", "openai", "anthropic", "hf"],
                        help="Model provider to use")
    parser.add_argument("--model-id", default=None, 
                        help="Specific model ID (defaults to best model for provider)")
    parser.add_argument("--api-key", "-k", default=None, 
                        help="API key for the model provider")
    parser.add_argument("--workspace", "-w", default="./agent_workspace",
                        help="Directory to store agent code and registry")
    parser.add_argument("--temperature", "-t", type=float, default=0.7,
                        help="Temperature for generation")
    parser.add_argument("--max-tokens", type=int, default=8192,
                        help="Maximum tokens in response")
    parser.add_argument("-i", "--interactive", action="store_true", 
                        help="Start in interactive mode")
    parser.add_argument("-v", "--verbose", action="store_true", 
                        help="Enable verbose output")

    args = parser.parse_args()
    
    # Set up environment variables for API keys if provided
    if args.api_key:
        if args.model.lower() == "gemini":
            os.environ["GOOGLE_API_KEY"] = args.api_key
        elif args.model.lower() == "openai":
            os.environ["OPENAI_API_KEY"] = args.api_key
        elif args.model.lower() == "anthropic":
            os.environ["ANTHROPIC_API_KEY"] = args.api_key
        elif args.model.lower() == "hf":
            os.environ["HF_API_TOKEN"] = args.api_key
    
    try:
        # Initialize the evolving agent system
        system = EvolvingAgentSystem(
            model_type=args.model,
            model_id=args.model_id,
            api_key=args.api_key,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            verbose=args.verbose,
            workspace_dir=args.workspace
        )
        
        # Run in appropriate mode
        if args.interactive or not args.prompt:
            if hasattr(system.interpreter, "chat"):
                system.interpreter.chat(args.prompt)  # Call chat method directly on interpreter
            else:
                system.run(args.prompt) # Default to run
        else:
            system.run(args.prompt)
            
    except ImportError as e:
        print(f"Error: {e}")
        if "google-genai" in str(e):
            print("Install Google GenAI package with: pip install google-genai")
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)


if __name__ == "__main__":
    main()
