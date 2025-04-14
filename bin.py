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

TRUCKS_NUMBER = 1

#NOTA: usar um behaviour ciclico para verificar estado do caixote, e acrescentar separado um behaviour de FSM quando está cheio não funciona!!
# se respostas do contrato demorarem muito, esse behaviour cíclico spawna várias instâncias do behaviour de contrato em vez de esperar que um FSM termine o contrato

#bin_fullness_threshold = 80 # after this value, the bin sends contract to trucks

class BinAgent(Agent):

    def __init__(self, jid, password, id, max_capacity, known_trucks, latitude, longitude):
        super().__init__(jid, password)
        # shared state between behaviours

        self.id = id
        self.latitude = latitude
        self.longitude = longitude
        self.bin_fullness = 0
        self.truck_responses = {}
        self.max_capacity = max_capacity
        self.threshold_ratio = 0.8 # 80% of the bin max capacity
        self.known_trucks = known_trucks # list of known trucks

        self.time_full_start = None # moment it started to be full
        self.total_time_full = 0 # total time the bin has been full
        self.total_overflow = 0 # total waste above capacity
        self.last_overflow = 0 # last waste above capacity
        self.waste_level = []

    # progressively fill bin with garbage
    class FillBinBehaviour(PeriodicBehaviour):
        async def run(self):
            self.agent.bin_fullness += 100
            print(f"BIN: Bin fullness: {self.agent.bin_fullness}")

            # Check if it is above capacity.
            if self.agent.bin_fullness > self.agent.max_capacity:
                # Start counting time if not already counting
                if self.agent.time_full_start is None:
                    self.agent.time_full_start = self.agent.time

                current_overflow = self.agent.bin_fullness - self.agent.max_capacity
                diff = current_overflow - self.agent.last_overflow
                self.agent.total_overflow += diff
                self.agent.last_overflow = current_overflow
            else:
                # If it was counting and is no longer full, stop the timer
                if self.agent.time_full_start is not None:  
                    duration = self.agent.time - self.agent.time_full_start
                    self.agent.total_time_full += duration
                    self.agent.time_full_start = None

    class UpdateTimeBehaviour(PeriodicBehaviour):
        async def run(self):
            self.agent.time += 1 # increment time every second

    class SendCapacityUpdateBehaviour(PeriodicBehaviour):
        async def run(self):
            for truck_jid in self.agent.known_trucks:
                msg = Message(to=truck_jid)
                msg.set_metadata("performative", "inform")
                msg.set_metadata("type", "bin_status_update")
                msg.body = f"{self.agent.jid};{self.agent.bin_fullness};{self.agent.max_capacity};{self.agent.latitude};{self.agent.longitude}"
                await self.send(msg)
                #print(f"BIN: Sent capacity update to {truck_jid} -> {msg.body}")
        
    class BinFSMBehaviour(FSMBehaviour):
        async def on_start(self):
            print(f"BIN: Bin starting at {self.current_state}")

        async def on_end(self):
            print(f"BIN: Bin finished contract at {self.current_state}")

    #checks how full the bin is, and starts contract if necessary
    class checkBin(State):
        async def run(self):
            #print("Entering state 1 bin")
            if (self.agent.bin_fullness > self.agent.max_capacity * self.agent.threshold_ratio):
                print("BIN: Bin full, sending contract to trucks")
                self.set_next_state(BIN_STATE_TWO)
            else:
                #pass
                print("BIN: Bin not full, repeating check in next iteration")
                await asyncio.sleep(1) # only check every 1 seconds
                self.set_next_state(BIN_STATE_ONE)

    # send contract to trucks, and wait for their responses, until all response/timeout
    class sendContractWaitResponses(State):
        async def run(self):
            print("Entering state 2 bin")
            truck_agents = ["agente2@localhost"]
            # send contract to all trucks
            for truck in truck_agents:
                msg = Message(to=truck)
                msg.set_metadata("performative", "cfp")
                msg.body = f"{self.agent.id};{self.agent.bin_fullness};{self.agent.latitude};{self.agent.longitude}"
                await self.send(msg)
                print("BIN: Sent contract cfp to truck", truck)

            #wait for responses
            self.truck_answers = 0
            self.agent.truck_responses = {}
            reply_msg = await self.receive(timeout=1)
            while (self.truck_answers < TRUCKS_NUMBER):
                if reply_msg:
                    self.truck_answers += 1
                    print(f"BIN: Waiting for truck responses... {self.truck_answers}/{TRUCKS_NUMBER}")
                    if reply_msg.metadata["performative"] == "propose":
                        self.agent.truck_responses[reply_msg.sender] = reply_msg.body
                        print(f"BIN: Truck {reply_msg.sender} proposed {reply_msg.body}")

                    elif reply_msg.metadata["performative"] == "refuse":
                        print(f"BIN: Truck {reply_msg.sender} refused the contract")
                    reply_msg = await self.receive(timeout=3) # wait for more answers
                else:
                    print("BIN: Timeout waiting for truck responses")
                    break
                
            print("BIN: current contract state: ", self.agent.truck_responses)
            # if got answer from at least one truck go to next state of contract
            if (len(self.agent.truck_responses) > 0):
                print("BIN: All trucks answered contract, going to next phase")
                self.set_next_state(BIN_STATE_THREE)

            # if no truck answered (instant timeout), restart contract
            else:
                print("BIN: restarting contract")
                await asyncio.sleep(3) # wait a bit before sending contract again
                self.set_next_state(BIN_STATE_TWO)

    class proposalSelection(State):
        async def run(self):
            print(f"BIN: Selecting best truck for the job among {self.agent.truck_responses}")
            best_truck = min(self.agent.truck_responses, key=self.agent.truck_responses.get) # gets the key with the minimum value
            print("BIN: Selected truck: ", best_truck)

            # send accept proposal to best truck, and reject to others
            for truck, response in self.agent.truck_responses.items():
                #msg = Message(to=truck.jid)
                msg = Message(to=str(truck))
                if (truck == best_truck):
                    print(f"BIN: Sending accept-proposal to truck {truck}")
                    msg.set_metadata("performative", "accept-proposal")
                    await_time = float(response) + 0.2
                else:
                    print(f"BIN: Sending reject-proposal to truck {truck}")
                    msg.set_metadata("performative", "reject-proposal")
                msg.body = f"{self.agent.id};{response}" # need to remind the truck of the proposal ?
                await self.send(msg)
            
            print(f"Waiting {await_time} seconds")
            result_reply = await self.receive(timeout=await_time)

            if (not result_reply):
                print("BIN: Timeout waiting for truck results")
                self.set_next_state(BIN_STATE_TWO)
                return
            
            # if bin successfully cleaned, reset trucks and bin fullness, and go back to state one
            if result_reply.metadata["performative"] == "inform-done":
                print(f"BIN: Received success result from truck {result_reply.sender} with content {result_reply.body}")

                # Stop the timer if it is still running
                if self.agent.time_full_start is not None:
                    duration = self.agent.time - self.agent.time_full_start
                    self.agent.total_time_full += duration
                    self.agent.time_full_start = None

                self.agent_truck_responses = {} # reset trucks
                self.agent.waste_level.append(self.agent.bin_fullness)
                self.agent.bin_fullness = 0 # reset bin fullness
                self.agent.last_overflow = 0 # reset last overflow
                self.set_next_state(BIN_STATE_ONE) # transitions again to state one
            
            # if truck failed, ignore it in the next iteration, or go back to state two if all trucks failed
            elif result_reply.metadata["performative"] == "failure":
                print(f"BIN: Received failure result from truck {result_reply.sender}")
                self.agent_truck_responses.pop(result_reply.sender) # ignore this truck in the next iteration
                if (self.agent_truck_responses == {}):
                    self.set_next_state(BIN_STATE_TWO) # if all trucks failed, go back to sending contract and waiting for responses

    async def setup(self):
        binFill = self.FillBinBehaviour(period=1) # every 1 seconds, fill the bin with garbage
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

