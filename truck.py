import os
from spade.template import Template
import spade
from spade.agent import Agent
from spade.behaviour import FSMBehaviour, State, CyclicBehaviour, PeriodicBehaviour
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

TRUCK_CAPACITY = 80
TRUCK_SPEED = 0.005  # kilometers per second

MAX_SCHEDULED_TASKS = 10

RECEIVE_CFP_PERIOD = 0.5  # seconds
ACCEPT_PROPOSAL_TIMEOUT = 2  # time to wait for a response from the bin before accepting the proposal, or assume rejected

class TruckAgent(Agent):
    
    def __init__(self, jid, password, latitude, longitude):
        super().__init__(jid, password)

        # shared state between behaviours
        self.id = jid.split("@")[0].split("agente")[1]  # extract the agent ID from the JID, for easier debug
        self.lat = latitude
        self.long = longitude
        self.capacity = TRUCK_CAPACITY
        self.current_waste = 0
        self.bins_stats = {} # {bin_id: [capacity, lat, long, distance]}
        self.proposals = []  # store CFP proposals from bins
        self.schedule = [] # pickup schedule (either pending or confirmed)

        #global stats
        self.total_waste_collected = 0
        self.distance_traveled = 0

    class SendUpdateBehaviour(PeriodicBehaviour):

        async def run(self):
            msg = Message(to="world@localhost")
            msg.set_metadata("performative", "inform")
            msg.set_metadata("type", "truck_status_update")
            msg.body = f"{self.agent.jid};{self.agent.distance_traveled};{self.agent.total_waste_collected}"
            await self.send(msg)
            print(f"\033[92mTruck: Sent capacity update to world -> {msg.body}\033[0m")

    # periodic behaviour to receive CFP proposals from bins, only responsible for receiving messages
    class ReceiveCFPBehaviour(PeriodicBehaviour):
        async def run(self):
            print(f"TRUCK_{self.agent.id}: waiting for CFP proposals...")
            msg = await self.receive(timeout=1)  # timeout after 1 second
            if msg:
                if msg.metadata["performative"] == "cfp":
                    print(f"TRUCK_{self.agent.id}: CFP from {msg.sender}")
                    bin_id, bin_capacity, bin_latitute, bin_longitude = msg.body.strip().split(";")
                    self.agent.bins_stats[bin_id] = {
                        "capacity": float(bin_capacity),  # bin cleaning amount required
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
                template.set_metadata("proposal_id", proposal_id)  # add proposal id to message, to properly filter between different proposals of same bin
                final_template = fsm_template & template # final template only allows messages from the bin that sent the proposal, and non cfp messages (cause those should be caught by the receiveCFPBehaviour)
                self.agent.add_behaviour(fsm, final_template)

                print(f"TRUCK_{self.agent.id}: Started FSM for proposal {proposal_id} from {proposal.sender}")
            else:
                await asyncio.sleep(0.5)  # instead of running constanly

    class PerformQueueBehaviour(CyclicBehaviour):
        async def run(self):
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

                        self.agent.update_queue_task_finish(schedule_entry["proposal_id"]) # remove task from schedule, since it was completed

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
                time_to_bin, available_space_for_task, schedule_entry = self.agent.calculate_stats_to_bin(bin_latitute, bin_longitude, bin_occupation)

                # add additional info to schedule entry
                schedule_entry.update({
                    "proposal_id": self.proposal_id, # to track proposal
                    "bin_id": bin_id,
                    "bin_jid": str(self.proposal.sender), # to respond with success/failure of task to sender bin
                    "state": "pending", # to track state of the task, only "confirmed" tasks are actually performed
                })

                self.agent.schedule.append(schedule_entry) # add to schedule

                #build proposal message
                reply = Message(to=str(self.proposal.sender))
                reply.set_metadata("performative", "propose")
                reply.set_metadata("proposal_id", self.proposal_id)
                reply.body = f"{time_to_bin};{available_space_for_task}" 

                await self.send(reply)

                print(f"TRUCK_{self.agent.id}: Sent proposal {self.proposal_id} to {self.proposal.sender} with values time:{time_to_bin} aka from prev point({schedule_entry['last_bin_to_new_bin_time']}),{available_space_for_task}")
                self.set_next_state(TRUCK_STATE_TWO)

        # State 2: perform action
        class WaitResponse(State):
            def __init__(self, proposal=None, proposal_id=None):
                super().__init__()

                self.proposal = proposal
                self.proposal_id = proposal_id
        
            async def run(self):
                print(f"TRUCK_{self.agent.id}: Waiting for response on proposal {self.proposal_id}")
                msg = await self.receive(timeout=ACCEPT_PROPOSAL_TIMEOUT)  # wait for a message for some seconds -> longer than the bin's timeout on other proposals !
                if (not msg):
                    print(f"TRUCK_{self.agent.id}: No response for proposal {self.proposal_id}")

                    # end behaviour
                    self.agent.update_queue_task_cancel(self.proposal_id) # remove task from schedule, since no response was received
                    print(f"TRUCK_{self.agent.id}: Finished FSM for proposal {self.proposal_id}")

                    return

                if msg.metadata["performative"] == "reject-proposal":
                    print(f"TRUCK_{self.agent.id}: Proposal {self.proposal_id} rejected by {msg.sender}")

                    # end behaviour
                    self.agent.update_queue_task_cancel(self.proposal_id) # remove task from schedule, since it was rejected
                    print(f"TRUCK_{self.agent.id}: Finished FSM for proposal {self.proposal_id}")

                    return

                elif (msg.metadata["performative"] == "accept-proposal"):

                    bin_id = self.proposal.body.split(";")[0]
                    #bin sends back id, agreed time to reach (sent previously in truck's proposal), and waste to collect (can be less)
                    bin_id, _, waste = msg.body.strip().split(";")
                    waste = float(waste) # waste to be collected from bin

                    for i, entry in enumerate(self.agent.schedule):
                        if entry["proposal_id"] == self.proposal_id:

                            #check if bin is actually expecting less waste cleaned than proposed
                            waste_diff = self.agent.schedule[i]["waste"] - waste
                            if (waste_diff > 1.0): # 0 wasnt working with float comparison, so using 1.0 as threshold
                                print(f"TRUCK_{self.agent.id}: Proposal {self.proposal_id} accepted by {msg.sender}, but with less waste to collect: ([{waste}] vs {self.agent.schedule[i]['waste']})")
                                self.agent.schedule[i]["waste"] = waste
                                self.agent.update_queue_task_waste(self.proposal_id, waste_diff, start_index=i) # update subsequent tasks in queue to have more available space for the new task
                            
                            # update state of the task to confirmed, and performable by PerformQueueBehaviour
                            self.agent.schedule[i]["state"] = "confirmed" 
                            
                            break

                    print(f"TRUCK_{self.agent.id}: Proposal {self.proposal_id} accepted by {msg.sender}")
                        
                else:
                    print(f"TRUCK_{self.agent.id}: Unexpected message for proposal {self.proposal_id}: {msg.metadata['performative']}")
                    self.set_next_state(TRUCK_STATE_TWO) # repeat state, waiting for answer
    
    def calculate_stats_to_bin(self, bin_lat, bin_long, bin_waste):
        went_to_deposit = False

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
        
        # if truck is full, must go to deposit to perform this task
        if (last_occupied_capacity == self.capacity): 
            last_lat, last_long, time_elapsed = self.go_to_deposit(last_lat, last_long) # go to deposit if truck is full
            last_occupied_capacity = 0 # reset available capacity to full
            last_time += time_elapsed # update last time to include time to go to deposit
            went_to_deposit = True

        dist_from_prev_to_new_bin = haversine(bin_lat, bin_long, last_lat, last_long) # distance from last task to bin
        time_to_new_bin = dist_from_prev_to_new_bin / TRUCK_SPEED
        start_time = last_time # time this task will start, considering last task end time
        final_time = last_time + time_to_new_bin # time bin is reached, considering last task end time
        final_occupied_capacity = min(self.capacity, last_occupied_capacity + bin_waste) # capacity to reach bin, considering last task end capacity
        used_capacity = min(bin_waste, self.capacity - last_occupied_capacity) # waste to be collected from bin in the new task, considering last task end capacity
        
        # print(f"TRUCK_{self.id}: Calculated stats to bin {bin_lat}, {bin_long} with waste {bin_waste}:")
        print(f"TRUCK_{self.id}: waste to be collected from bin: {used_capacity}, final occupied_space after task: {final_occupied_capacity}/{self.capacity}")
    
        schedule_entry = {
            "start_time": start_time,
            "end_time": final_time,
            "end_latitude": bin_lat,
            "end_longitude": bin_long,
            "end_occupied_capacity": final_occupied_capacity,
            "waste": used_capacity, # waste to be collected from bin
            "last_bin_to_new_bin_distance": dist_from_prev_to_new_bin,
            "last_bin_to_new_bin_time": time_to_new_bin,
            "deposit": went_to_deposit # if the truck had to go to deposit before going to the bin, relevant for fixing schedule when cancelling tasks or updating waste
        }
        
        return final_time, used_capacity, schedule_entry # return time to bin, available space for task, and schedule entry with all the info needed to track the task
    
    def update_queue_task_cancel(self, proposal_id):

        # find the cancelled task index
        cancelled_task = None
        cancelled_index = -1
        for i, entry in enumerate(self.schedule):
            if entry["proposal_id"] == proposal_id:
                cancelled_task = entry
                cancelled_index = i
                break
        
        # security check for the task existence
        if cancelled_task is None:
            print(f"TRUCK_{self.id}: Proposal {proposal_id} not found in schedule")
            return

        # remove the cancelled task
        self.schedule.pop(cancelled_index)
        print(f"TRUCK_{self.id}: Cancelled task for proposal {proposal_id}, removed from schedule")

        #  since this task was cancelled, update all subsequent tasks in the queue values: time and waste, since this task was cancelled
        if not self.schedule or cancelled_index >= len(self.schedule):
            print(f"TRUCK_{self.id}: No tasks to update after cancellation of {proposal_id}")
            return
        
        if cancelled_index > 0:
            prev_task = self.schedule[cancelled_index - 1]
            prev_lat = prev_task["end_latitude"]
            prev_long = prev_task["end_longitude"]
            prev_time = prev_task["end_time"]
        else:
            prev_lat = self.lat
            prev_long = self.long
            prev_time = asyncio.get_event_loop().time()
            
        # calculate new timing for the first task after the cancelled one
        first_task_after = self.schedule[cancelled_index] # task after the cancelled one is now on the same position as the cancelled one
        dist_to_first = haversine(first_task_after["end_latitude"], first_task_after["end_longitude"], prev_lat, prev_long)
        new_time_to_first = dist_to_first / TRUCK_SPEED
        time_saved = cancelled_task["last_bin_to_new_bin_time"] - new_time_to_first

        # update the first task's timing, considering it will now go directly from the previous task to the first task after the cancelled one
        first_task_after["start_time"] = prev_time
        first_task_after["end_time"] = prev_time + new_time_to_first
        first_task_after["last_bin_to_new_bin_distance"] = dist_to_first
        first_task_after["last_bin_to_new_bin_time"] = new_time_to_first

        # update subsequent tasks' timing, based on saved time from going directly to the first task after the cancelled one
        for i in range(cancelled_index + 1, len(self.schedule)):
            self.schedule[i]["start_time"] -= time_saved
            self.schedule[i]["end_time"] -= time_saved

        # Adjust occupied capacity for subsequent tasks (starting from next task), consdering the cancelled task's waste, and potential saved landfill trips
        self.update_queue_task_waste(self.schedule[cancelled_index]["proposal_id"], cancelled_task["waste"], start_index=cancelled_index) # update subsequent tasks in queue to have more available space for the new task

        print(f"TRUCK_{self.id}: Updated schedule after cancelling {proposal_id}: {len(self.schedule)} tasks remaining")

    # receives:
    # - proposal_id: id of the task from which to start the update
    # - waste_diff: amount of waste reduced from what was previously proposed
    # - start_index: index of the task from which to start the update (if not provided, will search for it)
    def update_queue_task_waste(self, proposal_id, waste_diff, start_index=-1):
        
        if (start_index == -1):
            for i, entry in enumerate(self.schedule):
                if entry["proposal_id"] == proposal_id:
                    start_index = i
                    break
        
        i = start_index

        if (i > 0):
            prev_task = self.schedule[i - 1]
            prev_lat = prev_task["end_latitude"]
            prev_long = prev_task["end_longitude"]
            prev_time = prev_task["end_time"]
        else:
            prev_lat = self.lat
            prev_long = self.long
            prev_time = asyncio.get_event_loop().time()
  
        while i < len(self.schedule):
            task = self.schedule[i]
            # Get previous capacity (from previous task or current waste if last task is first task)
            prev_occupied_capacity = self.schedule[i - 1]["end_occupied_capacity"] if i > start_index else self.current_waste # previous task's occupied capacity
            curr_occupied_capacity_before_update = task["end_occupied_capacity"] # current task's occupied capacity before doing update

            # if task doesnt' require landfill, reduce occupied capacity
            if not task["deposit"]:
                task["end_occupied_capacity"] = max(0, task["end_occupied_capacity"] - waste_diff)
                print(f"TRUCK_{self.id}: Task {task['proposal_id']} updated: end_occupied_capacity={task['end_occupied_capacity']} (no landfill required)")
                prev_lat = task["end_latitude"]
                prev_long = task["end_longitude"]
                prev_time = task["end_time"]
                i += 1
            else:
                # Task originally required landfill, check if it can be done before landfill
                if self.capacity - prev_occupied_capacity >= task["waste"]:
                    # Calculat two scenarios:
                    # Option 1: From previous bin (A), go to bin (B) first, then landfill, then next bin (C)
                    # go bin(A) -> bin(B)
                    time_to_bin = haversine(prev_lat, prev_long, task["end_latitude"], task["end_longitude"]) / TRUCK_SPEED
                    # go bin(B) -> landfill
                    dist_to_landfill = get_distance_truck_to_deposit(task["end_latitude"], task["end_longitude"])
                    time_to_landfill_after_bin = dist_to_landfill / TRUCK_SPEED
                    time_option1 = time_to_bin + time_to_landfill_after_bin
                    # landfill -> bin (C) if C exists
                    if i + 1 < len(self.schedule):
                        next_task = self.schedule[i + 1]
                        _, _, time_to_next = self.go_to_deposit(next_task["end_latitude"], next_task["end_longitude"])
                        time_option1 += time_to_next
                    # if bin (C) does not exist, no need to account for next bin
                    else:
                        time_to_next = 0

                    # Option 2: From previous bin (A), go to landfill first, then bin (B), then next bin (C)
                    # bin(A) -> landfill
                    _, _, time_to_landfill = self.go_to_deposit(prev_lat, prev_long)
                    
                    # landfill -> bin(B)
                    _, _, time_from_landfill_to_bin = self.go_to_deposit(task["end_latitude"], task["end_longitude"])
                    
                    time_option2 = time_to_landfill + time_from_landfill_to_bin

                    # bin (B) -> bin (C), if C exists
                    if i + 1 < len(self.schedule):
                        next_task = self.schedule[i + 1]
                        dist_to_next = haversine(task["end_latitude"], task["end_longitude"], next_task["end_latitude"], next_task["end_longitude"])
                        time_to_next_option2 = dist_to_next / TRUCK_SPEED
                        time_option2 += time_to_next_option2

                    # if bin (C) does not exist, no need to account for next bin
                    else:
                        time_to_next_option2 = 0

                    # Choose the optimal option
                    if time_option1 <= time_option2:
                        # Perform task before landfill
                        task["deposit"] = False
                        task["end_occupied_capacity"] = prev_occupied_capacity + task["waste"]
                        task["start_time"] = prev_time
                        task["end_time"] = prev_time + time_to_bin
                        task["last_bin_to_new_bin_distance"] = haversine(prev_lat, prev_long, task["end_latitude"], task["end_longitude"])
                        task["last_bin_to_new_bin_time"] = time_to_bin
                        print(f"TRUCK_{self.id}: Task {task['proposal_id']} updated: end_occupied_capacity=[{task['end_occupied_capacity']}] vs [{curr_occupied_capacity_before_update} with deposit: true], deposit=False, end_time={task['end_time']} (performed before landfill, time_option1={time_option1:.2f} vs time_option2={time_option2:.2f})")

                        # Adjust next task's timing for landfill trip if it exists
                        if i + 1 < len(self.schedule):
                            next_task = self.schedule[i + 1]
                            _,_, time_to_landfill = self.go_to_deposit(task["end_latitude"], task["end_longitude"])
                            next_task["start_time"] = task["end_time"]
                            next_task["end_time"] = task["end_time"] + next_task["last_bin_to_new_bin_time"]
                            next_task["deposit"] = True
                            print(f"TRUCK_{self.id}: Next task {next_task['proposal_id']} updated: start_time={next_task['start_time']}, end_time={next_task['end_time']}, deposit=True (landfill after task {task['proposal_id']})")
                        prev_time = task["end_time"]
                        prev_lat = task["end_latitude"]
                        prev_long = task["end_longitude"]
                        i += 1
                    else:
                        # Keep landfill trip, stop adjusting
                        print(f"TRUCK_{self.id}: Task {task['proposal_id']} still requires landfill (time_option1={time_option1:.2f} vs time_option2={time_option2:.2f}), no further waste adjustments")
                        break
                else:
                    # Cannot perform task before landfill, stop adjusting
                    print(f"TRUCK_{self.id}: Task {task['proposal_id']} still requires landfill due to insufficient capacity, no further waste adjustments")
                    break

    def update_queue_task_finish(self, proposal_id):
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
        final_lat = get_latitude_from_deposit()
        final_long = get_longitude_from_deposit()

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

        sendUpdate = self.SendUpdateBehaviour(period=5)  # every 5 seconds, send update to world
        self.add_behaviour(sendUpdate)
    
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
