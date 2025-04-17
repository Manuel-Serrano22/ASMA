import os
import random
from spade.template import Template
import spade
from spade.agent import Agent
from spade.behaviour import FSMBehaviour, State, CyclicBehaviour, PeriodicBehaviour
from spade.message import Message
import asyncio
from dotenv import load_dotenv

load_dotenv()
SPADE_PASS = os.getenv('SPADE_PASS')

TRUCK_STATE_ONE = "SEND_PROPOSAL"
TRUCK_STATE_TWO = "PERFORM_ACTION"

# - Faz sentido ter um CFP a processar pedidos sequencialmente? Acho que não é possível satisfazer mais do que um pedido ao mesmo tempo, um camião ou vai a um sitio ou vai a outro, por isso paralelizar não importa, certo?

class TruckAgent(Agent):
    
    def __init__(self, jid, password):
        super().__init__(jid, password)

        # shared state between behaviours
        self.proposals = []  # store proposals from separate bins

    # cyclic check for CFP contracts
    # NOTE: its cyclic so it should run every iteration, but it has to wait for timeout 3sec on receive, so its periodic in practice
    class ReceiveCFPBehaviour(PeriodicBehaviour):
        async def run(self):
            print(f"TRUCK_{self.agent.jid}: waiting for CFP proposals...")
            msg = await self.receive(timeout=3)  # timeout after 3 seconds
            if msg:
                if msg.metadata["performative"] == "cfp":
                    print(f"TRUCK_{self.agent.jid}: cFP from {msg.sender}")
                    self.agent.proposals.append(msg)  # store the proposal in a queue to be processed by other behaviour
                else:
                    print(f"TRUCK_{self.agent.jid}: non-CFP message from {msg.sender}")
            else:
                print(f"TRUCK_{self.agent.jid}: No messages received during this check")

    class TruckFSMBehaviour(FSMBehaviour):
        # State 1: receive CFP proposal from a bin
        class ProcessContract(State):
            async def run(self):
                if self.agent.proposals:
                    proposal = self.agent.proposals.pop(0)  # Get the first proposal
                    
                    if self.agent.decide_accept_proposal(proposal):
                        reply = Message(to=str(proposal.sender))
                        reply.set_metadata("performative", "propose")
                        reply.body = str(random.randint(0, 100))  # Send a random value as proposal
                        await self.send(reply)
                        print(f"TRUCK_{self.agent.jid}: Truck sending proposal to {proposal.sender}")
                        self.set_next_state(TRUCK_STATE_TWO) 
                    else:
                        reply = Message(to=str(proposal.sender))
                        reply.set_metadata("performative", "refuse")
                        await self.send(reply)

                        print(f"TRUCK_{self.agent.jid}: Truck rejecting contract from {proposal.sender}")
                        self.set_next_state(TRUCK_STATE_ONE) # process other requests
                else:
                    print(f"TRUCK_{self.agent.jid}: No proposals to process currently")
                    await asyncio.sleep(3)  # wait for 3 second
                    self.set_next_state(TRUCK_STATE_ONE) 

        # State 2: perform action
        class PerformAction(State):
            async def run(self):
                print("Waiting for bin deicision on proposal...")
                msg = await self.receive(timeout=10)  # wait for a message for 10 seconds > longer than the bin's timeout on other proposals !

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
        cfp_template = Template()
        cfp_template.set_metadata("performative", "cfp")
        
        receive_cfp_behaviour = self.ReceiveCFPBehaviour(period=1)  # Check for CFP every 5 seconds
        self.add_behaviour(receive_cfp_behaviour, template=cfp_template) # force first behaviour to only read cfp messages, and ignore all others -> or else it would eat all messages that were supposed to go to the state machine behaviours!

        fsm = self.setupFSMBehaviour()
        self.add_behaviour(fsm, template=~cfp_template)

    # Simulation of logic to accept a proposal
    def decide_accept_proposal(self, proposal):
        if (random.randint(0,4) == 0):
            return False
        return True

    def setupFSMBehaviour(self):
        fsm = self.TruckFSMBehaviour()
        fsm.add_state(name=TRUCK_STATE_ONE, state=self.TruckFSMBehaviour.ProcessContract(), initial=True)
        fsm.add_state(name=TRUCK_STATE_TWO, state=self.TruckFSMBehaviour.PerformAction())

        fsm.add_transition(source=TRUCK_STATE_ONE, dest=TRUCK_STATE_ONE)
        fsm.add_transition(source=TRUCK_STATE_ONE, dest=TRUCK_STATE_TWO)
        fsm.add_transition(source=TRUCK_STATE_TWO, dest=TRUCK_STATE_ONE)
        return fsm

async def main():
    truck_agent = TruckAgent("agente2@localhost", SPADE_PASS)
    await truck_agent.start(auto_register=True)

    await spade.wait_until_finished(truck_agent)
    await truck_agent.stop()
    print("Agent finished")

if __name__ == "__main__":
    spade.run(main())