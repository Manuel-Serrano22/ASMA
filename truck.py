import os
import random
from spade.template import Template
import spade
from spade.agent import Agent
from spade.behaviour import FSMBehaviour, State, CyclicBehaviour, PeriodicBehaviour
from spade.message import Message
import asyncio
from utils import haversine, get_distance_truck_to_deposit, get_latitude_from_deposit, get_longitude_from_deposit
from dotenv import load_dotenv

load_dotenv()
SPADE_PASS = os.getenv('SPADE_PASS')

TRUCK_STATE_ONE = "SEND_PROPOSAL"
TRUCK_STATE_TWO = "PERFORM_ACTION"

TRUCK_CAPACITY = 2000
# - Faz sentido ter um CFP a processar pedidos sequencialmente? Acho que não é possível satisfazer mais do que um pedido ao mesmo tempo, um camião ou vai a um sitio ou vai a outro, por isso paralelizar não importa, certo?

class TruckAgent(Agent):
    
    def __init__(self, jid, password, latitude, longitude, capacity):
        super().__init__(jid, password)

        # shared state between behaviours
        self.id = jid.split("@")[0].split("agente")[1]  # extract the agent ID from the JID, for easier debug
        self.proposals = []  # store proposals from separate bins
        self.lat = latitude
        self.long = longitude
        self.capacity = capacity
        self.current_waste = 0
        self.bins_stats = {}
        self.time_to_reach_bin = 0
        self.total_waste_collected = 0
        self.distance_traveled = 0

    # cyclic check for CFP contracts
    # NOTE: its cyclic so it should run every iteration, but it has to wait for timeout 3sec on receive, so its periodic in practice
    class ReceiveCFPBehaviour(PeriodicBehaviour):
        async def run(self):
            print(f"TRUCK_{self.agent.id}: waiting for CFP proposals...")
            msg = await self.receive(timeout=3)  # timeout after 3 seconds
            if msg:
                if msg.metadata["performative"] == "cfp":
                    print(f"TRUCK_{self.agent.id}: cFP from {msg.sender} with body {msg.body}")
                    bin_id, bin_capacity, bin_latitute, bin_longitude = msg.body.strip().split(";")
                    self.agent.bins_stats[bin_id] = [
                        float(bin_capacity),  # bin capacity
                        float(bin_latitute),  # bin latitude
                        float(bin_longitude)  # bin longitude
                    ]

                    self.agent.proposals.append(msg)  # store the proposal in a queue to be processed by other behaviour
                else:
                    print(f"TRUCK_{self.agent.id}: non-CFP message from {msg.sender}")
            else:
                print(f"TRUCK_{self.agent.id}: No messages received during this check")

    class TruckFSMBehaviour(FSMBehaviour):
        # State 1: receive CFP proposal from a bin
        class ProcessContract(State):
            async def run(self):
                if self.agent.proposals:
                    proposal = self.agent.proposals.pop(0)  # get the oldest unanswered proposal
                    
                    if self.agent.decide_accept_proposal(proposal):

                        bin_id = proposal.body.split(";")[0]
                        reply = Message(to=str(proposal.sender))
                        reply.set_metadata("performative", "propose")

                        # track bin stats
                        bin_latitute = self.agent.bins_stats[bin_id][1]
                        bin_longitude = self.agent.bins_stats[bin_id][2]
                        truck_to_bin_dist = haversine(bin_latitute,bin_longitude, self.agent.lat, self.agent.long)
                        self.agent.bins_stats[bin_id].append(truck_to_bin_dist)

                        self.agent.time_to_reach_bin = truck_to_bin_dist / 100
                        print("TRUCK: Time to reach bin ->" + str(self.agent.time_to_reach_bin))

                        available_capacity = self.agent.capacity - self.agent.current_waste
                        reply.body = f"{self.agent.time_to_reach_bin};{available_capacity}" 
                        await self.send(reply)

                        print(f"TRUCK_{self.agent.id}: Truck sending proposal to {proposal.sender}")
                        self.set_next_state(TRUCK_STATE_TWO)
                    else:
                        reply = Message(to=str(proposal.sender))
                        reply.set_metadata("performative", "refuse")
                        await self.send(reply)

                        print(f"TRUCK_{self.agent.id}: rejecting contract from {proposal.sender}")
                        self.set_next_state(TRUCK_STATE_ONE) # process other requests
                else:
                    print(f"TRUCK_{self.agent.id}: No proposals to process currently")
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
                   
                    print(f"TRUCK_{self.agent.id}: simulating task for {self.agent.time_to_reach_bin}.")
                    await asyncio.sleep(self.agent.time_to_reach_bin)  # Simulate action time
                    print(f"Action performed, sending result to bin {msg.sender}...")

                    # Send the result to the bin
                    result_msg = Message(to=str(msg.sender))
                    
                    action_result = 1 # assume action is always successful

                    #successful cleaning
                    if action_result:
                        result_msg.set_metadata("performative", "inform-done") 
                        bin_id, bin_time, bin_waste = msg.body.strip().split(";")
                        truck_remaining_capacity = self.agent.capacity - self.agent.current_waste
                        waste_to_collect = min(float(bin_waste), truck_remaining_capacity)
                        result_msg.body = str(waste_to_collect)

                        self.agent.total_waste_collected += waste_to_collect
                        self.agent.distance_traveled += self.agent.bins_stats[bin_id][3]
                        self.agent.current_waste += waste_to_collect
                        self.agent.lat = self.agent.bins_stats[bin_id][1] # update truck position
                        self.agent.long = self.agent.bins_stats[bin_id][2] # update truck position
                        if(self.agent.current_waste == self.agent.capacity):
                            self.agent.go_to_deposit()
                    
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
    def decide_accept_proposal(self,proposal):
        #bin_id = proposal.body.split(";")[0]
        if(self.current_waste == self.capacity):
            self.go_to_deposit()
            return False
        
        return True #allow partial waste collection
    
    def go_to_deposit(self):
        dist = get_distance_truck_to_deposit(self.lat, self.long)
        self.distance_traveled += dist
        self.lat = get_latitude_from_deposit()
        self.long = get_longitude_from_deposit()
        self.current_waste = 0

    def setupFSMBehaviour(self):
        fsm = self.TruckFSMBehaviour()
        fsm.add_state(name=TRUCK_STATE_ONE, state=self.TruckFSMBehaviour.ProcessContract(), initial=True)
        fsm.add_state(name=TRUCK_STATE_TWO, state=self.TruckFSMBehaviour.PerformAction())

        fsm.add_transition(source=TRUCK_STATE_ONE, dest=TRUCK_STATE_ONE)
        fsm.add_transition(source=TRUCK_STATE_ONE, dest=TRUCK_STATE_TWO)
        fsm.add_transition(source=TRUCK_STATE_TWO, dest=TRUCK_STATE_ONE)
        return fsm

async def main():
    truck_agent = TruckAgent("agente2@localhost", SPADE_PASS, 41.1693, -8.6026)
    await truck_agent.start(auto_register=True)

    await spade.wait_until_finished(truck_agent)
    await truck_agent.stop()
    print("Agent finished")

if __name__ == "__main__":
    spade.run(main())