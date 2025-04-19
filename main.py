import datetime
import sys
import spade
from spade.agent import Agent
import asyncio
import os
from dotenv import load_dotenv
import truck as truck
import bin as bin
import pandas as pd
from utils import *
import world as world
load_dotenv()
SPADE_PASS = os.getenv('SPADE_PASS')

def display_stats(bin_stats, truck_stats, total_waste_collected):
    print("🚮 Bin Stats:")
    for bin_id, stats in bin_stats.items():
        print(f"  Bin {bin_id}:")
        print(f"    - Total Time Full:       {stats[0]}")
        print(f"    - Total Waste Overflow:  {stats[1]}")
        print(f"    - Average Waste:         {stats[2]:.2f}")

    print("\n🚚 Truck Stats:")
    for truck_id, stats in truck_stats.items():
        print(f"  Truck {truck_id}:")
        print(f"    - Total Distance Traveled: {stats:.2f}")

    print("\n Total Waste Collected ----> " + str(total_waste_collected))

async def main():

    if SPADE_PASS is None:
        raise ValueError("Missing SPADE_PASS in environment. Check .env file or environment variables.")
    
    # Provided by the user via terminal: num_trucks, bin_filling_rate_time, bin_filling_rate_quantity
    if len(sys.argv) == 4:
        num_trucks = int(sys.argv[1])
        bin_filling_rate_time = int(sys.argv[2])
        bin_filling_rate_quantity = int(sys.argv[3])
    else:
        num_trucks = int(input("Enter the number of trucks: "))
        bin_filling_rate_time = int(input("Enter bin filling rate time interval: "))
        bin_filling_rate_quantity = int(input("Enter bin filling rate quantity: "))

    # read information from dataset
    df = pd.read_csv("dataset/dataset.csv") #  -> dataset completo
    #df = pd.read_csv("dataset/1bin.csv")

    # base port
    port = 10000

    agent_list = []

    # Register and start world agent to collect statistics
    world_agent = world.WorldAgent("world@localhost", SPADE_PASS)
    await world_agent.start(auto_register=True)
    port_str = str(port)
    world_agent.web.start(hostname="127.0.0.1", port=port_str)
    port += 1
    agent_list.append(world_agent)


    # trash per second
    bin_filling = bin_filling_rate_quantity / bin_filling_rate_time
    print(f"Bin filling rate = {str(bin_filling_rate_quantity)} trash every {str(bin_filling_rate_time)} second(s)")
    # contact list for bin -> it receives the address
    contact_list = []
    deposito_row = df.iloc[-1]
    deposito_lat = deposito_row['Latitude']
    deposito_long = deposito_row['Longitude']
    for i in range(num_trucks):
        truck_jid = "agente" + str(i) + "@localhost"
        # update the contact_list
        contact_list.append(truck_jid)
        print("Creating truck agent with JID: " + truck_jid)
        # jid, password, latitude, longitude, capacity
        truck_agent = truck.TruckAgent(truck_jid, SPADE_PASS, deposito_lat, deposito_long)
        await truck_agent.start(auto_register=True)
        agent_list.append(truck_agent)
        port_str = str(port)
        truck_agent.web.start(hostname="127.0.0.1", port=port_str)
        port += 1

    for _, row in df[:-1].iterrows():
        jid = "agente" + row['ID'] + "@localhost"
        print("Creating bin agent with JID: " + jid)
        iD = row['ID']
        latitude = row['Latitude']
        longitude = row['Longitude']
        fsmagent = bin.BinAgent(jid, SPADE_PASS, iD, contact_list, latitude, longitude, bin_filling_rate_time, bin_filling_rate_quantity)
        await fsmagent.start(auto_register=True)
        agent_list.append(fsmagent)
        port_str = str(port)
        fsmagent.web.start(hostname="127.0.0.1", port=port_str)
        port += 1
    
    sim_duration = 30 # seconds
    await asyncio.sleep(sim_duration)

    # Gracefully stop all agents
    for agent in agent_list:
        for behaviour in agent.behaviours:
            behaviour.kill()
        await agent.stop()
        print(f"{agent.name} has been stopped.")


    display_stats(world_agent.bin_stats, world_agent.truck_stats, world_agent.total_waste_collected)

    world_agent.stop_requested = True
    await asyncio.sleep(1)

    
        
if __name__ == "__main__":
    spade.run(main())