from akgentic.core import ActorAddress, ActorSystem, Akgent
from akgentic.core.messages import Message


# Define a simple message class
class EchoMessage(Message):
    content: str


# Define a simple agent that echoes messages
class EchoAgent(Akgent):
    def receiveMsg_EchoMessage(self, message: EchoMessage, sender: ActorAddress) -> None:
        print(f"EchoAgent received: {message.content}")


# Create local actor system
system = ActorSystem()

# Create an agent instance
agent = system.createActor(EchoAgent)

# Send a message to the agent
system.tell(agent, EchoMessage(content="Hello, Akgentic!"))

system.shutdown()
