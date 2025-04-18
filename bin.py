import spade
from spade.agent import Agent
from spade.behaviour import FSMBehaviour, State, PeriodicBehaviour, OneShotBehaviour
import random
from spade.message import Message
import asyncio
import os
from dotenv import load_dotenv
import truck as truck

load_dotenv()
SPADE_PASS = os.getenv('SPADE_PASS')

BIN_STATE_ONE = "CHECK_BIN"
BIN_STATE_TWO = "SEND_CONTRACT_WAIT_RESPONSES"
BIN_STATE_THREE = "PROPOSAL_SELECTION"

BIN_MAX_CAPACITY = 100 # max bin capacity
BIN_THRESHOLD_RATIO = 0.5 # threshold for bin to send cfp contracts
BIN_FILL_RATE = 30
BIN_FILL_SPEED = 1 # every x seconds, fill the bin with garbage

class BinAgent(Agent):

    def __init__(self, jid, password, id, max_capacity, known_trucks, latitude, longitude):
        super().__init__(jid, password)
        # shared state between behaviours

        self.id = id
        self.latitude = latitude
        self.longitude = longitude
        self.bin_fullness = 0
        self.bin_fullness_proposal = 0 # store the bin fullness when sending the proposal
        self.truck_responses = {}
        self.max_capacity = max_capacity
        self.threshold_ratio = BIN_THRESHOLD_RATIO # 80% of the bin max capacity
        self.known_trucks = known_trucks # list of known trucks
        self.time_full_start = None # moment it started to be full
        self.total_time_full = 0 # total time the bin has been full
        self.in_overflow = False
        self.peak_overflow_cycle   = 0.0
        self.total_overflow_peaks  = 0.0
        self.waste_level = []

    # progressively fill bin with garbage
    class FillBinBehaviour(PeriodicBehaviour):
        async def run(self):
            self.agent.bin_fullness += BIN_FILL_RATE
            print(f"BIN_{self.agent.id}: Bin fullness: {self.agent.bin_fullness}")

            excess = self.agent.bin_fullness - self.agent.max_capacity

            if excess > 0:   
                if self.agent.time_full_start is None:
                    self.agent.time_full_start = self.agent.time

                if not self.agent.in_overflow:
                    self.agent.in_overflow = True
                    self.agent.peak_overflow_cycle = excess

                else:
                    if excess > self.agent.peak_overflow_cycle:
                        self.agent.peak_overflow_cycle = excess

            else:
                if self.agent.time_full_start is not None:  
                    duration = self.agent.time - self.agent.time_full_start
                    self.agent.total_time_full += duration
                    self.agent.time_full_start = None

                if self.agent.in_overflow:
                    self.agent.total_overflow_peaks += self.agent.peak_overflow_cycle
                    self.agent.peak_overflow_cycle = 0
                    self.agent.in_overflow = False

    class UpdateTimeBehaviour(PeriodicBehaviour):
        async def run(self):
            self.agent.time += 1 # increment time every second

    class SendCapacityUpdateBehaviour(PeriodicBehaviour):
        async def run(self):
            for truck_jid in self.agent.known_trucks:
                msg = Message(to=truck_jid)
                msg.set_metadata("performative", "inform")
                msg.set_metadata("type", "bin_status_update")
                msg.body = f"{self.agent.id};{self.agent.bin_fullness};{self.agent.max_capacity};{self.agent.latitude};{self.agent.longitude}"
                await self.send(msg)
                #print(f"BIN: Sent capacity update to {truck_jid} -> {msg.body}")
        
    class BinFSMBehaviour(FSMBehaviour):
        async def on_start(self):
            print(f"BIN_{self.agent.id}: Bin starting at {self.current_state}")

        async def on_end(self):
            print(f"BIN_{self.agent.id}: Bin finished contract at {self.current_state}")

    #checks how full the bin is, and starts contract if necessary
    class checkBin(State):
        async def run(self):
            # print("Entering state 1 bin")
            if (self.agent.bin_fullness > self.agent.max_capacity * self.agent.threshold_ratio):
                print(f"BIN_{self.agent.id}: Bin full, sending contract to trucks")
                self.set_next_state(BIN_STATE_TWO)
            else:
                # print(f"BIN_{self.agent.id}: Bin not full, repeating check in next iteration")
                await asyncio.sleep(1) # only check every 1 seconds
                self.set_next_state(BIN_STATE_ONE)

    # send contract to trucks, and wait for their responses, until all response/timeout
    class sendContractWaitResponses(State):
        async def run(self):
            print("Entering state 2 bin")
            # send contract to all trucks
            self.agent.bin_fullness_proposal = self.agent.bin_fullness
            for truck in self.agent.known_trucks:
                msg = Message(to=truck)
                msg.set_metadata("performative", "cfp")
                msg.body = f"{self.agent.id};{self.agent.bin_fullness_proposal};{self.agent.latitude};{self.agent.longitude}"
                await self.send(msg)
                print(f"BIN_{self.agent.id}: Sent contract cfp to truck", truck)

            #wait for responses
            self.truck_answers = 0
            self.agent.truck_responses = {}
            while (self.truck_answers < len(self.agent.known_trucks)):
                print(f"BIN_{self.agent.id}: Waiting for truck responses... {self.truck_answers}/{len(self.agent.known_trucks)}")
                reply_msg = await self.receive(timeout=3) # wait for answers
                if reply_msg:
                    self.truck_answers += 1
                    if reply_msg.metadata["performative"] == "propose":
                        truck_time, truck_capacity = reply_msg.body.strip().split(";")
                        self.agent.truck_responses[reply_msg.sender] = (float(truck_time), float(truck_capacity))
                        print(f"BIN_{self.agent.id}: Truck {reply_msg.sender} proposed: Time -> {truck_time}, Capacity -> {truck_capacity}")

                    elif reply_msg.metadata["performative"] == "refuse":
                        print(f"BIN_{self.agent.id}: Truck {reply_msg.sender} refused the contract")
                else:
                    print(f"BIN_{self.agent.id}: Timeout waiting for truck responses")
                    break
                
            print(f"BIN_{self.agent.id}: current contract state: ", self.agent.truck_responses)
            # if got answer from at least one truck go to next state of contract
            if (len(self.agent.truck_responses) > 0):
                print(f"BIN_{self.agent.id}: All trucks answered contract, going to next phase")
                self.set_next_state(BIN_STATE_THREE)

            # if no truck answered (instant timeout), restart contract
            else:
                print(f"BIN_{self.agent.id}: restarting contract")
                await asyncio.sleep(3) # wait a bit before sending contract again
                self.set_next_state(BIN_STATE_TWO)

    class proposalSelection(State):
        async def run(self):
            print(f"BIN_{self.agent.id}: Selecting best trucks among {self.agent.truck_responses}")
            truck_data = [(str(jid), t[0], t[1]) for jid, t in self.agent.truck_responses.items()]

            selected_trucks = self.agent.select_trucks_min_time(truck_data, self.agent.bin_fullness_proposal)
            print(f"BIN_{self.agent.id}: Selected trucks: {selected_trucks}")

            if not selected_trucks:
                print("BIN: No truck group could collect anything, restarting...")
                self.set_next_state(BIN_STATE_TWO)
                return

            remaining = self.agent.bin_fullness_proposal
            assignment = {}

            for jid in selected_trucks:
                _, cap = self.agent.truck_responses[jid]
                take = min(cap, remaining)
                assignment[jid] = take
                remaining -= take
                if remaining <= 0:
                    break
            
            for truck, (time, _) in self.agent.truck_responses.items():
                msg = Message(to=str(truck))
                if str(truck) in selected_trucks:
                    amount = assignment[truck]
                    print(f"BIN_{self.agent.id}: Sending accept-proposal to truck {truck}")
                    msg.set_metadata("performative", "accept-proposal")
                    msg.body = f"{self.agent.id};{time};{amount}"
                else:
                    print(f"BIN_{self.agent.id}: Sending reject-proposal to truck {truck}")
                    msg.set_metadata("performative", "reject-proposal")
                    msg.body = f"{self.agent.id};{time}"
                await self.send(msg)

            timeout_total = max(self.agent.truck_responses[truck][0] for truck in selected_trucks) + 0.2
            collected_total = 0.0
            responses_expected = len(selected_trucks)
            responses_received = 0
            responses_received_from = set()

            start_time = asyncio.get_event_loop().time()

            while responses_received < responses_expected:
                elapsed = asyncio.get_event_loop().time() - start_time
                remaining = timeout_total - elapsed

                if remaining <= 0:
                    print(f"BIN_{self.agent.id}: Timeout expired while waiting for truck results")
                    break

                result_reply = await self.receive(timeout=remaining)

                if not result_reply:
                    print(f"BIN_{self.agent.id}: No response received during this wait cycle")
                    break

                sender = str(result_reply.sender)
                if sender in responses_received_from:
                    continue

                responses_received += 1
                responses_received_from.add(sender)

                if result_reply.metadata["performative"] == "inform-done":
                    collected = float(result_reply.body)
                    print(f"BIN_{self.agent.id}: Truck {result_reply.sender} collected {collected} from bin")
                    self.agent.bin_fullness -= collected
                    self.agent.bin_fullness = max(0, self.agent.bin_fullness)
                    collected_total += collected

                elif result_reply.metadata["performative"] == "failure":
                    print(f"BIN_{self.agent.id}: Truck {result_reply.sender} failed")

                if responses_received == responses_expected:
                    break

            if responses_received < responses_expected:
                print(f"BIN_{self.agent.id}: Not all trucks responded in time. Retrying...")
                self.set_next_state(BIN_STATE_TWO)
                return
            
            self.agent.waste_level.append(collected_total)

            # Stop the timer if the bin is no longer full
            if self.agent.bin_fullness <= self.agent.max_capacity:
                if self.agent.time_full_start is not None:
                    duration = self.agent.time - self.agent.time_full_start
                    self.agent.total_time_full += duration
                    self.agent.time_full_start = None

                if self.agent.in_overflow:
                    self.agent.total_overflow_peaks += self.agent.peak_overflow_cycle
                    self.agent.peak_overflow_cycle = 0
                    self.agent.in_overflow = False

            self.agent.truck_responses = {} # reset trucks
            self.set_next_state(BIN_STATE_ONE) # transitions again to state one


    async def setup(self):
        binFill = self.FillBinBehaviour(period=BIN_FILL_SPEED) 
        self.add_behaviour(binFill)
        fsm = self.setupFSMBehaviour()
        self.add_behaviour(fsm)
        self.time = 0 # time in seconds, used to calculate how long the bin has been full
        self.add_behaviour(self.UpdateTimeBehaviour(period=1)) # every 1 seconds, update time

    # setup the transition and states for the fsm behaviour
    def setupFSMBehaviour(self):
        fsm = self.BinFSMBehaviour()
        fsm.add_state(name=BIN_STATE_ONE, state=self.checkBin(), initial=True)
        fsm.add_state(name=BIN_STATE_TWO, state=self.sendContractWaitResponses())
        fsm.add_state(name=BIN_STATE_THREE, state=self.proposalSelection())

        #NOTE: must register all transitions here, or they dont work when called in run functions of states !!
        fsm.add_transition(source=BIN_STATE_ONE, dest=BIN_STATE_ONE)
        fsm.add_transition(source=BIN_STATE_ONE, dest=BIN_STATE_TWO)
        fsm.add_transition(source=BIN_STATE_TWO, dest=BIN_STATE_ONE)
        fsm.add_transition(source=BIN_STATE_TWO, dest=BIN_STATE_TWO)
        fsm.add_transition(source=BIN_STATE_TWO, dest=BIN_STATE_THREE)
        fsm.add_transition(source=BIN_STATE_THREE, dest=BIN_STATE_ONE)
        fsm.add_transition(source=BIN_STATE_THREE, dest=BIN_STATE_TWO)
        return fsm
    

    def select_trucks_min_time(self, truck_data, required_capacity):
        factor = 10                              
        req = int(required_capacity * factor)

        ids   = [t[0] for t in truck_data]
        times = [t[1] for t in truck_data]
        caps  = [int(t[2] * factor) for t in truck_data]

        n        = len(truck_data)
        max_cap  = sum(caps)
        INF_TIME = float("inf")

        dp = [[INF_TIME]*(max_cap+1) for _ in range(n+1)]
        dp[0][0] = 0

        for i in range(1, n+1):
            t   = times[i-1]
            cap = caps[i-1]
            for c in range(max_cap+1):
                dp[i][c] = dp[i-1][c]
                if c >= cap and dp[i-1][c-cap] != INF_TIME:
                    cand = max(dp[i-1][c-cap], t)
                    if cand < dp[i][c]:
                        dp[i][c] = cand

        chosen_cap, best_time = None, INF_TIME
        for c in range(req, max_cap+1):
            if dp[n][c] < best_time:
                best_time, chosen_cap = dp[n][c], c

        if chosen_cap is None:
            for c in range(max_cap, -1, -1):
                if dp[n][c] < best_time:
                    best_time, chosen_cap = dp[n][c], c
                    break
            if chosen_cap is None:
                return []

        selected = []
        c = chosen_cap
        for i in range(n, 0, -1):
            t   = times[i-1]
            cap = caps[i-1]
            if c >= cap and dp[i][c] == max(dp[i-1][c-cap], t):
                selected.append(ids[i-1])
                c -= cap
        return selected[::-1]

