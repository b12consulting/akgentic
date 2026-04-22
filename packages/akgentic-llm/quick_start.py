from akgentic.llm import ModelConfig, ReactAgent, ReactAgentConfig

# Configure agent
config = ReactAgentConfig(model_cfg=ModelConfig(provider="openai", model="gpt-4.1"))

# Create agent
agent = ReactAgent(config=config)

# Run agent
result = agent.run_sync("Hello, how are you?")
print(result)
