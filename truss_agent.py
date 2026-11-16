"""
LangChain Agent for Truss Analysis
Uses DeepSeek LLM with custom tools for AutoCAD and structural analysis
"""

import os
from typing import List, Tuple, Optional
from langchain_deepseek import ChatDeepSeek
from langchain_core.messages import HumanMessage, AIMessage

from truss_tools import ALL_TOOLS

# The DeepSeek API key is read from the DEEPSEEK_API_KEY environment variable (never hard-code it).

# System prompt for the truss analysis assistant
SYSTEM_PROMPT = """You are a helpful structural engineering assistant specialized in truss analysis.
You help users interact with AutoCAD and perform structural analysis on 3D trusses.

Your capabilities:
1. Extract truss data from AutoCAD (joints, elements, loads, boundary conditions)
2. Draw trusses in AutoCAD at specified locations
3. Run 3D truss analysis using OpenSees (with geometric and material nonlinearity)
4. Answer questions about truss data and analysis results
5. Generate plots of analysis results (displacement distribution, stress distribution, forces)
6. Assess analysis results against Eurocode 3 (EN 1993-1-1) using RAG retrieval from
   the standard and worked examples. This checks:
   - Tension resistance (§6.2.3): N_Ed ≤ N_pl,Rd = A·f_y/γ_M0
   - Compression resistance (§6.2.4): N_Ed ≤ N_c,Rd
   - Buckling resistance (§6.3.1): N_Ed ≤ N_b,Rd = χ·A·f_y/γ_M1
   - Serviceability deflection limits (§7.2)
   - Member slenderness ratios

Guidelines:
- Be concise and professional in your responses
- When presenting numerical results, use appropriate units (mm for displacement, MPa for stress)
- If the user asks about specific elements or joints, use the query tool to get the data
- Always confirm successful operations with relevant summary information
- If an operation fails, explain the likely cause and suggest solutions
- When the user asks to visualize, plot, or graph results, use the plot_analysis_results tool
- Available plot types: "displacement", "stress", "force"
- When presenting Eurocode compliance results, always:
  * State the governing EC3 clause and equation number
  * Show the numerical unity check (N_Ed / N_Rd ≤ 1.0)
  * Flag any members that FAIL the check
  * Reference the source document (EN 1993-1-1 or worked example)

7. Optimize truss section sizes to minimize total mass using pymoo.
   Algorithms: PSO (default), GA, DE, or ALL (runs all three, picks best).
   Supports discrete optimization (selecting from a CHS catalog like CHS_eurocode.csv)
   and continuous optimization (optimizing cross-section area directly).
   Constraints enforced: tension, compression, buckling, slenderness, and deflection
   per Eurocode 3. Returns a report with optimal sections per group and mass reduction.
   Saves optimized truss to truss_optimized.txt for drawing in AutoCAD.

Common workflows:
1. First extract truss from AutoCAD, then run analysis
2. After analysis, user can query results (stresses, displacements)
3. User can request plots to visualize results
4. User can assess results against Eurocode 3 for compliance
5. User can redraw the truss (original or optimized) at a new location
6. User can optimize section sizes: "optimize the truss" or "minimize the weight"
   - Use mode="discrete" (default) to pick from CHS catalog
   - Use mode="continuous" if user explicitly requests continuous optimization
   - algorithm="PSO" (default), "GA", "DE", or "ALL" (compares all three)
   - Adjust pop_size and n_gen for convergence (higher = slower but better)
"""