TRUCK_STATE_ONE = "RECEIVE_CFP"
TRUCK_STATE_TWO = "PERFORM_ACTION"

#NOTA: Truck tem dois behaviours:
# - um para receber pedidos CFP e colocar numa queue
# - outro para FSM, que processa os pedidos CFP da queue sequencialmente.
# - Faz sentido ter um CFP a processar pedidos sequencialmente? Acho que não é possível satisfazer mais do que um pedido ao mesmo tempo, um camião ou vai a um sitio ou vai a outro, por isso paralelizar não importa, certo?

 
async def main():
    truck_agent = truck.TruckAgent("agente4@localhost", SPADE_PASS, 41.1693, -8.6026, 200)
    await truck_agent.start()
    truck_agent.web.start(hostname="127.0.0.1", port="10001")
    await asyncio.sleep(1)

    # truck_agent2 = truck.TruckAgent("agente5@localhost", SPADE_PASS, 41.234, -8.6124, 350)
    # await truck_agent2.start()
    # truck_agent2.web.start(hostname="127.0.0.1", port="10002")
    # await asyncio.sleep(3)

    # truck_agent3 = truck.TruckAgent("agente6@localhost", SPADE_PASS, 41.1493, -8.5826, 100)
    # await truck_agent3.start()
    # truck_agent3.web.start(hostname="127.0.0.1", port="10003")
    # await asyncio.sleep(3)

    fsmagent = BinAgent("agente1@localhost", SPADE_PASS, "A", BIN_MAX_CAPACITY, ["agente4@localhost"], 40.0, -8.0)
    await fsmagent.start(auto_register=True)
    fsmagent.web.start(hostname="127.0.0.1", port="10004")

    fsmagent_2 = BinAgent("agente2@localhost", SPADE_PASS, "B", BIN_MAX_CAPACITY, ["agente4@localhost"], 42.0, -8.0)
    await fsmagent_2.start(auto_register=True)
    fsmagent_2.web.start(hostname="127.0.0.1", port="10005")

    fsmagent_3 = BinAgent("agente3@localhost", SPADE_PASS, "C", BIN_MAX_CAPACITY, ["agente4@localhost"], 43.0, -8.0)
    await fsmagent_3.start(auto_register=True)
    fsmagent_3.web.start(hostname="127.0.0.1", port="10006")

    sim_duration = 30 # seconds
    await asyncio.sleep(sim_duration)

    # If the simulation ends while the bin is still full, the time_full_start is still running 
    if fsmagent.time_full_start is not None:
        duration = fsmagent.time - fsmagent.time_full_start
        fsmagent.total_time_full += duration
        fsmagent.time_full_start = None

    if fsmagent.in_overflow:
        fsmagent.total_overflow_peaks += fsmagent.peak_overflow_cycle
        fsmagent.peak_overflow_cycle = 0
        fsmagent.in_overflow = False

    print("\n--- BIN METRICS ---")
    print(f"Total time bin was full: {fsmagent.total_time_full:.2f} seconds")
    print(f"Total overflow accumulated: {fsmagent.total_overflow_peaks:.2f} units")

    await spade.wait_until_finished(truck_agent)
    # await spade.wait_until_finished(truck_agent2)
    # await spade.wait_until_finished(truck_agent3)

    await spade.wait_until_finished(fsmagent)
    await spade.wait_until_finished(fsmagent_2)
    # await spade.wait_until_finished(fsmagent_3)

    await fsmagent.stop()
    await fsmagent_2.stop()
    # await fsmagent_3.stop()

    print(f"BIN: Bins finished")

    await truck_agent.stop()
    # await truck_agent_2.stop()
    # await truck_agent_3.stop()

    print("TRUCK: Trucks finished")

if __name__ == "__main__":
    spade.run(main())
