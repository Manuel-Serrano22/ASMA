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

bin_fullness_threshold = 80 # after this value, the bin sends contract to trucks

class BinAgent(Agent):

    def __init__(self, jid, password):
        super().__init__(jid, password)
        # shared state between behaviours
        self.bin_fullness = 0
        self.truck_responses = {}

    # progressively fill bin with garbage
    class FillBinBehaviour(PeriodicBehaviour):
        async def run(self):
            self.agent.bin_fullness += 260
            print(f"BIN: Bin fullness: {self.agent.bin_fullness}")
        
    class BinFSMBehaviour(FSMBehaviour):
        async def on_start(self):
            print(f"BIN: Bin starting at {self.current_state}")

        async def on_end(self):
            print(f"BIN: Bin finished contract at {self.current_state}")

    #checks how full the bin is, and starts contract if necessary
    class checkBin(State):
        async def run(self):
            # print("Entering state 1 bin")
            if (self.agent.bin_fullness > bin_fullness_threshold):
                print("BIN: Bin full, sending contract to trucks")
                self.set_next_state(BIN_STATE_TWO)
            else:
                pass
                # print("BIN: Bin not full, repeating check in next iteration")
                # await asyncio.sleep(1) # only check every 1 seconds
                # self.set_next_state(BIN_STATE_ONE)

    # send contract to trucks, and wait for their responses, until all response/timeout
    class sendContractWaitResponses(State):
        async def run(self):
            print("Entering state 2 bin")
            truck_agents = ["agente2@localhost"]
            # send contract to all trucks
            for truck in truck_agents:
                msg = Message(to=truck)
                msg.set_metadata("performative", "cfp")
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
                        self.agent.truck_responses[reply_msg.sender] = int(reply_msg.body)
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
                else:
                    print(f"BIN: Sending reject-proposal to truck {truck}")
                    msg.set_metadata("performative", "reject-proposal")
                msg.body = str(response) # need to remind the truck of the proposal ?
                await self.send(msg)

            result_reply = await self.receive(timeout=1)

            if (not result_reply):
                print("BIN: Timeout waiting for truck results")
                self.set_next_state(BIN_STATE_TWO)
                return
            
            # if bin successfully cleaned, reset trucks and bin fullness, and go back to state one
            if result_reply.metadata["performative"] == "inform-done":
                print(f"BIN: Received success result from truck {result_reply.sender} with content {result_reply.body}")
                self.agent_truck_responses = {} # reset trucks
                self.agent.bin_fullness = 0 # reset bin fullness
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

    # setup the transition and states for the fsm behaviour
    def setupFSMBehaviour(self):
        fsm = self.BinFSMBehaviour()
        fsm.add_state(name=BIN_STATE_ONE, state=self.checkBin(), initial=True)
        fsm.add_state(name=BIN_STATE_TWO, state=self.sendContractWaitResponses())
        fsm.add_state(name=BIN_STATE_THREE, state=self.proposalSelection())

        #NOTE: must register all transitions here, or they dont work when called in run functions of states !!
        # fsm.add_transition(source=BIN_STATE_ONE, dest=BIN_STATE_ONE)
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
    truck_agent = truck.TruckAgent("agente2@localhost", SPADE_PASS)
    await truck_agent.start()
    truck_agent.web.start(hostname="127.0.0.1", port="10001")

    await asyncio.sleep(3)

    fsmagent = BinAgent("agente1@localhost", SPADE_PASS)
    await fsmagent.start(auto_register=True)
    fsmagent.web.start(hostname="127.0.0.1", port="10000")

    await spade.wait_until_finished(truck_agent)
    await spade.wait_until_finished(fsmagent)
    await fsmagent.stop()
    print("BIN: Agent finished")

    await truck_agent.stop()
    print("TRUCK: Agent finished")

if __name__ == "__main__":
    spade.run(main())
