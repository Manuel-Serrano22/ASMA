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

TRUCK_CAPACITY = 200
TRUCK_SPEED = 100  # meters per second

MAX_SCHEDULED_TASKS = 10

RECEIVE_CFP_PERIOD = 0.5  # seconds
ACCEPT_PROPOSAL_TIMEOUT = 2  # time to wait for a response from the bin before accepting the proposal, or assume rejected

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
        self.schedule = [] # pickup schedule (either pending or confirmed)

        #global stats
        self.total_waste_collected = 0
        self.distance_traveled = 0
        self._lock = asyncio.Lock()  # for managing access to shared state
        
    # periodic behaviour to receive CFP proposals from bins, only responsible for receiving messages
    class ReceiveCFPBehaviour(PeriodicBehaviour):
        async def run(self):
            print(f"TRUCK_{self.agent.id}: waiting for CFP proposals...")
            msg = await self.receive(timeout=1)  # timeout after 3 seconds
            if msg:
                if msg.metadata["performative"] == "cfp":
                    print(f"TRUCK_{self.agent.id}: CFP from {msg.sender} with body {msg.body}")
                    bin_id, bin_capacity, bin_latitute, bin_longitude = msg.body.strip().split(";")
                    self.agent.bins_stats[bin_id] = {
                        "capacity": float(bin_capacity),  # bin capacity # not very relevant, since bin overwrites the value in accept-proposal, but good to have an initial estimation in contract
                        "lat": float(bin_latitute),  # bin latitude
                        "long": float(bin_longitude)  # bin longitude
                    }

                    self.agent.proposals.append(msg)  # store the proposal in a queue to be processed by other behaviour
                else:
                    print(f"TRUCK_{self.agent.id}: non-CFP message from {msg.sender}")
            else:
                print(f"TRUCK_{self.agent.id}: No messages received during this check")

    # behaviour to manage spawning FSMs for each proposal
    # creates infinite FSMs, but accept_proposal condition inside FSM limits the number of scheduled tasks
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

                final_template = fsm_template & template # final template only allows messages from the bin that sent the proposal, and non cfp messages (cause those should be caught by the receiveCFPBehaviour)
                self.agent.add_behaviour(fsm, final_template)

                print(f"TRUCK_{self.agent.id}: Started FSM for proposal {proposal_id} from {proposal.sender}")
            else:
                await asyncio.sleep(0.1)  # instead of running constanly

    class PerformQueueBehaviour(CyclicBehaviour):
        async def run(self):
            # async with self.agent._lock: # guarantee no conflict when accessing shared schedule
                if self.agent.schedule and self.agent.schedule[0]["state"] == "confirmed":
                    schedule_entry = self.agent.schedule[0]
                    print(f"TRUCK_{self.agent.id}: simulating task for proposal {schedule_entry['proposal_id']} during {schedule_entry['last_bin_to_new_bin_time']} seconds")
                    
                    await asyncio.sleep(schedule_entry["last_bin_to_new_bin_time"])  # simulate action time

                    # Send the result to the bin             
                    result_msg = Message(to=str(schedule_entry["bin_jid"]))
                    
                    action_result = 1 # assume action is always successful

                    #successful cleaning
                    if action_result:
                        # print(f"TRUCK_{self.agent.id}: Successfully cleaned bin {schedule_entry['bin_id']} for proposal {schedule_entry['proposal_id']}")
                        # print(f"TRUCK_{self.agent.id}: updating stats and sending result to bin {schedule_entry['bin_jid']}")
                        result_msg.set_metadata("performative", "inform-done") 
                        result_msg.body = str(schedule_entry["waste"]) # truck sends back the amount of waste collected as confirmation

                        self.agent.total_waste_collected += schedule_entry["waste"]
                        self.agent.distance_traveled += schedule_entry["last_bin_to_new_bin_distance"] # update distance traveled
                        self.agent.current_waste =  schedule_entry["end_occupied_capacity"] # update truck capacity
                        self.agent.lat = schedule_entry["end_latitude"] # update truck position
                        self.agent.long = schedule_entry["end_longitude"] # update truck position

                        await self.agent.update_queue_task_finish(schedule_entry["proposal_id"]) # remove task from schedule, since it was completed

                    #unsucessful cleaning
                    else:
                        print(f"TRUCK_{self.agent.id}: Failed to clean bin {schedule_entry['bin_id']} for proposal {schedule_entry['proposal_id']}")
                        result_msg.set_metadata("performative", "failure")
                        result_msg.body = "Truck failed to clean the bin!"

                    await self.send(result_msg)

                else:
                    await asyncio.sleep(0.5)  # instead of running constanly

    class ProposalFSMBehaviour(FSMBehaviour):
        def __init__(self, proposal, proposal_id):
            super().__init__()

            # how to pass these values to the child states properly? Did an "enginheiro" solution
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
                    
                    # end behaviour, nothing else to do
                    print(f"TRUCK_{self.agent.id}: Finished FSM for proposal {self.proposal_id}")
                    return
                
            # if going to accept proposal
                bin_id = self.proposal.body.split(";")[0]
                bin_latitute = self.agent.bins_stats[bin_id]["lat"]
                bin_longitude = self.agent.bins_stats[bin_id]["long"]
                bin_occupation = self.agent.bins_stats[bin_id]["capacity"]

                #calculate new schedule entry, and proposal message values, to reach bin considering position and scheduled pickups
                time_to_bin, available_space_for_task, schedule_entry = await self.agent.calculate_stats_to_bin(bin_latitute, bin_longitude, bin_occupation)

                # add additional info to schedule entry
                schedule_entry.update({
                    "proposal_id": self.proposal_id, # to track proposal
                    "bin_id": bin_id,
                    "bin_jid": str(self.proposal.sender), # to respond with success/failure of task to sender bin
                    "state": "pending", # to track state of the task
                })

                self.agent.schedule.append(schedule_entry) # add to schedule

                #build proposal message
                reply = Message(to=str(self.proposal.sender))
                reply.set_metadata("performative", "propose")
                reply.body = f"{time_to_bin};{available_space_for_task}" 

                await self.send(reply)

                print(f"TRUCK_{self.agent.id}: Sent proposal {self.proposal_id} to {self.proposal.sender} with values time:{time_to_bin} aka({schedule_entry['last_bin_to_new_bin_time']}),{available_space_for_task}")
                self.set_next_state(TRUCK_STATE_TWO)

        # State 2: perform action
        class WaitResponse(State):
            def __init__(self, proposal=None, proposal_id=None):
                super().__init__()

                self.proposal = proposal
                self.proposal_id = proposal_id
        
            async def run(self):
                print(f"TRUCK_{self.agent.id}: Waiting for response on proposal {self.proposal_id}")
                msg = await self.receive(timeout=ACCEPT_PROPOSAL_TIMEOUT)  # wait for a message for 10 seconds > longer than the bin's timeout on other proposals !
                if (not msg):
                    print(f"TRUCK_{self.agent.id}: No response for proposal {self.proposal_id}")
                    await self.agent.update_queue_task_cancel(self.proposal_id) # remove task from schedule, since no response was received

                    # end behaviour
                    print(f"TRUCK_{self.agent.id}: Finished FSM for proposal {self.proposal_id}")
                    await self.agent.update_queue_task_cancel(self.proposal_id) # remove task from schedule, since no response was received

                    return

                if msg.metadata["performative"] == "reject-proposal":
                    print(f"TRUCK_{self.agent.id}: Proposal {self.proposal_id} rejected by {msg.sender}")
                    
                    # end behaviour
                    print(f"TRUCK_{self.agent.id}: Finished FSM for proposal {self.proposal_id}")

                    return

                elif (msg.metadata["performative"] == "accept-proposal"):

                    bin_id = self.proposal.body.split(";")[0]
                    #bin sends back id, agreed time to reach (sent previously in truck's proposal), and waste to collect (can be less)
                    bin_id, _, waste = msg.body.strip().split(";")
                    waste = float(waste) # waste to be collected from bin

                    async with self.agent._lock: # guarantee no conflict when accessing shared entries
                        for i, entry in enumerate(self.agent.schedule):
                            if entry["proposal_id"] == self.proposal_id:
                                self.agent.schedule[i]["state"] = "confirmed" # update state of the task to confirmed
                                self.agent.schedule[i]["bin_id"] = bin_id # update bin id in schedule
                                
                                #check if bin is actually expecting less waste cleaned than proposed
                                waste_diff = self.agent.schedule[i]["waste"] - waste
                                if (waste_diff > 0):
                                    self.agent.schedule[i]["waste"] = waste
                                    print(f"TRUCK_{self.agent.id}: Proposal {self.proposal_id} accepted by {msg.sender}, but with less waste to collect: ([{waste}] vs {self.agent.schedule[i]['waste']})")
                                    await self.update_queue_task_waste(entry["proposal_id"], waste_diff) # update subsequent tasks in queue to have more available space for the new task
                                break

                        print(f"TRUCK_{self.agent.id}: Proposal {self.proposal_id} accepted by {msg.sender}")
                        
                else:
                    print(f"TRUCK_{self.agent.id}: Unexpected message for proposal {self.proposal_id}: {msg.metadata['performative']}")
                    self.set_next_state(TRUCK_STATE_TWO) # repeat state, waiting for answer
    
    async def calculate_stats_to_bin(self, bin_lat, bin_long, bin_waste):
        # async with self._lock:
        # if there are scheduled tasks, calculate time to reach bin considering last task stats on queue
        if (len(self.schedule) > 0):
            last_task = self.schedule[-1]
            last_lat = last_task["end_latitude"]
            last_long = last_task["end_longitude"]
            last_occupied_capacity = last_task["end_occupied_capacity"]
            last_time = last_task["end_time"]
        #else use current position and stats as last task
        else: 
            last_lat = self.lat
            last_long = self.long
            last_occupied_capacity = self.current_waste
            last_time = asyncio.get_event_loop().time() + ACCEPT_PROPOSAL_TIMEOUT # time this task will start # assuming a timeout buffer to receive a confirmation from the bin, and actually start the task
        
        # if truck is full, must go to deposit
        if (last_occupied_capacity == self.capacity): 
            last_lat, last_long, time_elapsed = self.go_to_deposit(last_lat, last_long) # go to deposit if truck is full
            last_occupied_capacity = 0 # reset available capacity to full
            last_time += time_elapsed # update last time to include time to go to deposit

        dist_from_prev_to_new_bin = haversine(bin_lat, bin_long, last_lat, last_long) # distance from last task to bin
        time_to_new_bin = dist_from_prev_to_new_bin / TRUCK_SPEED
        start_time = last_time # time this task will start, considering last task end time
        final_time = last_time + time_to_new_bin # time bin is reached, considering last task end time
        final_occupied_capacity = min(self.capacity, last_occupied_capacity + bin_waste) # capacity to reach bin, considering last task end capacity
        used_capacity = min(bin_waste, self.capacity - last_occupied_capacity) # waste to be collected from bin in the new task, considering last task end capacity
        
        print(f"TRUCK_{self.id}: Calculated stats to bin {bin_lat}, {bin_long} with waste {bin_waste}:")
        print(f"TRUCK_{self.id}: waste to be collected from bin: {used_capacity}, final occupied_space after task: {final_occupied_capacity}/{self.capacity}")
    
        schedule_entry = {
            "start_time": start_time,
            "end_time": final_time,
            "end_latitude": bin_lat,
            "end_longitude": bin_long,
            "end_occupied_capacity": final_occupied_capacity,
            "waste": used_capacity, # waste to be collected from bin
            "last_bin_to_new_bin_distance": dist_from_prev_to_new_bin,
            "last_bin_to_new_bin_time": time_to_new_bin
        }
        
        return final_time, used_capacity, schedule_entry # return time to bin, available space for task, and schedule entry with all the info needed to track the task
    
    #TODO
    async def update_queue_task_cancel(self, proposal_id):
        async with self._lock:
            # remove entry from schedule
            self.schedule = [entry for entry in self.schedule if entry["proposal_id"] != proposal_id]
            print(f"TRUCK_{self.id}: Completed task for proposal {proposal_id}, removed from schedule")

            # update all subsequent tasks in the queue to be executed earlier, have different location and have more available occupation 
            # for i, entry in enumerate(self.schedule):
            #     if entry["proposal_id"] == proposal_id:
            #         self.schedule[i]["state"] = "cancelled"
            #         self.schedule[i]["waste"] = 0 # waste to be collected from bin in the new task, considering last task end capacity
            #         self.schedule[i]["end_occupied_capacity"] = self.capacity
            #         self.schedule[i]["end_time"] = asyncio.get_event_loop().time() + ACCEPT_PROPOSAL_TIMEOUT # time this task will start # assuming a timeout buffer to receive a confirmation from the bin, and actually start the task
            #         break
            # print(f"TRUCK_{self.id}: Updated schedule for proposal {proposal_id}, removed from schedule")
            # update all subsequent tasks in the queue to have more available space for the new task
    
    #TODO
    async def update_queue_task_waste(self, proposal_id, waste_diff):
        pass
        # async with self._lock:
        #     # update all subsequent tasks in the queue to have more available space for the new task
        #     for i, entry in enumerate(self.schedule):
        #         if entry["proposal_id"] == proposal_id:
        #             self.schedule[i]["waste"] = entry["waste"] + waste_diff
        #             self.schedule[i]["end_occupied_capacity"] = self.capacity - self.schedule[i]["waste"]
        #             break
        #     print(f"TRUCK_{self.id}: Updated schedule for proposal {proposal_id}, removed from schedule")
        #     # update all subsequent tasks in the queue to have more available space for the new task

    async def update_queue_task_finish(self, proposal_id):
        # async with self._lock:
            # remove entry from schedule
            self.schedule = [entry for entry in self.schedule if entry["proposal_id"] != proposal_id]
            print(f"TRUCK_{self.id}: Completed task for proposal {proposal_id}, removed from schedule")

    # Simulation of logic to accept a proposal
    def decide_accept_proposal(self, proposal):

        if len(self.schedule) >= MAX_SCHEDULED_TASKS: # limit number of scheduled tasks
            print(f"TRUCK_{self.id}: Too many scheduled tasks, rejecting proposal {proposal.body}")
            return False
        
        return True #allow total or partial proposal capacity waste collection
    
    #returns end latitude and longitude of the truck, and time to reach deposit from that location
    def go_to_deposit(self, curr_lat, curr_long):
        dist = get_distance_truck_to_deposit(curr_lat, curr_long)
        # self.distance_traveled += dist
        final_lat = get_latitude_from_deposit()
        final_long = get_longitude_from_deposit()
        # self.current_waste = 0

        return final_lat, final_long, dist / TRUCK_SPEED


    def setupFSMBehaviour(self, proposal, proposal_id): 
        fsm = self.ProposalFSMBehaviour(proposal, proposal_id)
        fsm.add_state(name=TRUCK_STATE_ONE, state=self.ProposalFSMBehaviour.SendProposal(proposal, proposal_id), initial=True)
        fsm.add_state(name=TRUCK_STATE_TWO, state=self.ProposalFSMBehaviour.WaitResponse(proposal, proposal_id))
        fsm.add_transition(source=TRUCK_STATE_ONE, dest=TRUCK_STATE_TWO)
        fsm.add_transition(source=TRUCK_STATE_TWO, dest=TRUCK_STATE_TWO)

        fsm_template = Template()
        fsm_template.set_metadata("performative", "cfp")

        return fsm, ~fsm_template # FSM behaviour will only deal with non cfp messages

    async def setup(self):
        cfp_template = Template()
        cfp_template.set_metadata("performative", "cfp")
        
        receive_cfp_behaviour = self.ReceiveCFPBehaviour(period=RECEIVE_CFP_PERIOD)  # Check for CFP every 1 seconds
        self.add_behaviour(receive_cfp_behaviour, template=cfp_template) # force first behaviour to only read cfp messages, and ignore all others -> or else it would eat all messages that were supposed to go to the state machine behaviours!

        perform_queue = self.PerformQueueBehaviour()
        self.add_behaviour(perform_queue)

        process_proposals = self.ProcessProposalsBehaviour()
        self.add_behaviour(process_proposals)

    async def finish(self, proposal_id):
        print(f"TRUCK_{self.id}: Finished FSM for proposal {proposal_id}")
        # any other cleanup code needd?

async def main():
    truck_agent = TruckAgent("agente2@localhost", SPADE_PASS, 41.1693, -8.6026, TRUCK_CAPACITY)
    await truck_agent.start(auto_register=True)

    await spade.wait_until_finished(truck_agent)
    await truck_agent.stop()
    print("Agent finished")

if __name__ == "__main__":
    spade.run(main())