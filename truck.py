import random
import spade
from spade.agent import Agent
from spade.behaviour import FSMBehaviour, State, CyclicBehaviour
from spade.message import Message
import asyncio

TRUCK_STATE_ONE = "RECEIVE_CFP"
TRUCK_STATE_TWO = "SEND_PROPOSAL"
TRUCK_STATE_THREE = "PERFORM_ACTION"

# - Faz sentido ter um CFP a processar pedidos sequencialmente? Acho que não é possível satisfazer mais do que um pedido ao mesmo tempo, um camião ou vai a um sitio ou vai a outro, por isso paralelizar não importa, certo?

class TruckAgent(Agent):
    
    def __init__(self, jid, password):
        super().__init__(jid, password)

        # shared state between behaviours
        self.currentProposal = None  # store currently processed proposal

    class BinStatusReceiver(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=2)
            if msg and msg.metadata.get("type") == "bin_status_update":
                bin_id, fullness, capacity, latitude, longitude = msg.body.strip().split(";")
                fullness = int(fullness)
                capacity = int(capacity)
                latitude = float(latitude)
                longitude = float(longitude)
                # É preciso decidir o que vamos fazer com a informação recebida
                print(f"TRUCK: Received bin update -> {bin_id}: {fullness}/{capacity}/{latitude}/{longitude}")

    class TruckFSMBehaviour(FSMBehaviour):
        # State 1: receive CFP proposal from a bin
        class ReceiveContract(State):
            async def run(self):
                msg = await self.receive(timeout=3)  # timeout after 3 seconds
                if msg:
                    if msg.metadata["performative"] == "cfp":
                        print(f"TRUCK: cFP from {msg.sender}")
                        self.agent.currentProposal = msg  # store the proposal in a queue to be processed by other behaviour
                        self.set_next_state(TRUCK_STATE_TWO)
                    else:
                        print(f"TRUCK: non-CFP message from {msg.sender}")
                        await asyncio.sleep(1)  # wait for 1 seconds
                        self.set_next_state(TRUCK_STATE_ONE) # repeat check for requets
                else:
                    print("TRUCK: No messages received during this check")
                    await asyncio.sleep(1)  # wait for 1 seconds
                    self.set_next_state(TRUCK_STATE_ONE) # repeat check for requets

        class ProcessContract(State):
            async def run(self):
                proposal = self.agent.currentProposal
                if self.agent.decide_accept_proposal(proposal):
                    #reply = Message(to=proposal.sender.jid)
                    reply = Message(to=str(proposal.sender))
                    reply.set_metadata("performative", "propose")
                    reply.body = str(random.randint(0, 100))  # Send a random value as proposal
                    await self.send(reply)
                    print(f"TRUCK: Truck sending proposal to {proposal.sender}")
                    self.set_next_state(TRUCK_STATE_THREE) 
                else:
                    #reply = Message(to=proposal.sender.jid)
                    reply = Message(to=str(proposal.sender))
                    reply.set_metadata("performative", "refuse")
                    await self.send(reply)

                    print(f"TRUCK: Truck rejecting contract from {proposal.sender}")
                    self.set_next_state(TRUCK_STATE_ONE) # process other requests

        # State 2: perform action
        class PerformAction(State):
            async def run(self):
                print("Waiting for bin deicision on proposal...")
                msg = await self.receive(timeout=10)  # wait for a message for 3 seconds

                if (not msg):
                    print("No proposal answer received during action")
                    self.set_next_state(TRUCK_STATE_ONE)
                    return

                if msg.metadata["performative"] == "reject-proposal":
                    print(f"Proposal rejected by {msg.sender}")

                elif (msg.metadata["performative"] == "accept-proposal"):
                    print(f"Proposal accepted by {msg.sender}")

                    # await asyncio.sleep(2)  # Simulate action time
                    
                    print("Action performed, sending result to bin...")

                    # Send the result to the bin
                    #result_msg = Message(to=msg.sender.jid)
                    result_msg = Message(to=str(msg.sender))
                    action_result = random.randint(0, 10) != 0 # simulate success or failure of operation
                    #successful cleaning
                    if action_result:
                        result_msg.set_metadata("performative", "inform-done") 
                        result_msg.body = "Truck cleaned the bin!"
                    #unsucessful cleaning
                    else:
                        result_msg.set_metadata("performative", "failure")
                        result_msg.body = "Truck failed to clean the bin!"

                    await self.send(result_msg)

                else:
                    #print(f"Unexpected message received {msg.metadata['performative']} from {msg.sender.jid}")
                    print(f"Unexpected message received {msg.metadata['performative']} from {msg.sender}")

                self.set_next_state(TRUCK_STATE_ONE)  # return to waiting for proposals

    async def setup(self):
        fsm = self.setupFSMBehaviour()
        self.add_behaviour(fsm)
        self.add_behaviour(self.BinStatusReceiver())

    # Simulation of logic to accept a proposal
    def decide_accept_proposal(self, proposal):
        # if (random.randint(0,4) == 0):
        #     return False
        return True

    def setupFSMBehaviour(self):
        fsm = self.TruckFSMBehaviour()
        fsm.add_state(name=TRUCK_STATE_ONE, state=self.TruckFSMBehaviour.ReceiveContract(), initial=True)
        fsm.add_state(name=TRUCK_STATE_TWO, state=self.TruckFSMBehaviour.ProcessContract())
        fsm.add_state(name=TRUCK_STATE_THREE, state=self.TruckFSMBehaviour.PerformAction())

        fsm.add_transition(source=TRUCK_STATE_ONE, dest=TRUCK_STATE_ONE)
        fsm.add_transition(source=TRUCK_STATE_ONE, dest=TRUCK_STATE_TWO)
        fsm.add_transition(source=TRUCK_STATE_TWO, dest=TRUCK_STATE_THREE)
        fsm.add_transition(source=TRUCK_STATE_THREE, dest=TRUCK_STATE_ONE)
        return fsm

async def main():
    truck_agent = TruckAgent("agente2@localhost", input("Password: "))
    await truck_agent.start(auto_register=True)

    await spade.wait_until_finished(truck_agent)
    await truck_agent.stop()
    print("Agent finished")

if __name__ == "__main__":
    spade.run(main())