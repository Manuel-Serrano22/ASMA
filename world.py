import spade
from spade.agent import Agent
from spade.behaviour import FSMBehaviour, State, CyclicBehaviour
import ast


class WorldAgent(Agent):

    def __init__(self, jid, password):
        super().__init__(jid, password)
        self.total_waste_collected = 0
        self.bin_stats = {} # dictionary that holds bin stats: [A]: [total_time_full, total_waste_overflow, average_waste]
        self.truck_stats = {} # dictionary that holds truck stats: [0]: [total_distance_traveled]
        self.stop_requested = False
    
    class StatusReceiver(CyclicBehaviour):

        async def run(self):
            if self.agent.stop_requested:
                print("WorldAgent received stop signal, shutting down behavior.")
                self.kill()  # stop this behaviour
                return

            msg = await self.receive(timeout=2)

            if msg and msg.metadata.get("type") == "bin_status_update":
                bin_id, total_time_full, total_overflow_peaks, waste_level, current_waste = msg.body.strip().split(";")
                total_time_full = float(total_time_full)
                total_overflow_peaks = float(total_overflow_peaks)
                waste_level = ast.literal_eval(waste_level)
                print(f"Waste level is this: {waste_level}")
                print(f"\033[93mWORLD: Received bin update -> {bin_id}: {total_time_full}/{total_overflow_peaks}/{waste_level}\033[0m")
                if not waste_level:
                    average = 0
                else:
                    average = sum(waste_level) / len(waste_level)
                self.agent.bin_stats[bin_id] = [total_time_full, total_overflow_peaks, average, current_waste]
            elif msg and msg.metadata.get("type") == "truck_status_update":
                truck_id, distance_traveled, waste_collected = msg.body.strip().split(";")
                distance_traveled = float(distance_traveled)
                waste_collected = float(waste_collected)
                self.agent.truck_stats[truck_id] = distance_traveled
                self.agent.total_waste_collected += waste_collected
    
    async def setup(self):
        self.add_behaviour(self.StatusReceiver())
            