class TrussAgent:
    """Agent for interacting with truss analysis tools using DeepSeek LLM."""

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the truss agent.

        Args:
            api_key: DeepSeek API key. If not provided, uses DEEPSEEK_API_KEY env var.
        """
        if api_key:
            os.environ["DEEPSEEK_API_KEY"] = api_key
        if not os.environ.get("DEEPSEEK_API_KEY"):
            raise RuntimeError("DEEPSEEK_API_KEY is not set. Export it before starting BIM-ATA "
                               "(see README.md).")

        # Initialize LLM with tools bound
        self.llm = ChatDeepSeek(
            model="deepseek-chat",
            temperature=0.1,
        )

        # Bind tools to the LLM
        self.llm_with_tools = self.llm.bind_tools(ALL_TOOLS)

        # Create a tools dictionary for easy lookup
        self.tools_dict = {tool.name: tool for tool in ALL_TOOLS}

        # Chat history for multi-turn conversations
        self.chat_history: List = []

    def _execute_tool(self, tool_name: str, tool_args: dict) -> str:
        """Execute a tool and return the result."""
        if tool_name in self.tools_dict:
            tool = self.tools_dict[tool_name]
            try:
                result = tool.invoke(tool_args)
                return str(result)
            except Exception as e:
                return f"Error executing {tool_name}: {str(e)}"
        else:
            return f"Unknown tool: {tool_name}"

    def chat(self, user_message: str) -> str:
        """
        Send a message to the agent and get a response.

        Args:
            user_message: The user's input message

        Returns:
            The agent's response as a string
        """
        try:
            # Build messages list with system prompt and history
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]

            # Add chat history
            for msg in self.chat_history:
                if isinstance(msg, HumanMessage):
                    messages.append({"role": "user", "content": msg.content})
                elif isinstance(msg, AIMessage):
                    messages.append({"role": "assistant", "content": msg.content})

            # Add current user message
            messages.append({"role": "user", "content": user_message})

            # Get LLM response with potential tool calls
            response = self.llm_with_tools.invoke(messages)

            # Check if there are tool calls
            if hasattr(response, 'tool_calls') and response.tool_calls:
                tool_results = []

                for tool_call in response.tool_calls:
                    tool_name = tool_call['name']
                    tool_args = tool_call['args']

                    print(f"Calling tool: {tool_name} with args: {tool_args}")
                    result = self._execute_tool(tool_name, tool_args)
                    tool_results.append(f"[{tool_name}]: {result}")

                # Combine tool results
                tool_output = "\n\n".join(tool_results)

                # Get final response from LLM incorporating tool results
                messages.append({"role": "assistant", "content": f"Tool results:\n{tool_output}"})
                messages.append({"role": "user", "content": "Please summarize the results in a helpful way."})

                final_response = self.llm.invoke(messages)
                response_text = final_response.content
            else:
                # No tool calls, use direct response
                response_text = response.content

            # Update chat history
            self.chat_history.append(HumanMessage(content=user_message))
            self.chat_history.append(AIMessage(content=response_text))

            # Keep chat history manageable (last 20 messages)
            if len(self.chat_history) > 20:
                self.chat_history = self.chat_history[-20:]

            return response_text

        except Exception as e:
            error_msg = f"Error processing request: {str(e)}"
            print(error_msg)
            return error_msg

    def clear_history(self):
        """Clear the chat history."""
        self.chat_history = []

    def get_history(self) -> List[Tuple[str, str]]:
        """
        Get chat history as list of (role, content) tuples.

        Returns:
            List of (role, content) tuples
        """
        history = []
        for msg in self.chat_history:
            if isinstance(msg, HumanMessage):
                history.append(("user", msg.content))
            elif isinstance(msg, AIMessage):
                history.append(("assistant", msg.content))
        return history


def create_agent(api_key: Optional[str] = None) -> TrussAgent:
    """
    Factory function to create a TrussAgent instance.

    Args:
        api_key: Optional DeepSeek API key

    Returns:
        TrussAgent instance
    """
    return TrussAgent(api_key=api_key)


# Simple test
if __name__ == "__main__":
    print("Testing Truss Agent...")
    print("-" * 50)

    agent = create_agent()

    # Test conversation
    test_messages = [
        "Hello! What can you help me with?",
        "Check if AutoCAD is connected",
    ]

    for msg in test_messages:
        print(f"\nUser: {msg}")
        response = agent.chat(msg)
        print(f"Agent: {response}")
        print("-" * 50)
