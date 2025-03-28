import spade
from spade.agent import Agent
from spade.behaviour import FSMBehaviour, State, PeriodicBehaviour, OneShotBehaviour


class WorldAgent(Agent):

    def __init__(self, jid, password):
        super().__init__(jid, password)
        self.total_waste_collected = 0
        
        self.average_waste_level_bin = 0
        self.time_bin_full = 0
        self.total_waste_outside_bin = 0
        
        self.distance_travelled_truck = 0
        self.trucks_total_distance = 0

    class WorldFSMBehaviour(FSMBehaviour):
        # State 1
        class awaitAnswers(State):
            async def run(self):
            msg = await self.receive(timeout=3)
            if msg

        class printFinalStatistics(State):



        def setupFSMBehaviour():
            fsm = self.WorldFSMBehaviour()
            fsm.add_state(name=WORLD_STATE_ONE, state=self.WorldFSMBehaviour.awaitAnswers(), initial=True)
            fsm.add_state(name=WORLD_STATE_TWO, state=self.WorldFSMBehaviour.printFinalStatistics())
            


async def main():
    world_agent = WorldAgent("worldagente@localhost", input("Password: "))
    await world_agent.start(auto_register=True)

    await spade.wait_until_finished(world_agent)
    await world_agent.stop()
    print("World agent finished")

if __name__ == "__main__":
    spade.run(main())