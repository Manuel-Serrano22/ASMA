import random
import spade
from spade.agent import Agent
from spade.behaviour import FSMBehaviour, State, CyclicBehaviour, PeriodicBehaviour
from spade.message import Message
import asyncio
from utils import haversine, get_distance_truck_to_deposit, get_latitude_from_deposit, get_longitude_from_deposit

TRUCK_STATE_ONE = "RECEIVE_CFP"
TRUCK_STATE_TWO = "SEND_PROPOSAL"
TRUCK_STATE_THREE = "PERFORM_ACTION"

# - Faz sentido ter um CFP a processar pedidos sequencialmente? Acho que não é possível satisfazer mais do que um pedido ao mesmo tempo, um camião ou vai a um sitio ou vai a outro, por isso paralelizar não importa, certo?

class TruckAgent(Agent):
    
    def __init__(self, jid, password, latitude, longitude):
        super().__init__(jid, password)

        # shared state between behaviours
        self.currentProposal = None  # store currently processed proposal
        self.lat = latitude
        self.long = longitude
        self.capacity = 100
        self.current_waste= 0
        self.bins_stats = {}
        self.time_to_reach_bin = 0
        self.total_waste_collected = 0
        self.distance_traveled = 0

    class SendUpdateBehaviour(PeriodicBehaviour):

        async def run(self):
            msg = Message(to="world@localhost")
            msg.set_metadata("performative", "inform")
            msg.set_metadata("type", "truck_status_update")
            msg.body = f"{self.agent.jid};{self.agent.distance_traveled};{self.agent.total_waste_collected}"
            await self.send(msg)
            print(f"\033[91mBIN: Sent capacity update to world -> {msg.body}\033[0m")

    class TruckFSMBehaviour(FSMBehaviour):
        # State 1: receive CFP proposal from a bin
        class ReceiveContract(State):
            async def run(self):
                msg = await self.receive(timeout=3)  # timeout after 3 seconds
                if msg:
                    if msg.metadata["performative"] == "cfp":
                        print(f"TRUCK: CFP from {msg.sender} with body {msg.body}")
                        bin_id, bin_capacity, bin_latitute, bin_longitude = msg.body.strip().split(";")
                        self.agent.bins_stats[bin_id] = []
                        self.agent.bins_stats[bin_id].append(float(bin_capacity))
                        self.agent.bins_stats[bin_id].append(float(bin_latitute))
                        self.agent.bins_stats[bin_id].append(float(bin_longitude))
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
                if self.agent.decide_accept_proposal():
                    #reply = Message(to=proposal.sender.jid)
                    bin_id = proposal.body.split(";")[0]
                    reply = Message(to=str(proposal.sender))
                    reply.set_metadata("performative", "propose")
                    bin_latitute = self.agent.bins_stats[bin_id][1]
                    bin_longitude = self.agent.bins_stats[bin_id][2]
                    truck_to_bin_dist = haversine(bin_latitute,bin_longitude, self.agent.lat, self.agent.long)
                    self.agent.bins_stats[bin_id].append(truck_to_bin_dist)
                    self.agent.time_to_reach_bin = truck_to_bin_dist / 100
                    print("TRUCK: Time to reach bin ->" + str(self.agent.time_to_reach_bin))
                   
                    available_capacity = self.agent.capacity - self.agent.current_waste
                    reply.body = f"{self.agent.time_to_reach_bin};{available_capacity}" 
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
                    #print(f"Bin stats from proposal {self.agent.bins_stats[bin_id]}")
                   
                    await asyncio.sleep(self.agent.time_to_reach_bin)  # Simulate action time
                    print("Action performed, sending result to bin...")

                    # Send the result to the bin
                    #result_msg = Message(to=msg.sender.jid)
                    result_msg = Message(to=str(msg.sender))
                    
                    action_result = 1

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
        sendUpdate = self.SendUpdateBehaviour(period=5)  # every 5 seconds, send update to world
        self.add_behaviour(sendUpdate)
        fsm = self.setupFSMBehaviour()
        self.add_behaviour(fsm)

    # Simulation of logic to accept a proposal
    def decide_accept_proposal(self):
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
        fsm.add_state(name=TRUCK_STATE_ONE, state=self.TruckFSMBehaviour.ReceiveContract(), initial=True)
        fsm.add_state(name=TRUCK_STATE_TWO, state=self.TruckFSMBehaviour.ProcessContract())
        fsm.add_state(name=TRUCK_STATE_THREE, state=self.TruckFSMBehaviour.PerformAction())

        fsm.add_transition(source=TRUCK_STATE_ONE, dest=TRUCK_STATE_ONE)
        fsm.add_transition(source=TRUCK_STATE_ONE, dest=TRUCK_STATE_TWO)
        fsm.add_transition(source=TRUCK_STATE_TWO, dest=TRUCK_STATE_ONE)
        fsm.add_transition(source=TRUCK_STATE_TWO, dest=TRUCK_STATE_THREE)
        fsm.add_transition(source=TRUCK_STATE_THREE, dest=TRUCK_STATE_ONE)
        return fsm
