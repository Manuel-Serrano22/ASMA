import spade
from spade.agent import Agent
from spade.behaviour import FSMBehaviour, State, CyclicBehaviour



class WorldAgent(Agent):

    def __init__(self, jid, password):
        super().__init__(jid, password)
        self.total_waste_collected = 0
        self.total_distance = 0

    class BinStatusReceiver(CyclicBehaviour):

        async def run(self):
            msg = await self.receive(timeout=2)

            if msg and msg.metadata.get("type") == "bin_status_update":
                bin_id, total_time_full, total_overflow_peaks, waste_level = msg.body.strip().split(";")
                total_time_full = float(total_time_full)
                total_overflow_peak = float(total_overflow_peaks)
                print(f"\033[93mWORLD: Received bin update -> {bin_id}: {total_time_full}/{total_overflow_peaks}/{waste_level}\033[0m")

    async def setup(self):
        self.add_behaviour(self.BinStatusReceiver())
            
