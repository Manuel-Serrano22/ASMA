import os
from spade.template import Template
import spade
from spade.agent import Agent
from spade.behaviour import FSMBehaviour, State, CyclicBehaviour, PeriodicBehaviour
from spade.message import Message
import asyncio
from utils import haversine, get_distance_truck_to_deposit, get_latitude_from_deposit, get_longitude_from_deposit
from dotenv import load_dotenv
import uuid

load_dotenv()
SPADE_PASS = os.getenv('SPADE_PASS')

TRUCK_STATE_ONE = "SEND_PROPOSAL"
TRUCK_STATE_TWO = "WAIT_RESPONSE"
TRUCK_STATE_THREE = "PERFORM ACTION"

TRUCK_CAPACITY = 2000
TRUCK_SPEED = 100  # meters per second

class TruckAgent(Agent):
    
    def __init__(self, jid, password, latitude, longitude, capacity):
        super().__init__(jid, password)

        # shared state between behaviours
        self.id = jid.split("@")[0].split("agente")[1]  # extract the agent ID from the JID, for easier debug
        self.lat = latitude
        self.long = longitude
        self.capacity = capacity
        self.current_waste = 0
        self.bins_stats = {} # {bin_id: [capacity, lat, long, distance]}
        self.proposals = []  # store CFP proposals from bins
        self.schedule = [] # confirmed pickups
        self.pending_schedule = [] # pending pickups (not confirmed yet)
        self.active_fsms = {}  # Track FSMs by proposal ID

        #global stats
        self.total_waste_collected = 0
        self.distance_traveled = 0
        self._lock = asyncio.Lock()  # for managing access to shared state
        
    # periodic behaviour to receive CFP proposals from bins, only responsible for receiving messages
    class ReceiveCFPBehaviour(PeriodicBehaviour):
        async def run(self):
            print(f"TRUCK_{self.agent.id}: waiting for CFP proposals...")
            msg = await self.receive(timeout=3)  # timeout after 3 seconds
            if msg:
                if msg.metadata["performative"] == "cfp":
                    print(f"TRUCK_{self.agent.id}: cFP from {msg.sender} with body {msg.body}")
                    bin_id, bin_capacity, bin_latitute, bin_longitude = msg.body.strip().split(";")
                    self.agent.bins_stats[bin_id] = {
                        "capacity": float(bin_capacity),  # bin capacity
                        "lat": float(bin_latitute),  # bin latitude
                        "long": float(bin_longitude)  # bin longitude
                    }

                    self.agent.proposals.append(msg)  # store the proposal in a queue to be processed by other behaviour
                else:
                    print(f"TRUCK_{self.agent.id}: non-CFP message from {msg.sender}")
            else:
                print(f"TRUCK_{self.agent.id}: No messages received during this check")

    # behaviour to manage spawning FSMs for each proposal
    # TODO: currently creates infinite FSMs, maybe should limit to N ?
    class ProcessProposalsBehaviour(CyclicBehaviour):
        async def run(self):
            if self.agent.proposals:
                proposal = self.agent.proposals.pop(0) # get the oldest unanswered proposal
                
                # uniqiue id for this proposal
                proposal_id = str(uuid.uuid4())
                
                # spawn new fsm behaviour for this proposal
                fsm, fsm_template = self.agent.setupFSMBehaviour(proposal, proposal_id)

                # Only allow behaviour to process messages from that specific bin
                template = Template()
                template.sender = str(proposal.sender)  

                final_template = fsm_template & template
                self.agent.add_behaviour(fsm, final_template)
                self.agent.active_fsms[proposal_id] = fsm # track active fsms list, for behaviour cleanup, and remove schedule entries

                print(f"TRUCK_{self.agent.id}: Started FSM for proposal {proposal_id} from {proposal.sender}")
            else:
                await asyncio.sleep(0.5)  # instead of running constanly
                
    class ProposalFSMBehaviour(FSMBehaviour):
        def __init__(self, proposal, proposal_id):
            super().__init__()

            # how to pass these values to the child states properly? Did an enginheiro solution
            self.proposal = proposal
            self.proposal_id = proposal_id
            
        # State 1: receive CFP proposal from a bin, and send proposal
        class SendProposal(State):
            def __init__(self, proposal=None, proposal_id=None):
                super().__init__()

                self.proposal = proposal
                self.proposal_id = proposal_id

            async def run(self):
            # if going to refuse proposal
                if not self.agent.decide_accept_proposal(self.proposal):
                    # build proposal message
                    reply = Message(to=str(self.proposal.sender))
                    reply.set_metadata("performative", "refuse")
                    await self.send(reply)
                    print(f"TRUCK_{self.agent.id}: rejecting proposal {self.proposal_id} contract from {self.proposal.sender}")
                    
                    # end behaviour
                    print(f"TRUCK_{self.agent.id}: Finished FSM for proposal {self.proposal_id}")
                    await self.agent.remove_fsm(self.proposal_id)
                    return
                
            # if going to accept proposal
                # store bin stats
                bin_id = self.proposal.body.split(";")[0]
                bin_latitute = self.agent.bins_stats[bin_id]["lat"]
                bin_longitude = self.agent.bins_stats[bin_id]["long"]
                bin_capacity = self.agent.bins_stats[bin_id]["capacity"]

                #calculate time to reach bin considering position and scheduled pickups
                time_to_bin, distance_to_bin = await self.agent.calculate_time_to_bin(bin_latitute, bin_longitude, bin_capacity)

                self.agent.pending_schedule.append({
                    "bin_id": bin_id,
                    "lat": bin_latitute,
                    "long": bin_longitude,
                    "time_to_reach": time_to_bin, 
                    "waste": bin_capacity, # waste collected estimation might be > waste in actual schedule, since bin will possibly agree on lower collection value
                    "proposal_id": self.proposal_id # to track proposal
                })

                #build proposal message
                reply = Message(to=str(self.proposal.sender))
                reply.set_metadata("performative", "propose")
                available_capacity = self.agent.capacity - self.agent.current_waste
                reply.body = f"{time_to_bin};{available_capacity}" 

                await self.send(reply)

                print(f"TRUCK_{self.agent.id}: Sent proposal {self.proposal_id} to {self.proposal.sender} with time {time_to_bin}")
                self.set_next_state(TRUCK_STATE_TWO)

        # State 2: perform action
        class WaitResponse(State):
            def __init__(self, proposal=None, proposal_id=None):
                super().__init__()

                self.proposal = proposal
                self.proposal_id = proposal_id
        
            async def run(self):
                print(f"TRUCK_{self.agent.id}: Waiting for response on proposal {self.proposal_id}")
                msg = await self.receive(timeout=10)  # wait for a message for 10 seconds > longer than the bin's timeout on other proposals !
                if (not msg):
                    print(f"TRUCK_{self.agent.id}: No response for proposal {self.proposal_id}")
                    
                    # end behaviour
                    print(f"TRUCK_{self.agent.id}: Finished FSM for proposal {self.proposal_id}")
                    await self.agent.remove_fsm(self.proposal_id)
                    return

                if msg.metadata["performative"] == "reject-proposal":
                    print(f"TRUCK_{self.agent.id}: Proposal {self.proposal_id} rejected by {msg.sender}")
                    
                    # end behaviour
                    print(f"TRUCK_{self.agent.id}: Finished FSM for proposal {self.proposal_id}")
                    await self.agent.remove_fsm(self.proposal_id)
                    return

                elif (msg.metadata["performative"] == "accept-proposal"):

                    bin_id = self.proposal.body.split(";")[0]
                    bin_lat = self.agent.bins_stats[bin_id]["lat"]
                    bin_long = self.agent.bins_stats[bin_id]["long"]

                    #bin sends back id, agreed time to reach (sent previously in truck's proposal), and waste to collect
                    bin_id, time_to_reach, waste = msg.body.strip().split(";")
                    
                    async with self.agent._lock: # guarantee no conflict when accessing shared pending entries
                        pending_entry = next((entry for entry in self.agent.pending_schedule if entry["proposal_id"] == self.proposal_id), None) # get the entry from pending schedule
                        if pending_entry:
                            self.agent.pending_schedule.remove(pending_entry)

                            # add to confirmed schedule
                            self.agent.schedule.append({
                                "bin_id": bin_id,
                                "lat": bin_lat,
                                "long": bin_long,
                                "time_to_reach": float(time_to_reach),
                                "waste": float(waste),
                                "proposal_id": self.proposal_id,
                                "start_time": asyncio.get_event_loop().time()  # add start time to task, to schedule other tasks, considering ongoing tasks
                            })
                            print(f"TRUCK_{self.agent.id}: Proposal {self.proposal_id} accepted by {msg.sender}, added to schedule")
                            self.set_next_state(TRUCK_STATE_THREE) # move to perform action state
                        
                        # if for some reason the entry is not found in pending schedule (does this ever happen?)
                        else:
                            print(f"TRUCK_{self.agent.id}: Proposal {self.proposal_id} not found in pending schedule")
                            
                            # end behaviour
                            print(f"TRUCK_{self.agent.id}: Finished FSM for proposal {self.proposal_id}")
                            await self.agent.remove_fsm(self.proposal_id)
                            return
                else:
                    print(f"TRUCK_{self.agent.id}: Unexpected message for proposal {self.proposal_id}: {msg.metadata['performative']}")
                    self.set_next_state(TRUCK_STATE_TWO) # repeat state, waiting for answer

        class PerformAction(State):
            def __init__(self, proposal=None, proposal_id=None):
                super().__init__()

                self.proposal = proposal
                self.proposal_id = proposal_id
            
            async def run(self):
                async with self.agent._lock: # guarantee no conflict when accessing shared scheduled entries
                    bin_id = self.proposal.body.split(";")[0]
                    schedule_entry = next((entry for entry in self.agent.schedule if entry["proposal_id"] == self.proposal_id), None)

                    if not schedule_entry: # if for some reason the scheduled action not found (does this ever happen?)
                        print(f"TRUCK_{self.agent.id}: Schedule entry for proposal {self.proposal_id} not found")
                        
                        # end behaviour
                        print(f"TRUCK_{self.agent.id}: Finished FSM for proposal {self.proposal_id}")
                        await self.agent.remove_fsm(self.proposal_id)
                        return
                    
                    time_to_reach = schedule_entry["time_to_reach"]
                    print(f"TRUCK_{self.agent.id}: simulating task for {schedule_entry['time_to_reach']}.")
                await asyncio.sleep(time_to_reach)  # Simulate action time

                # Send the result to the bin             
                result_msg = Message(to=str(self.proposal.sender))
                
                action_result = 1 # assume action is always successful

                #successful cleaning
                if action_result:
                    result_msg.set_metadata("performative", "inform-done") 
                    truck_remaining_capacity = self.agent.capacity - self.agent.current_waste
                    waste_to_collect = min(schedule_entry["waste"], truck_remaining_capacity)
                    result_msg.body = str(waste_to_collect) # truck sends back the amount of waste collected as confirmation

                    self.agent.total_waste_collected += waste_to_collect

                    #TODO: isto está bem???
                    self.agent.distance_traveled += haversine(self.agent.lat, self.agent.long, schedule_entry["lat"], schedule_entry["long"])
                    self.agent.current_waste += waste_to_collect
                    
                    self.agent.lat = schedule_entry["lat"] # update truck position
                    self.agent.long = schedule_entry["long"] # update truck position
                    if(self.agent.current_waste >= self.agent.capacity):
                        self.agent.go_to_deposit()
                
                    async with self.agent._lock: # guarantee no conflict when accessing shared scheduled entries
                        # remove entry from schedule
                        self.agent.schedule = [entry for entry in self.agent.schedule if entry["proposal_id"] != self.proposal_id]
                        print(f"TRUCK_{self.agent.id}: Completed task for proposal {self.proposal_id}, removed from schedule")
                
                #unsucessful cleaning
                else:
                    print(f"TRUCK_{self.agent.id}: Failed to clean bin {bin_id} for proposal {self.proposal_id}")
                    result_msg.set_metadata("performative", "failure")
                    result_msg.body = "Truck failed to clean the bin!"

                await self.send(result_msg)

                # end behaviour
                print(f"TRUCK_{self.agent.id}: Finished FSM for proposal {self.proposal_id}")
                await self.agent.remove_fsm(self.proposal_id)
    
    async def calculate_time_to_bin(self, bin_lat, bin_long, bin_waste):
        async with self._lock:
            current_lat, current_long = self.lat, self.long
            total_distance = 0
            simulated_waste = self.current_waste
            current_time = asyncio.get_event_loop().time()

            # Process scheduled tasks, adjusting for elapsed time
            for entry in sorted(self.schedule, key=lambda x: x["start_time"]):
                    elapsed = current_time - entry["start_time"]
                    # if elapsed >= entry["time_to_reach"]:
                    #     print("Task is complete, updating position and waste")
                    #     # Task is complete; update position and waste
                    #     current_lat, current_long = entry["lat"], entry["long"]
                    #     simulated_waste += min(entry["waste"], self.capacity - simulated_waste)
                    #     if simulated_waste >= self.capacity:
                    #         dist_to_deposit = get_distance_truck_to_deposit(current_lat, current_long)
                    #         total_distance += dist_to_deposit
                    #         current_lat, current_long = get_latitude_from_deposit(), get_longitude_from_deposit()
                    #         simulated_waste = 0
                    # else:
                    print("Calculated time to bin, task is ongoing")
                    # Task is ongoing; calculate remaining time
                    remaining_time = entry["time_to_reach"] - elapsed
                    total_distance += (remaining_time * TRUCK_SPEED)  # Convert remaining time back to distance
                    current_lat, current_long = entry["lat"], entry["long"]
                    simulated_waste += min(entry["waste"], self.capacity - simulated_waste)
                    if simulated_waste >= self.capacity:
                        dist_to_deposit = get_distance_truck_to_deposit(current_lat, current_long)
                        total_distance += dist_to_deposit
                        current_lat, current_long = get_latitude_from_deposit(), get_longitude_from_deposit()
                        simulated_waste = 0

            # Process pending proposals (assumed to be confirmed)
            for entry in self.pending_schedule:
                dist = haversine(current_lat, current_long, entry["lat"], entry["long"])
                total_distance += dist
                current_lat, current_long = entry["lat"], entry["long"]
                simulated_waste += min(entry["waste"], self.capacity - simulated_waste)
                if simulated_waste >= self.capacity:
                    dist_to_deposit = get_distance_truck_to_deposit(current_lat, current_long)
                    total_distance += dist_to_deposit
                    current_lat, current_long = get_latitude_from_deposit(), get_longitude_from_deposit()
                    simulated_waste = 0

            # Add distance to the new bin
            dist_to_bin = haversine(current_lat, current_long, bin_lat, bin_long)
            total_distance += dist_to_bin
            simulated_waste += min(bin_waste, self.capacity - simulated_waste)
            if simulated_waste >= self.capacity:
                dist_to_deposit = get_distance_truck_to_deposit(bin_lat, bin_long)
                total_distance += dist_to_deposit

            time_to_reach = total_distance / TRUCK_SPEED  # Assuming 100 units per second
            return time_to_reach, dist_to_bin

    # Simulation of logic to accept a proposal
    def decide_accept_proposal(self, proposal):
        
        # in practice this never happens, but just in case
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

    async def remove_fsm(self, proposal_id):
        async with self._lock:
            if proposal_id in self.active_fsms:
                fsm = self.active_fsms.pop(proposal_id)

                # self.remove_behaviour(fsm) # TODO: can i not remove the behaviour?

                # Remove the corresponding schedule entries
                self.schedule = [entry for entry in self.schedule if entry["proposal_id"] != proposal_id]
                # Remove the corresponding pending schedule entries
                self.pending_schedule = [entry for entry in self.pending_schedule if entry["proposal_id"] != proposal_id]
                print(f"TRUCK_{self.id}: Removed FSM and schedule entries for proposal {proposal_id}")

    def setupFSMBehaviour(self, proposal, proposal_id): 
        fsm = self.ProposalFSMBehaviour(proposal, proposal_id)
        fsm.add_state(name=TRUCK_STATE_ONE, state=self.ProposalFSMBehaviour.SendProposal(proposal, proposal_id), initial=True)
        fsm.add_state(name=TRUCK_STATE_TWO, state=self.ProposalFSMBehaviour.WaitResponse(proposal, proposal_id))
        fsm.add_state(name=TRUCK_STATE_THREE, state=self.ProposalFSMBehaviour.PerformAction(proposal, proposal_id))
        fsm.add_transition(source=TRUCK_STATE_ONE, dest=TRUCK_STATE_TWO)
        fsm.add_transition(source=TRUCK_STATE_TWO, dest=TRUCK_STATE_TWO)
        fsm.add_transition(source=TRUCK_STATE_TWO, dest=TRUCK_STATE_THREE)

        fsm_template = Template()
        fsm_template.set_metadata("performative", "cfp")

        return fsm, ~fsm_template

    async def setup(self):
        cfp_template = Template()
        cfp_template.set_metadata("performative", "cfp")
        
        receive_cfp_behaviour = self.ReceiveCFPBehaviour(period=1)  # Check for CFP every 5 seconds
        self.add_behaviour(receive_cfp_behaviour, template=cfp_template) # force first behaviour to only read cfp messages, and ignore all others -> or else it would eat all messages that were supposed to go to the state machine behaviours!

        process_proposals = self.ProcessProposalsBehaviour()
        self.add_behaviour(process_proposals)

    async def finish(self, proposal_id):
        print(f"TRUCK_{self.id}: Finished FSM for proposal {proposal_id}")
        await self.agent.remove_fsm(proposal_id)

async def main():
    truck_agent = TruckAgent("agente2@localhost", SPADE_PASS, 41.1693, -8.6026, TRUCK_CAPACITY)
    await truck_agent.start(auto_register=True)

    await spade.wait_until_finished(truck_agent)
    await truck_agent.stop()
    print("Agent finished")

if __name__ == "__main__":
    spade.run(main())