TRUCK_STATE_ONE = "RECEIVE_CFP"
TRUCK_STATE_TWO = "PERFORM_ACTION"

#NOTA: Truck tem dois behaviours:
# - um para receber pedidos CFP e colocar numa queue
# - outro para FSM, que processa os pedidos CFP da queue sequencialmente.
# - Faz sentido ter um CFP a processar pedidos sequencialmente? Acho que não é possível satisfazer mais do que um pedido ao mesmo tempo, um camião ou vai a um sitio ou vai a outro, por isso paralelizar não importa, certo?

 
async def main():
    truck_agent = truck.TruckAgent("agente2@localhost", SPADE_PASS, 41.1693, -8.6026, 2000)
    await truck_agent.start()
    truck_agent.web.start(hostname="127.0.0.1", port="10001")
    await asyncio.sleep(3)

    fsmagent = BinAgent("agente1@localhost", SPADE_PASS, "A", 500, ["agente2@localhost"], 40.0, -8.0)
    await fsmagent.start(auto_register=True)
    fsmagent.web.start(hostname="127.0.0.1", port="10000")

    sim_duration = 30 # seconds
    await asyncio.sleep(sim_duration)

    # If the simulation ends while the bin is still full, the time_full_start is still running 
    if fsmagent.time_full_start is not None:
        duration = fsmagent.time - fsmagent.time_full_start
        fsmagent.total_time_full += duration
        fsmagent.time_full_start = None

    print("\n--- BIN METRICS ---")
    print(f"Total time bin was full: {fsmagent.total_time_full:.2f} seconds")
    print(f"Total overflow accumulated: {fsmagent.total_overflow:.2f} units")

    await spade.wait_until_finished(truck_agent)
    await spade.wait_until_finished(fsmagent)
    await fsmagent.stop()
    print("BIN: Agent finished")

    await truck_agent.stop()
    print("TRUCK: Agent finished")

if __name__ == "__main__":
    spade.run(main())
